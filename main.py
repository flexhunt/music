#!/usr/bin/env python3
# main.py -- fixed YouTube Music search + download Telegram bot

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
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

COOKIES_FILE = os.environ.get("COOKIES_FILE", "cookies.txt")
FFMPEG_REQUIRED = True

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
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))

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
    if not formats:
        return 'bestaudio/best'

    exts = {f.get('ext') for f in formats if f.get('ext')}
    acodes = {f.get('acodec') for f in formats if f.get('acodec')}

    if 'm4a' in exts:
        return 'bestaudio[ext=m4a]/bestaudio/best'
    if 'webm' in exts:
        return 'bestaudio[ext=webm]/bestaudio/best'

    for codec in ('opus', 'aac', 'mp3'):
        if codec in acodes:
            return f"bestaudio[acodec={codec}]/bestaudio/best"

    for f in formats:
        if f.get('vcodec') == 'none':
            return 'bestaudio/best'

    return 'bestaudio/best'

# -------------------------
# Bot handlers (search & UI)
# -------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data[user_id] = {"results": [], "index": 0}
    welcome_message = (
        "Welcome to the YouTube Music Search Bot! 🎵\n\n"
        "Send me a song name and I'll search YouTube Music for you.\n"
    )
    await update.message.reply_text(welcome_message)

