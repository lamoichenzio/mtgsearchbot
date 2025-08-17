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

# --- Helpers ---
def format_results_list(cards, offset, total):
    lines = [f"Results {offset+1}-{offset+len(cards)} of {total}:"]
    for idx, c in enumerate(cards, start=offset+1):
        lines.append(f"{idx}. {c.get('name','Unknown')}")
    return "\n".join(lines)

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

    # Single placeholder to avoid spamming the chat
    working = await update.message.reply_text("🔎 Cerco…")
    ctx.user_data["results_msg_id"] = working.message_id
    ctx.user_data["results_chat_id"] = update.effective_chat.id
    track_message(ctx, update.effective_chat.id, working.message_id)

    resp = requests.get("https://api.scryfall.com/cards/named", params={"fuzzy": name})
    if resp.status_code == 200:
        card = resp.json()
        logger.debug("[/search] Fuzzy found: %s", card["name"])
        try:
            await ctx.bot.delete_message(ctx.user_data["results_chat_id"], ctx.user_data["results_msg_id"])
        except Exception:
            pass
        await send_full_image(update.message, ctx, update.effective_chat.id, card)
        return

    logger.debug("[/search] Fuzzy failed, trying autocomplete")
    ac_resp = requests.get("https://api.scryfall.com/cards/autocomplete", params={"q": name})
    suggestions = ac_resp.json().get("data", [])
    if not suggestions:
        await ctx.bot.edit_message_text(
            chat_id=ctx.user_data["results_chat_id"],
            message_id=ctx.user_data["results_msg_id"],
            text=f"No results found for '{name}'."
        )
        return

    keyboard = [[InlineKeyboardButton(s, callback_data=f"namesuggest:{s}")] for s in suggestions[:10]]
    await ctx.bot.edit_message_text(
        chat_id=ctx.user_data["results_chat_id"],
        message_id=ctx.user_data["results_msg_id"],
        text="No exact match found. Did you mean:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_name_suggestion(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    name = update.callback_query.data.split(":", 1)[1]
    logger.info("[suggestion] Selected: %s", name)
    resp = requests.get("https://api.scryfall.com/cards/named", params={"fuzzy": name})
    if resp.status_code == 200:
        card = resp.json()
        try:
            chat_id = ctx.user_data.get("results_chat_id") or update.callback_query.message.chat.id
            msg_id = ctx.user_data.get("results_msg_id") or update.callback_query.message.message_id
            await ctx.bot.delete_message(chat_id, msg_id)
        except Exception:
            pass
        await send_full_image(update.callback_query.message, ctx, update.callback_query.message.chat.id, card)
        return
    else:
        chat_id = ctx.user_data.get("results_chat_id") or update.callback_query.message.chat.id
        msg_id = ctx.user_data.get("results_msg_id") or update.callback_query.message.message_id
        await ctx.bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text="❌ Failed to retrieve this card.")
        return

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

    offset = ctx.user_data["offset"]
    window = ctx.user_data["all_cards"][offset:offset+5]
    text = format_results_list(window, offset, total)
    keyboard = [[InlineKeyboardButton(c["name"], callback_data=f"findchoose:{c['id']}")] for c in window]
    if offset + 5 < total:
        keyboard.append([InlineKeyboardButton("◀️ Prev", callback_data="findprev"), InlineKeyboardButton("▶️ Next", callback_data="findnext")])
    sent = await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    ctx.user_data["results_msg_id"] = sent.message_id
    ctx.user_data["results_chat_id"] = update.effective_chat.id
    track_message(ctx, update.effective_chat.id, sent.message_id)
    return

# Removed send_query_page function entirely

async def handle_find_choice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    data = update.callback_query.data
    chat_id = ctx.user_data.get("results_chat_id") or update.callback_query.message.chat.id
    msg_id = ctx.user_data.get("results_msg_id") or update.callback_query.message.message_id

    if data == "findnext":
        ctx.user_data["offset"] = min(ctx.user_data["offset"] + 5, max(0, ctx.user_data["total"] - 5))
    elif data == "findprev":
        ctx.user_data["offset"] = max(0, ctx.user_data["offset"] - 5)
    elif data.startswith("findchoose:"):
        cid = data.split(":", 1)[1]
        card = next((c for c in ctx.user_data["all_cards"] if c["id"] == cid), None)
        if card:
            # Replace the list message by deleting it, then send the image
            try:
                await ctx.bot.delete_message(chat_id, msg_id)
            except Exception:
                pass
            await send_full_image(update.callback_query.message, ctx, chat_id, card)
        else:
            await ctx.bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text="❌ Could not find this card.")
        return
    else:
        return

    # Rebuild current window and edit the same message
    offset = ctx.user_data["offset"]
    total = ctx.user_data["total"]
    window = ctx.user_data["all_cards"][offset:offset+5]
    text = format_results_list(window, offset, total)
    keyboard = [[InlineKeyboardButton(c["name"], callback_data=f"findchoose:{c['id']}")] for c in window]
    row = []
    if offset > 0:
        row.append(InlineKeyboardButton("◀️ Prev", callback_data="findprev"))
    if offset + 5 < total:
        row.append(InlineKeyboardButton("▶️ Next", callback_data="findnext"))
    if row:
        keyboard.append(row)
    await ctx.bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=text, reply_markup=InlineKeyboardMarkup(keyboard))

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
async def send_full_image(message, ctx, chat_id, card):
    if "image_uris" in card:
        url = card["image_uris"]["normal"]
    else:
        url = card["card_faces"][0]["image_uris"]["normal"]
    caption = f"{card['name']} — {card['set_name']}"
    sent = await message.reply_photo(url, caption=caption)
    track_message(ctx, chat_id, sent.message_id)

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
app.add_handler(CallbackQueryHandler(handle_find_choice, pattern=r"^(findchoose:|findnext$|findprev$)"))
app.add_error_handler(error_handler)

PORT = int(os.getenv("PORT", "8443"))
app.run_webhook(
    listen="0.0.0.0",
    port=PORT,
    url_path=TOKEN,
    webhook_url=f"https://{HOST}/{TOKEN}"
)