import os
import logging
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from ytmusicapi import YTMusic
import yt_dlp

DOWNLOADS_DIR = "downloads"
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

try:
    ytmusic = YTMusic()
except Exception as e:
    logger.error(f"Failed to initialize YTMusic: {e}")
    ytmusic = None

user_data = {}

def escape_html(text):
    if not text:
        return ""
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))

def format_duration(duration_str):
    if duration_str:
        return duration_str
    return "Unknown"

def get_thumbnail(thumbnails):
    if thumbnails and len(thumbnails) > 0:
        return thumbnails[-1].get("url", "")
    return ""

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
        logger.error(f"Search error: {e}")
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
                logger.error(f"Error sending photo: {e}")
                await context.bot.send_message(
                    chat_id=edit_message.chat_id,
                    text=message_text,
                    reply_markup=reply_markup,
                    parse_mode="HTML"
                )
        else:
            await edit_message.edit_text(
                text=message_text,
                reply_markup=reply_markup,
                parse_mode="HTML"
            )
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
                logger.error(f"Error sending photo: {e}")
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

async def handle_download(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, song_index: int):
    query = update.callback_query
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
   
    status_msg = await query.message.reply_text("Downloading song... Please wait ⏳")
   
    try:
        url = f"https://music.youtube.com/watch?v={video_id}"
       
        safe_title = "".join(c for c in song['title'] if c.isalnum() or c in (' ', '-', '_')).strip()[:50]
        output_template = os.path.join(DOWNLOADS_DIR, f"{safe_title}_{video_id}.%(ext)s")
       
        ydl_opts = {
    # No strict 'format' - yt-dlp auto-selects best available audio
    'format_sort': ['abr', 'asr', 'vcodec:none'],  # Sort by audio bitrate, sample rate, prefer no video
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'mp3',
        'preferredquality': '192',
    }],
    'outtmpl': output_template,
    'quiet': True,
    'no_warnings': True,
    'cookiefile': 'cookies.txt',
    'extractaudio': True,  # Force audio extraction if needed
}
       
        loop = asyncio.get_event_loop()
       
        def download():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                return info
       
        info = await loop.run_in_executor(None, download)
       
        mp3_file = os.path.join(DOWNLOADS_DIR, f"{safe_title}_{video_id}.mp3")
       
        if not os.path.exists(mp3_file):
            for f in os.listdir(DOWNLOADS_DIR):
                if video_id in f and f.endswith('.mp3'):
                    mp3_file = os.path.join(DOWNLOADS_DIR, f)
                    break
       
        if os.path.exists(mp3_file):
            await status_msg.edit_text("Uploading to Telegram... 📤")
           
            try:
                with open(mp3_file, 'rb') as audio_file:
                    await context.bot.send_audio(
                        chat_id=query.message.chat_id,
                        audio=audio_file,
                        title=song['title'],
                        performer=song['artist'],
                        caption=f"{song['title']} - {song['artist']}"
                    )
                await status_msg.delete()
            finally:
                if os.path.exists(mp3_file):
                    os.remove(mp3_file)
        else:
            await status_msg.edit_text("Failed to download. Please try again.")
           
    except Exception as e:
        logger.error(f"Download error: {e}")
        await status_msg.edit_text(f"Download failed: {str(e)}")
        for f in os.listdir(DOWNLOADS_DIR):
            if video_id in f:
                try:
                    os.remove(os.path.join(DOWNLOADS_DIR, f))
                except:
                    pass

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
            await handle_download(update, context, user_id, song_index)
        except (ValueError, IndexError):
            await query.message.reply_text("Invalid download request.")

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
   
    logger.info(f"Starting webhook on {webhook_url}")
   
    application.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=token,
        webhook_url=webhook_url
    )

if __name__ == "__main__":
    main()
