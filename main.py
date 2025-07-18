import os
import logging
import requests

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from collections import deque

# --- Logging setup ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.DEBUG
)
logger = logging.getLogger(__name__)

# --- Config ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
HOST = os.getenv("RENDER_EXTERNAL_HOSTNAME", "")
MAX_TRACKED_MESSAGES = 500

# --- Utility to track sent message IDs ---
def track_message(ctx, chat_id, message_id):
    if "sent_messages" not in ctx.application.bot_data:
        ctx.application.bot_data["sent_messages"] = {}
    if chat_id not in ctx.application.bot_data["sent_messages"]:
        ctx.application.bot_data["sent_messages"][chat_id] = deque(maxlen=MAX_TRACKED_MESSAGES)
    ctx.application.bot_data["sent_messages"][chat_id].append(message_id)
    logger.debug("[track_message] Tracked message %d in chat %d", message_id, chat_id)

# --- /start ---
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    logger.info("[/start] Triggered by %s", update.effective_user.username)
    sent = await update.message.reply_text(
        "👋 MTG Search Bot ready.\n\n"
        "Commands:\n"
        "/search <card name> - Find a card by name\n"
        "/find <query> - Advanced card search\n"
        "/cleanup <N> - Delete last N bot messages\n\n"
        "Example /find queries:\n"
        "• c:r cmc=1\n"
        "• t:creature o:\"draw a card\"\n"
        "• o:flying c:u cmc<=3\n"
        "Full syntax: https://scryfall.com/docs/syntax"
    )
    track_message(ctx, update.effective_chat.id, sent.message_id)

