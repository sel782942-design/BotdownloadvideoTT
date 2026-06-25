import os
import logging
import re
import yt_dlp
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, MessageHandler, CommandHandler, CallbackQueryHandler, filters, ContextTypes

# Loads variables from a .env file (in the same folder) into the environment
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Reads the token from the .env file. Never hardcode your token directly in this file.
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

TIKTOK_URL_PATTERN = re.compile(r"(https?://(?:www\.|vt\.|vm\.)?tiktok\.com/\S+)")
INSTAGRAM_URL_PATTERN = re.compile(
    r"(https?://(?:www\.)?instagram\.com/(?:reel|reels|p|tv)/\S+)"
)

# Temporary in-memory map of message id -> (platform, url), so buttons know what to download
pending_urls = {}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Send me a TikTok or Instagram link. I'll ask if you want the video or just the audio."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""

    tiktok_match = TIKTOK_URL_PATTERN.search(text)
    instagram_match = INSTAGRAM_URL_PATTERN.search(text)

    if tiktok_match:
        platform = "tiktok"
        url = tiktok_match.group(1)
    elif instagram_match:
        platform = "instagram"
        url = instagram_match.group(1)
    else:
        await update.message.reply_text("Please send a valid TikTok or Instagram link.")
        return

    msg = await update.message.reply_text("What would you like?")
    pending_urls[msg.message_id] = (platform, url)

    keyboard = [
        [
            InlineKeyboardButton("🎬 Video", callback_data=f"video:{msg.message_id}"),
            InlineKeyboardButton("🎵 Audio only", callback_data=f"audio:{msg.message_id}"),
        ]
    ]
    await msg.edit_text("What would you like?", reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    mode, msg_id_str = query.data.split(":")
    msg_id = int(msg_id_str)
    entry = pending_urls.get(msg_id)

    if not entry:
        await query.edit_message_text("This link expired, please send it again.")
        return

    platform, url = entry

    await query.edit_message_text("Downloading, please wait...")

    file_path = None
    try:
        if mode == "video":
            file_path = download_media(url, platform, audio_only=False)
            with open(file_path, "rb") as f:
                await query.message.reply_video(video=f, supports_streaming=True)
        else:
            file_path = download_media(url, platform, audio_only=True)
            with open(file_path, "rb") as f:
                await query.message.reply_audio(audio=f)
        await query.message.delete()
    except Exception as e:
        logger.error(f"Download failed: {e}")
        await query.edit_message_text(f"Failed to download: {e}")
    finally:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
        pending_urls.pop(msg_id, None)


def download_media(url: str, platform: str, audio_only: bool = False) -> str:
    """Downloads a TikTok or Instagram video (or its audio track) and returns the local file path."""
    output_template = os.path.join(DOWNLOAD_DIR, "%(id)s.%(ext)s")

    if audio_only:
        ydl_opts = {
            "outtmpl": output_template,
            "format": "bestaudio/best",
            "quiet": True,
            "no_warnings": True,
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
        }
    else:
        ydl_opts = {
            "outtmpl": output_template,
            "format": "mp4/bestvideo+bestaudio/best",
            "quiet": True,
            "no_warnings": True,
        }
        if platform == "tiktok":
            ydl_opts["extractor_args"] = {"tiktok": {"download_without_watermark": ["true"]}}

    # Instagram private/login-walled content needs cookies; see note below.
    cookie_file = os.environ.get("INSTAGRAM_COOKIES_FILE")
    if platform == "instagram" and cookie_file and os.path.exists(cookie_file):
        ydl_opts["cookiefile"] = cookie_file

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)

        if audio_only:
            # the postprocessor changes the extension to .mp3
            base, _ = os.path.splitext(filename)
            mp3_path = base + ".mp3"
            if os.path.exists(mp3_path):
                return mp3_path

        return filename


def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN not found. Create a .env file next to this script "
            "with a line like: TELEGRAM_BOT_TOKEN=your_token_here"
        )

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_button))

    logger.info("Bot started...")
    app.run_polling()


if __name__ == "__main__":
    main()
