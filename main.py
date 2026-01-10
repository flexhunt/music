#!/usr/bin/env python3
# main.py -- OAuth2 Enabled for Cloud IPs

import os
import re
import logging
import asyncio
from typing import List, Dict, Any, Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from ytmusicapi import YTMusic
import yt_dlp
from yt_dlp.utils import DownloadError

# -------------------------
# Config & paths
# -------------------------
DOWNLOADS_DIR = os.environ.get("DOWNLOADS_DIR", "downloads")
CACHE_DIR = os.path.join(DOWNLOADS_DIR, ".cache") # Cache auth token here
os.makedirs(DOWNLOADS_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)

# -------------------------
# Logging
# -------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# -------------------------
# YTMusic init
# -------------------------
try:
    ytmusic = YTMusic()
    logger.info("YTMusic initialized")
except Exception as e:
    logger.error("Failed to initialize YTMusic: %s", e)
    ytmusic = None

# -------------------------
# In-memory user state
# -------------------------
user_data: Dict[int, Dict[str, Any]] = {}

# -------------------------
# Utility helpers
# -------------------------
def escape_html(text: Optional[str]) -> str:
    if not text:
        return ""
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

def format_duration(duration_str: Optional[str]) -> str:
    return duration_str or "Unknown"

def get_thumbnail(thumbnails: List[Dict[str, Any]]) -> str:
    if thumbnails and len(thumbnails) > 0:
        return thumbnails[-1].get("url", "")
    return ""

def sanitize_filename(title: str, max_len: int = 40) -> str:
    s = re.sub(r'[^A-Za-z0-9 _-]', '_', title)
    s = re.sub(r'\s+', ' ', s).strip()
    return s[:max_len]

# -------------------------
# Format choosing logic
# -------------------------
def choose_format_from_formats(formats: List[Dict[str, Any]]) -> str:
    if not formats: return 'bestaudio/best'
    exts = {f.get('ext') for f in formats if f.get('ext')}
    acodes = {f.get('acodec') for f in formats if f.get('acodec')}
    if 'm4a' in exts: return 'bestaudio[ext=m4a]/bestaudio/best'
    if 'webm' in exts: return 'bestaudio[ext=webm]/bestaudio/best'
    for codec in ('opus', 'aac', 'mp3'):
        if codec in acodes: return f"bestaudio[acodec={codec}]/bestaudio/best"
    return 'bestaudio/best'

# -------------------------
# Bot handlers
# -------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data[user_id] = {"results": [], "index": 0}
    await update.message.reply_text("OAuth2 Bot Ready 🎵\nSend me a song name.")