async def search_song(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    query = update.message.text

    if ytmusic is None:
        await update.message.reply_text("YouTube Music service is unavailable.")
        return

    await update.message.reply_text(f"Searching for: {query}...")

    try:
        search_results = ytmusic.search(query, filter="songs", limit=5)

        if not search_results:
            await update.message.reply_text("No results found.")
            return

        results = []
        for item in search_results[:5]:
            song_data = {
                "title": item.get("title", "Unknown Title"),
                "artist": ", ".join([a.get("name", "") for a in item.get("artists", [])]) or "Unknown Artist",
                "album": item.get("album", {}).get("name", "N/A") if item.get("album") else "N/A",
                "duration": format_duration(item.get("duration", "Unknown")),
                "thumbnail": get_thumbnail(item.get("thumbnails", [])),
                "videoId": item.get("videoId", "")
            }
            results.append(song_data)

        user_data[user_id] = {"results": results, "index": 0}
        await send_result(update, context, user_id)

    except Exception as e:
        logger.exception("Search error: %s", e)
        await update.message.reply_text("An error occurred while searching.")

async def send_result(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, edit_message=None):
    data = user_data.get(user_id, {})
    results = data.get("results", [])
    index = data.get("index", 0)

    if not results:
        return

    song = results[index]

    title = escape_html(song['title'])
    artist = escape_html(song['artist'])
    
    message_text = (
        f"<b>{title}</b>\n"
        f"Artist: {artist}\n"
        f"Result {index + 1} of {len(results)}"
    )

    keyboard = []
    nav_buttons = []
    if index > 0:
        nav_buttons.append(InlineKeyboardButton("Previous", callback_data="prev"))
    if index < len(results) - 1:
        nav_buttons.append(InlineKeyboardButton("Next", callback_data="next"))

    if nav_buttons:
        keyboard.append(nav_buttons)

    if song.get("videoId"):
        keyboard.append([InlineKeyboardButton("Download Song 🎧", callback_data=f"download_{index}")])

    if index == len(results) - 1:
        keyboard.append([InlineKeyboardButton("Restart Search 🔄", callback_data="restart")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    if edit_message:
        try:
            if song["thumbnail"]:
                await edit_message.delete()
                await context.bot.send_photo(
                    chat_id=edit_message.chat_id,
                    photo=song["thumbnail"],
                    caption=message_text,
                    reply_markup=reply_markup,
                    parse_mode="HTML"
                )
            else:
                await edit_message.edit_text(text=message_text, reply_markup=reply_markup, parse_mode="HTML")
        except Exception:
             await context.bot.send_message(chat_id=edit_message.chat_id, text=message_text, reply_markup=reply_markup, parse_mode="HTML")
    else:
        message = update.message if update.message else update.callback_query.message
        if song["thumbnail"]:
            await message.reply_photo(photo=song["thumbnail"], caption=message_text, reply_markup=reply_markup, parse_mode="HTML")
        else:
            await message.reply_text(text=message_text, reply_markup=reply_markup, parse_mode="HTML")

# -------------------------
# Download + probe logic
# -------------------------
async def handle_download(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, song_index: int):
    query = update.callback_query
    data = user_data.get(user_id, {})
    results = data.get("results", [])

    if not results or song_index >= len(results):
        await query.message.reply_text("Session expired. Search again.")
        return

    song = results[song_index]
    original_video_id = song.get("videoId")
    status_msg = await query.message.reply_text("Preparing download... ⏳")

    loop = asyncio.get_event_loop()

    # Shared Probe Function
    def probe(url_to_probe: str):
        probe_opts = {
            'skip_download': True,
            'quiet': True,
            'no_warnings': True,
            'allowed_extractors': ['default', 'youtube'],
        }
        if COOKIES_FILE and os.path.exists(COOKIES_FILE):
            probe_opts['cookiefile'] = COOKIES_FILE
        
        with yt_dlp.YoutubeDL(probe_opts) as ydl:
            return ydl.extract_info(url_to_probe, download=False)

    # 1. Try Direct ID (Standard & Music)
    info = None
    used_url = None
    
    direct_urls = [
        f"https://www.youtube.com/watch?v={original_video_id}",
        f"https://music.youtube.com/watch?v={original_video_id}"
    ]

    for url in direct_urls:
        try:
            info = await loop.run_in_executor(None, probe, url)
            if info:
                used_url = url
                break
        except Exception:
            continue

    # 2. Fallback: If Direct ID failed (Topic/Restrictions), search for alternative
    if not info:
        search_query = f"ytsearch1:{song.get('title')} {song.get('artist')} audio"
        logger.info(f"Direct ID failed. Trying fallback search: {search_query}")
        try:
            info = await loop.run_in_executor(None, probe, search_query)
            if info and 'entries' in info and len(info['entries']) > 0:
                info = info['entries'][0] # Take first result
                used_url = info.get('webpage_url', info.get('url'))
                logger.info(f"Fallback found: {used_url}")
        except Exception as e:
            logger.error(f"Fallback search failed: {e}")

    if not info or not used_url:
        await status_msg.edit_text("❌ Download failed. Track is restricted and no alternative found.")
        return

    # 3. Setup Download
    video_id = info.get('id', original_video_id)
    safe_title = sanitize_filename(song.get('title', 'track'))
    base_out = f"{safe_title}_{video_id}"
    output_template = os.path.join(DOWNLOADS_DIR, f"{base_out}.%(ext)s")

    formats = info.get('formats', [])
    chosen_format = choose_format_from_formats(formats)

    ydl_opts = {
        'format': chosen_format,
        'noplaylist': True,
        'outtmpl': output_template,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'quiet': True,
        'no_warnings': True,
    }
    if COOKIES_FILE and os.path.exists(COOKIES_FILE):
        ydl_opts['cookiefile'] = COOKIES_FILE

    await status_msg.edit_text("Downloading... ⬇️")

    try:
        def download():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(used_url, download=True)

        await loop.run_in_executor(None, download)

        # Locate MP3
        mp3_file = os.path.join(DOWNLOADS_DIR, f"{base_out}.mp3")
        if not os.path.exists(mp3_file):
             # Fallback find
            for f in os.listdir(DOWNLOADS_DIR):
                if video_id in f and f.endswith('.mp3'):
                    mp3_file = os.path.join(DOWNLOADS_DIR, f)
                    break
        
        if os.path.exists(mp3_file):
            await status_msg.edit_text("Uploading... 📤")
            with open(mp3_file, 'rb') as audio_file:
                await context.bot.send_audio(
                    chat_id=query.message.chat_id,
                    audio=audio_file,
                    title=song.get('title'),
                    performer=song.get('artist'),
                    caption=f"{song.get('title')} - {song.get('artist')}"
                )
            try:
                os.remove(mp3_file)
                await status_msg.delete()
            except:
                pass
        else:
            await status_msg.edit_text("Error: File conversion failed.")

    except Exception as e:
        logger.error(f"Download Error: {e}")
        await status_msg.edit_text("Download error. Try another song.")

# -------------------------
# Callback handler
# -------------------------
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    data = user_data.get(user_id, {})
    action = query.data

    if action == "next" and data["index"] < len(data["results"]) - 1:
        user_data[user_id]["index"] += 1
        await send_result(update, context, user_id, edit_message=query.message)
    elif action == "prev" and data["index"] > 0:
        user_data[user_id]["index"] -= 1
        await send_result(update, context, user_id, edit_message=query.message)
    elif action == "restart":
        user_data[user_id] = {"results": [], "index": 0}
        await query.message.reply_text("Search restarted!")
    elif action.startswith("download_"):
        await handle_download(update, context, user_id, int(action.split("_")[1]))

# -------------------------
# Main
# -------------------------
def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("No Token")
        return

    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_song))

    port = int(os.environ.get("PORT", 10000))
    service_url = os.environ.get("RENDER_EXTERNAL_URL") or f"https://{os.environ.get('RENDER_SERVICE_NAME')}.onrender.com"
    
    application.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=token,
        webhook_url=f"{service_url}/{token}"
    )

if __name__ == "__main__":
    main()
