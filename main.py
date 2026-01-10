#!/usr/bin/env python3
# main.py -- No-Auth Fallback Mode

import os
import re
import logging
import asyncio
from typing import List, Dict, Any, Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from ytmusicapi import YTMusic
import yt_dlp

# -------------------------
# Config
# -------------------------
DOWNLOADS_DIR = os.environ.get("DOWNLOADS_DIR", "downloads")
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

# -------------------------
# Logging
# -------------------------
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# -------------------------
# YTMusic
# -------------------------
try:
    ytmusic = YTMusic()
except:
    ytmusic = None

user_data = {}

# -------------------------
# Helpers
# -------------------------
def escape_html(text): return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
def sanitize_filename(title): return re.sub(r'[^A-Za-z0-9 _-]', '_', title).strip()[:40]

# -------------------------
# Bot Handlers
# -------------------------
async def start(update, context):
    await update.message.reply_text("Bot Ready 🎵\nNo-Auth Mode. Send me a song.")

async def search_song(update, context):
    query = update.message.text
    if not ytmusic: return await update.message.reply_text("Search unavailable.")
    
    await update.message.reply_text(f"Searching: {query}...")
    try:
        res = ytmusic.search(query, filter="songs", limit=5)
        if not res: return await update.message.reply_text("No results.")
        
        results = []
        for item in res[:5]:
            results.append({
                "title": item.get("title", "Unknown"),
                "artist": ", ".join([a["name"] for a in item.get("artists", [])]),
                "videoId": item.get("videoId", ""),
                "thumbnail": item.get("thumbnails", [{}])[-1].get("url", "")
            })
        
        user_data[update.effective_user.id] = {"results": results, "index": 0}
        await send_result(update, context, update.effective_user.id)
    except Exception as e:
        logger.error(e)
        await update.message.reply_text("Error searching.")

async def send_result(update, context, user_id, edit_message=None):
    data = user_data.get(user_id, {})
    if not data.get("results"): return
    
    song = data["results"][data["index"]]
    text = f"<b>{escape_html(song['title'])}</b>\n{escape_html(song['artist'])}\n{data['index']+1}/{len(data['results'])}"
    
    buttons = []
    nav = []
    if data["index"] > 0: nav.append(InlineKeyboardButton("Prev", callback_data="prev"))
    if data["index"] < len(data["results"]) - 1: nav.append(InlineKeyboardButton("Next", callback_data="next"))
    if nav: buttons.append(nav)
    buttons.append([InlineKeyboardButton("Download 🎧", callback_data=f"download_{data['index']}")])
    
    markup = InlineKeyboardMarkup(buttons)
    
    if edit_message:
        try: await edit_message.edit_caption(caption=text, reply_markup=markup, parse_mode="HTML")
        except: await edit_message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    else:
        await update.message.reply_photo(song["thumbnail"], caption=text, reply_markup=markup, parse_mode="HTML")

# -------------------------
# Download Logic (No Auth)
# -------------------------
async def handle_download(update, context, user_id, index):
    query = update.callback_query
    data = user_data.get(user_id, {})
    song = data["results"][index]
    
    status = await query.message.reply_text("Searching for unlocked version... ⏳")
    
    # 1. Configuration: NO COOKIES, Spoof Android
    # This combination is most likely to bypass IP blocks for Public Videos
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': os.path.join(DOWNLOADS_DIR, f"%(title)s_%(id)s.%(ext)s"),
        'quiet': True,
        'no_warnings': True,
        'extractor_args': {'youtube': {'player_client': ['android', 'web']}}, # Spoof client
        'postprocessors': [{'key': 'FFmpegExtractAudio','preferredcodec': 'mp3','preferredquality': '192'}],
    }

    loop = asyncio.get_event_loop()
    
    # 2. Strategy: SKIP the ID. Go straight to Search.
    # The ID from YTMusic is often a "Topic" track which is strict.
    # Searching finds the Vevo/Lyric video which is loose.
    search_query = f"ytsearch1:{song['title']} {song['artist']} official audio"
    
    try:
        def download():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # We strictly use search, ignoring the provided ID which is likely restricted
                return ydl.extract_info(search_query, download=True)
        
        info = await loop.run_in_executor(None, download)
        
        if 'entries' in info: info = info['entries'][0]
        final_filename = ydl_opts['outtmpl'] % info
        mp3 = final_filename.rsplit('.', 1)[0] + ".mp3"
        
        # 3. Upload
        if os.path.exists(mp3):
            await status.edit_text("Uploading... 📤")
            with open(mp3, 'rb') as f:
                await context.bot.send_audio(query.message.chat_id, f, title=song['title'], performer=song['artist'])
            os.remove(mp3)
            await status.delete()
        else:
            await status.edit_text("Could not process file.")
            
    except Exception as e:
        logger.error(f"DL Fail: {e}")
        if "Sign in" in str(e):
            await status.edit_text("❌ Server IP is banned by YouTube. Cannot download.")
        else:
            await status.edit_text("❌ Download failed.")

async def callback(update, context):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    action = query.data
    
    if "download" in action:
        await handle_download(update, context, uid, int(action.split("_")[1]))
    elif action in ["prev", "next"]:
        user_data[uid]["index"] += 1 if action == "next" else -1
        await send_result(update, context, uid, query.message)

def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token: return
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(MessageHandler(filters.TEXT, search_song))
    
    url = os.environ.get("RENDER_EXTERNAL_URL") or f"https://{os.environ.get('RENDER_SERVICE_NAME')}.onrender.com"
    app.run_webhook(listen="0.0.0.0", port=int(os.environ.get("PORT", 10000)), url_path=token, webhook_url=f"{url}/{token}")

if __name__ == "__main__":
    main()