async def search_song(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    query = update.message.text
    if ytmusic is None:
        await update.message.reply_text("Service unavailable.")
        return

    await update.message.reply_text(f"Searching: {query}...")
    try:
        search_results = ytmusic.search(query, filter="songs", limit=5)
        if not search_results:
            await update.message.reply_text("No results.")
            return

        results = []
        for item in search_results[:5]:
            results.append({
                "title": item.get("title", "Unknown"),
                "artist": ", ".join([a.get("name", "") for a in item.get("artists", [])]) or "Unknown",
                "album": item.get("album", {}).get("name", "N/A") if item.get("album") else "N/A",
                "duration": format_duration(item.get("duration", "Unknown")),
                "thumbnail": get_thumbnail(item.get("thumbnails", [])),
                "videoId": item.get("videoId", "")
            })

        user_data[user_id] = {"results": results, "index": 0}
        await send_result(update, context, user_id)
    except Exception as e:
        logger.exception("Search error: %s", e)
        await update.message.reply_text("Search error.")

async def send_result(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, edit_message=None):
    data = user_data.get(user_id, {})
    results = data.get("results", [])
    index = data.get("index", 0)
    if not results: return

    song = results[index]
    text = f"<b>{escape_html(song['title'])}</b>\n{escape_html(song['artist'])}\n{index + 1}/{len(results)}"
    
    keyboard = []
    nav = []
    if index > 0: nav.append(InlineKeyboardButton("Prev", callback_data="prev"))
    if index < len(results) - 1: nav.append(InlineKeyboardButton("Next", callback_data="next"))
    if nav: keyboard.append(nav)
    if song.get("videoId"): keyboard.append([InlineKeyboardButton("Download 🎧", callback_data=f"download_{index}")])
    
    markup = InlineKeyboardMarkup(keyboard)

    if edit_message:
        try:
            if song["thumbnail"]:
                await edit_message.delete()
                await context.bot.send_photo(edit_message.chat_id, song["thumbnail"], caption=text, reply_markup=markup, parse_mode="HTML")
            else:
                await edit_message.edit_text(text, reply_markup=markup, parse_mode="HTML")
        except:
             await context.bot.send_message(edit_message.chat_id, text, reply_markup=markup, parse_mode="HTML")
    else:
        msg = update.message or update.callback_query.message
        if song["thumbnail"]:
            await msg.reply_photo(song["thumbnail"], caption=text, reply_markup=markup, parse_mode="HTML")
        else:
            await msg.reply_text(text, reply_markup=markup, parse_mode="HTML")

# -------------------------
# Download Logic (OAuth2)
# -------------------------
async def handle_download(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, song_index: int):
    query = update.callback_query
    data = user_data.get(user_id, {})
    results = data.get("results", [])

    if not results or song_index >= len(results):
        await query.message.reply_text("Expired. Search again.")
        return

    song = results[song_index]
    original_id = song.get("videoId")
    status_msg = await query.message.reply_text("Preparing... CHECK SERVER LOGS IF THIS HANGS ⚠️")

    loop = asyncio.get_event_loop()

    # OAUTH2 OPTIONS
    # We remove 'cookiefile' and use 'username': 'oauth2'
    base_opts = {
        'username': 'oauth2', 
        'password': '',
        'cache_dir': CACHE_DIR,
        'skip_download': True,
        'quiet': False, # Must be False to see the Auth Code in logs
        'no_warnings': True,
        'allowed_extractors': ['default', 'youtube'],
    }

    def probe(url, is_fallback=False):
        opts = base_opts.copy()
        if is_fallback:
             # Fallback searches might not need strict auth, but we use it to be safe
             pass 
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)

    # 1. Probe
    info = None
    used_url = f"https://www.youtube.com/watch?v={original_id}"
    
    try:
        logger.info(f"Probing {used_url} with OAuth2...")
        info = await loop.run_in_executor(None, probe, used_url, False)
    except Exception as e:
        logger.error(f"Primary probe failed: {e}")
        # Fallback
        try:
            search_query = f"ytsearch1:{song['title']} {song['artist']} audio"
            logger.info(f"Trying fallback: {search_query}")
            info = await loop.run_in_executor(None, probe, search_query, True)
            if info and 'entries' in info:
                info = info['entries'][0]
                used_url = info.get('webpage_url')
        except Exception as e2:
            logger.error(f"Fallback failed: {e2}")

    if not info:
        await status_msg.edit_text("❌ Failed. Did you authorize in the logs?")
        return

    # 2. Download
    await status_msg.edit_text("Downloading... ⬇️")
    video_id = info.get('id', original_id)
    safe_title = sanitize_filename(song.get('title', 'track'))
    out_path = os.path.join(DOWNLOADS_DIR, f"{safe_title}_{video_id}.%(ext)s")

    dl_opts = base_opts.copy()
    dl_opts.update({
        'skip_download': False,
        'format': choose_format_from_formats(info.get('formats', [])),
        'outtmpl': out_path,
        'postprocessors': [{'key': 'FFmpegExtractAudio','preferredcodec': 'mp3','preferredquality': '192'}],
    })

    try:
        def download():
            with yt_dlp.YoutubeDL(dl_opts) as ydl:
                return ydl.extract_info(used_url, download=True)
        
        await loop.run_in_executor(None, download)

        mp3 = os.path.join(DOWNLOADS_DIR, f"{safe_title}_{video_id}.mp3")
        # Fallback finder
        if not os.path.exists(mp3):
            for f in os.listdir(DOWNLOADS_DIR):
                if video_id in f and f.endswith(".mp3"):
                    mp3 = os.path.join(DOWNLOADS_DIR, f)
                    break
        
        if os.path.exists(mp3):
            await status_msg.edit_text("Uploading... 📤")
            with open(mp3, 'rb') as f:
                await context.bot.send_audio(query.message.chat_id, f, title=song['title'], performer=song['artist'])
            os.remove(mp3)
            await status_msg.delete()
        else:
            await status_msg.edit_text("Conversion failed.")
            
    except Exception as e:
        logger.error(f"DL Error: {e}")
        await status_msg.edit_text("Error during download.")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = user_data.get(user_id, {})
    action = query.data
    
    if action == "restart":
        user_data[user_id] = {"results": [], "index": 0}
        await query.message.reply_text("Restarted.")
    elif action == "next" and data["index"] < len(data["results"]) - 1:
        user_data[user_id]["index"] += 1
        await send_result(update, context, user_id, edit_message=query.message)
    elif action == "prev" and data["index"] > 0:
        user_data[user_id]["index"] -= 1
        await send_result(update, context, user_id, edit_message=query.message)
    elif action.startswith("download_"):
        await handle_download(update, context, user_id, int(action.split("_")[1]))

def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token: return
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_song))
    
    port = int(os.environ.get("PORT", 10000))
    url = os.environ.get("RENDER_EXTERNAL_URL") or f"https://{os.environ.get('RENDER_SERVICE_NAME')}.onrender.com"
    app.run_webhook(listen="0.0.0.0", port=port, url_path=token, webhook_url=f"{url}/{token}")

if __name__ == "__main__":
    main()
