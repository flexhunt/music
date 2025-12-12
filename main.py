#!/usr/bin/env python3
# main.py -- fixed YouTube Music search + download Telegram bot (python-telegram-bot v20+)

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

COOKIES_FILE = os.environ.get("COOKIES_FILE", "cookies.txt")  # optional, recommended
FFMPEG_REQUIRED = True  # set to True -- Render must have ffmpeg in PATH

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
    ytmusic = YTMusic()  # local unauthenticated client (works for basic searches)
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
    """
    Choose a safe format selector string based on available formats returned by yt-dlp probe.
    Preference order: m4a -> webm -> opus/aac/mp3 acodec -> any audio-only -> bestaudio/best
    """
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

    # look for audio-only formats explicitly (no vcodec)
    for f in formats:
        if f.get('vcodec') == 'none':
            # pick that format id or use general selector
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
        "You can browse through the results using the navigation buttons."
    )
    await update.message.reply_text(welcome_message)

async def search_song(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    query = update.message.text

    if ytmusic is None:
        await update.message.reply_text("YouTube Music service is unavailable. Please try again later.")
        return

    await update.message.reply_text(f"Searching for: {query}...")

    try:
        search_results = ytmusic.search(query, filter="songs", limit=5)

        if not search_results:
            await update.message.reply_text("No results found. Please try a different search term.")
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
        await update.message.reply_text("An error occurred while searching. Please try again.")

async def send_result(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, edit_message=None):
    data = user_data.get(user_id, {})
    results = data.get("results", [])
    index = data.get("index", 0)

    if not results:
        return

    song = results[index]

    title = escape_html(song['title'])
    artist = escape_html(song['artist'])
    album = escape_html(song['album'])
    duration = escape_html(song['duration'])

    message_text = (
        f"<b>{title}</b>\n\n"
        f"Artist: {artist}\n"
        f"Album: {album}\n"
        f"Duration: {duration}\n\n"
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
        # attempt to delete original and send new photo message (keeps appearance consistent)
        if song["thumbnail"]:
            try:
                await edit_message.delete()
                await context.bot.send_photo(
                    chat_id=edit_message.chat_id,
                    photo=song["thumbnail"],
                    caption=message_text,
                    reply_markup=reply_markup,
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.warning("Failed to send photo when editing: %s", e)
                await context.bot.send_message(
                    chat_id=edit_message.chat_id,
                    text=message_text,
                    reply_markup=reply_markup,
                    parse_mode="HTML"
                )
        else:
            try:
                await edit_message.edit_text(
                    text=message_text,
                    reply_markup=reply_markup,
                    parse_mode="HTML"
                )
            except Exception as e:
                # fallback: send new message
                logger.warning("Failed to edit message, sending new message: %s", e)
                await context.bot.send_message(chat_id=edit_message.chat_id, text=message_text, reply_markup=reply_markup, parse_mode="HTML")
    else:
        message = update.message if update.message else update.callback_query.message
        if song["thumbnail"]:
            try:
                await message.reply_photo(
                    photo=song["thumbnail"],
                    caption=message_text,
                    reply_markup=reply_markup,
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.warning("Error sending photo: %s", e)
                await message.reply_text(
                    text=message_text,
                    reply_markup=reply_markup,
                    parse_mode="HTML"
                )
        else:
            await message.reply_text(
                text=message_text,
                reply_markup=reply_markup,
                parse_mode="HTML"
            )

# -------------------------
# Download + probe logic
# -------------------------
async def handle_download(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, song_index: int):
    query = update.callback_query
    # we intentionally answer earlier in button handler; here ensure safe
    # await query.answer()  # already answered by button handler

    data = user_data.get(user_id, {})
    results = data.get("results", [])

    if not results or song_index >= len(results):
        await query.message.reply_text("No song to download. Please search again.")
        return

    song = results[song_index]
    video_id = song.get("videoId")
    if not video_id:
        await query.message.reply_text("Cannot download this song.")
        return

    status_msg = await query.message.reply_text("Preparing download... ⏳")

    try:
        # prefer youtube.com and fallback to music.youtube.com if needed
        url_primary = f"https://www.youtube.com/watch?v={video_id}"
        url_music = f"https://music.youtube.com/watch?v={video_id}"

        safe_title = sanitize_filename(song.get('title', 'track'))
        base_out = f"{safe_title}_{video_id}"
        output_template = os.path.join(DOWNLOADS_DIR, f"{base_out}.%(ext)s")

        loop = asyncio.get_event_loop()

        # Probe function (no download) - try primary youtube.com first
        def probe(url_to_probe: str):
            probe_opts = {
                'skip_download': True,
                'quiet': True,
                'no_warnings': True,
            }
            if COOKIES_FILE and os.path.exists(COOKIES_FILE):
                probe_opts['cookiefile'] = COOKIES_FILE
            with yt_dlp.YoutubeDL(probe_opts) as ydl:
                info = ydl.extract_info(url_to_probe, download=False)
                return info

        # try primary url then music url if primary probe fails
        info = None
        probe_errors = []
        for probe_url in (url_primary, url_music):
            try:
                info = await loop.run_in_executor(None, probe, probe_url)
                probe_used_url = probe_url
                break
            except Exception as e:
                logger.warning("Probe failed for %s: %s", probe_url, e)
                probe_errors.append((probe_url, str(e)))
                info = None

        if not info:
            logger.error("All probes failed for %s: %s", video_id, probe_errors)
            await status_msg.edit_text("Could not probe the video formats. It may be restricted.")
            return

        formats = info.get('formats', [])
        # log a short sample of formats for debugging
        logger.info("Formats sample for %s (probing %s): %s",
                    video_id, probe_used_url,
                    ", ".join(f"{f.get('format_id')}/{f.get('ext')}/{f.get('acodec')}" for f in formats[:8]))

        chosen_format = choose_format_from_formats(formats)
        logger.info("Chosen format for %s: %s", video_id, chosen_format)

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
            'overwrites': False,
        }
        if COOKIES_FILE and os.path.exists(COOKIES_FILE):
            ydl_opts['cookiefile'] = COOKIES_FILE

        status_msg = await status_msg.edit_text("Downloading and converting to mp3... ⬇️")

        def download(url_to_download: str):
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(url_to_download, download=True)

        # try download with probe_used_url first, if fails try the other URL and a fallback chain
        download_errors = []
        try_urls = [probe_used_url] + ([url_primary, url_music] if probe_used_url not in (url_primary, url_music) else [])
        download_info = None
        for dl_url in try_urls:
            try:
                download_info = await loop.run_in_executor(None, download, dl_url)
                break
            except DownloadError as e:
                logger.warning("Download failed for %s with chosen format %s: %s", dl_url, chosen_format, e)
                download_errors.append((dl_url, str(e)))
            except Exception as e:
                logger.exception("Unexpected download error for %s: %s", dl_url, e)
                download_errors.append((dl_url, str(e)))

        # If still not downloaded, try fallback format selectors
        if not download_info:
            fallback_chain = [
                'bestaudio[ext=m4a]/bestaudio/best',
                'bestaudio[ext=webm]/bestaudio/best',
                'bestaudio/best',
                'best'
            ]
            last_exc = None
            for fmt in fallback_chain:
                ydl_opts['format'] = fmt
                logger.info("Trying fallback format %s for %s", fmt, video_id)
                def dl2(dl_url_local):
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl2:
                        return ydl2.extract_info(dl_url_local, download=True)
                for dl_url in (url_primary, url_music):
                    try:
                        download_info = await loop.run_in_executor(None, dl2, dl_url)
                        break
                    except Exception as e:
                        last_exc = e
                        logger.warning("Fallback format %s failed for %s: %s", fmt, dl_url, e)
                if download_info:
                    break
            if not download_info:
                logger.error("All download attempts failed for %s: %s", video_id, download_errors)
                await status_msg.edit_text("Download failed for all attempted formats. See logs.")
                return

        # locate mp3
        mp3_file = os.path.join(DOWNLOADS_DIR, f"{base_out}.mp3")
        if not os.path.exists(mp3_file):
            # search for any file with video_id and .mp3
            for f in os.listdir(DOWNLOADS_DIR):
                if video_id in f and f.lower().endswith('.mp3'):
                    mp3_file = os.path.join(DOWNLOADS_DIR, f)
                    break

        if os.path.exists(mp3_file):
            await status_msg.edit_text("Uploading to Telegram... 📤")
            try:
                with open(mp3_file, 'rb') as audio_file:
                    await context.bot.send_audio(
                        chat_id=query.message.chat_id,
                        audio=audio_file,
                        title=song.get('title'),
                        performer=song.get('artist'),
                        caption=f"{song.get('title')} - {song.get('artist')}"
                    )
                try:
                    await status_msg.delete()
                except:
                    pass
            finally:
                # cleanup
                try:
                    os.remove(mp3_file)
                except Exception as e:
                    logger.warning("Failed to remove mp3 %s: %s", mp3_file, e)
        else:
            logger.error("Expected mp3 not found after download for %s", video_id)
            await status_msg.edit_text("Download completed but MP3 file not found. Try again later.")

    except Exception as exc:
        logger.exception("Download error for %s: %s", video_id, exc)
        try:
            await status_msg.edit_text(f"Download failed: {str(exc)}")
        except:
            pass
        # cleanup partial files that include video_id
        for f in os.listdir(DOWNLOADS_DIR):
            if video_id in f:
                try:
                    os.remove(os.path.join(DOWNLOADS_DIR, f))
                except Exception:
                    pass

# -------------------------
# Callback handler
# -------------------------
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    data = user_data.get(user_id, {})

    if not data.get("results"):
        await query.message.reply_text("No search results found. Please send a song name to search.")
        return

    action = query.data

    if action == "next":
        if data["index"] < len(data["results"]) - 1:
            user_data[user_id]["index"] += 1
            await send_result(update, context, user_id, edit_message=query.message)

    elif action == "prev":
        if data["index"] > 0:
            user_data[user_id]["index"] -= 1
            await send_result(update, context, user_id, edit_message=query.message)

    elif action == "restart":
        user_data[user_id] = {"results": [], "index": 0}
        await query.message.reply_text("Search restarted! Send me a song name to search again.")

    elif action.startswith("download_"):
        try:
            song_index = int(action.split("_")[1])
            # spawn the download handler (it is async and uses run_in_executor)
            await handle_download(update, context, user_id, song_index)
        except (ValueError, IndexError):
            await query.message.reply_text("Invalid download request.")

# -------------------------
# Main (webhook)
# -------------------------
def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN environment variable not set!")
        return

    application = Application.builder().token(token).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_song))

    port = int(os.environ.get("PORT", 10000))
    service_url = os.environ.get("RENDER_EXTERNAL_URL") or f"https://{os.environ.get('RENDER_SERVICE_NAME')}.onrender.com"
    webhook_url = f"{service_url}/{token}"

    logger.info("Starting webhook on %s", webhook_url)

    application.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=token,
        webhook_url=webhook_url
    )

if __name__ == "__main__":
    main()