# --- /search ---
async def search(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        sent = await update.message.reply_text("Usage: /search <card name>")
        track_message(ctx, update.effective_chat.id, sent.message_id)
        return
    name = " ".join(ctx.args).strip()
    logger.info("[/search] Searching for: %s", name)

    resp = requests.get("https://api.scryfall.com/cards/named", params={"fuzzy": name})
    if resp.status_code == 200:
        card = resp.json()
        logger.debug("[/search] Fuzzy found: %s", card["name"])
        await send_full_image(update, ctx, card)
        return

    logger.debug("[/search] Fuzzy failed, trying autocomplete")
    ac_resp = requests.get("https://api.scryfall.com/cards/autocomplete", params={"q": name})
    suggestions = ac_resp.json().get("data", [])
    if not suggestions:
        sent = await update.message.reply_text(f"No results found for '{name}'.")
        track_message(ctx, update.effective_chat.id, sent.message_id)
        return

    keyboard = [[InlineKeyboardButton(s, callback_data=f"namesuggest:{s}")] for s in suggestions[:10]]
    sent = await update.message.reply_text(
        "No exact match found. Did you mean:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    track_message(ctx, update.effective_chat.id, sent.message_id)

async def handle_name_suggestion(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_reply_markup(reply_markup=None)
    name = update.callback_query.data.split(":", 1)[1]
    logger.info("[suggestion] Selected: %s", name)
    resp = requests.get("https://api.scryfall.com/cards/named", params={"fuzzy": name})
    if resp.status_code == 200:
        card = resp.json()
        await send_full_image(update.callback_query, ctx, card)
    else:
        sent = await update.callback_query.message.reply_text("❌ Failed to retrieve this card.")
        track_message(ctx, update.effective_chat.id, sent.message_id)

# --- /find ---
async def find(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        sent = await update.message.reply_text(
            "Usage: /find <query>\n\n"
            "Examples:\n"
            "• c:r cmc=1\n"
            "• t:creature o:\"draw a card\"\n"
            "• o:flying c:u cmc<=3\n"
            "Full syntax: https://scryfall.com/docs/syntax"
        )
        track_message(ctx, update.effective_chat.id, sent.message_id)
        return
    query = " ".join(ctx.args).strip()
    logger.info("[/find] Query: %s", query)

    resp = requests.get("https://api.scryfall.com/cards/search", params={"q": query, "unique": "cards", "order": "relevance"})
    data = resp.json()
    cards = data.get("data", [])
    total = data.get("total_cards", 0)
    logger.debug("[/find] Found %d cards", total)

    if not cards:
        sent = await update.message.reply_text("No results found for this query.")
        track_message(ctx, update.effective_chat.id, sent.message_id)
        return

    ctx.user_data["query"] = query
    ctx.user_data["total"] = total
    ctx.user_data["all_cards"] = cards
    ctx.user_data["offset"] = 0

    await send_query_page(update, ctx)

async def send_query_page(update, ctx):
    offset = ctx.user_data["offset"]
    cards = ctx.user_data["all_cards"][offset:offset+5]
    total = ctx.user_data["total"]
    logger.debug("[/find] Showing cards %d-%d of %d", offset+1, offset+len(cards), total)

    media = []
    for c in cards:
        img_url = c["image_uris"]["small"] if "image_uris" in c else c["card_faces"][0]["image_uris"]["small"]
        media.append(InputMediaPhoto(img_url, caption=c["name"]))
    sent_msgs = await update.message.reply_media_group(media)
    for msg in sent_msgs:
        track_message(ctx, update.effective_chat.id, msg.message_id)

    keyboard = [[InlineKeyboardButton(c["name"], callback_data=f"findchoose:{c['id']}")] for c in cards]
    if offset + 5 < total:
        keyboard.append([InlineKeyboardButton("▶️ Show more", callback_data="findnext")])

    sent = await update.message.reply_text(
        f"Results {offset+1}-{offset+len(cards)} of {total}:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    track_message(ctx, update.effective_chat.id, sent.message_id)

async def handle_find_choice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_reply_markup(reply_markup=None)
    data = update.callback_query.data
    if data == "findnext":
        ctx.user_data["offset"] += 5
        logger.debug("[findnext] Offset updated to %d", ctx.user_data["offset"])
        await send_query_page(update.callback_query, ctx)
        return
    cid = data.split(":", 1)[1]
    card = next((c for c in ctx.user_data["all_cards"] if c["id"] == cid), None)
    if card:
        await send_full_image(update.callback_query, ctx, card)
    else:
        sent = await update.callback_query.message.reply_text("❌ Could not find this card.")
        track_message(ctx, update.effective_chat.id, sent.message_id)

# --- /cleanup ---
async def cleanup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    count = int(args[0]) if args and args[0].isdigit() else 15
    chat_id = update.effective_chat.id
    logger.info("[/cleanup] Requested by %s to delete last %d messages", update.effective_user.username, count)

    sent = await update.message.reply_text(f"🧹 Cleaning up last {count} bot messages...")
    track_message(ctx, chat_id, sent.message_id)

    try:
        messages = list(ctx.application.bot_data.get("sent_messages", {}).get(chat_id, []))[-count:]
        deleted = 0
        for msg_id in messages:
            try:
                await ctx.bot.delete_message(chat_id, msg_id)
                deleted += 1
            except Exception as e:
                logger.warning("[/cleanup] Could not delete message %d: %s", msg_id, str(e))
        done = await update.message.reply_text(f"✅ Cleanup completed. Deleted {deleted} messages.")
        track_message(ctx, chat_id, done.message_id)
    except Exception as e:
        logger.error("[/cleanup] Error: %s", str(e))
        sent = await update.message.reply_text("❌ An error occurred during cleanup.")
        track_message(ctx, chat_id, sent.message_id)

# --- Send card image ---
async def send_full_image(source, ctx, card):
    if "image_uris" in card:
        url = card["image_uris"]["normal"]
    else:
        url = card["card_faces"][0]["image_uris"]["normal"]
    caption = f"{card['name']} — {card['set_name']}"
    sent = await source.message.reply_photo(url, caption=caption)
    track_message(ctx, source.effective_chat.id, sent.message_id)

# --- Error handler ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("🚨 Exception handled:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        sent = await update.effective_message.reply_text("❌ An internal error occurred, please try again later.")
        track_message(context, update.effective_chat.id, sent.message_id)

# --- Application setup ---
app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("search", search))
app.add_handler(CommandHandler("find", find))
app.add_handler(CommandHandler("cleanup", cleanup))
app.add_handler(CallbackQueryHandler(handle_name_suggestion, pattern=r"^namesuggest:"))
app.add_handler(CallbackQueryHandler(handle_find_choice, pattern=r"^(findchoose:|findnext$)"))
app.add_error_handler(error_handler)

PORT = int(os.getenv("PORT", "8443"))
app.run_webhook(
    listen="0.0.0.0",
    port=PORT,
    url_path=TOKEN,
    webhook_url=f"https://{HOST}/{TOKEN}"
)