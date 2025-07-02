import os
import logging
import requests
from urllib.parse import quote_plus, unquote_plus

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
    filters,
)

# --- Logging setup ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.DEBUG
)
logger = logging.getLogger(__name__)

# --- Config ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
HOST = os.getenv("RENDER_EXTERNAL_HOSTNAME", "")

# --- /start ---
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    logger.info("[/start] Triggered by %s", update.effective_user.username)
    await update.message.reply_text(
        "👋 Welcome! Use:\n"
        "/search <card name> to find a card by name\n"
        "/find <query> for advanced search (color, cmc, keywords...)"
    )

# --- /search <name> with fuzzy + suggestions ---
async def search(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        return await update.message.reply_text("Usage: /search <card name>")
    name = " ".join(ctx.args).strip()
    logger.info("[/search] Searching for: %s", name)

    resp = requests.get("https://api.scryfall.com/cards/named", params={"fuzzy": name})
    if resp.status_code == 200:
        card = resp.json()
        logger.debug("[/search] Fuzzy found: %s", card["name"])
        await send_full_image(update, card)
        return

    logger.debug("[/search] Fuzzy failed, trying autocomplete")
    ac_resp = requests.get("https://api.scryfall.com/cards/autocomplete", params={"q": name})
    suggestions = ac_resp.json().get("data", [])
    if not suggestions:
        return await update.message.reply_text(f"No results found for '{name}'.")

    keyboard = [[InlineKeyboardButton(s, callback_data=f"namesuggest:{s}")] for s in suggestions[:10]]
    await update.message.reply_text(
        "No exact match found. Did you mean:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_name_suggestion(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    name = update.callback_query.data.split(":", 1)[1]
    logger.info("[suggestion] Selected: %s", name)
    resp = requests.get("https://api.scryfall.com/cards/named", params={"fuzzy": name})
    if resp.status_code == 200:
        card = resp.json()
        await send_full_image(update.callback_query, card)
    else:
        logger.error("[suggestion] Failed to retrieve card for %s", name)
        await update.callback_query.message.reply_text("❌ Failed to retrieve this card.")

# --- /find <query> advanced search ---
async def find(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        return await update.message.reply_text("Usage: /find <query>")
    query = " ".join(ctx.args).strip()
    logger.info("[/find] Query: %s", query)

    resp = requests.get("https://api.scryfall.com/cards/search", params={"q": query, "unique": "cards", "order": "relevance"})
    data = resp.json()
    cards = data.get("data", [])
    total = data.get("total_cards", 0)
    logger.debug("[/find] Found %d cards", total)

    if not cards:
        return await update.message.reply_text("No results found for this query.")

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
    await update.message.reply_media_group(media)

    keyboard = [[InlineKeyboardButton(c["name"], callback_data=f"findchoose:{c['id']}")] for c in cards]
    if offset + 5 < total:
        keyboard.append([InlineKeyboardButton("▶️ Show more", callback_data="findnext")])

    await update.message.reply_text(
        f"Results {offset+1}-{offset+len(cards)} of {total}:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_find_choice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    data = update.callback_query.data
    if data == "findnext":
        ctx.user_data["offset"] += 5
        logger.debug("[findnext] Offset updated to %d", ctx.user_data["offset"])
        await send_query_page(update.callback_query, ctx)
        return
    cid = data.split(":", 1)[1]
    card = next((c for c in ctx.user_data["all_cards"] if c["id"] == cid), None)
    if card:
        logger.debug("[findchoose] Selected card: %s", card['name'])
        await send_full_image(update.callback_query, card)
    else:
        logger.error("[findchoose] Card with ID %s not found", cid)
        await update.callback_query.message.reply_text("❌ Could not find this card.")

# --- Send card image in HD ---
async def send_full_image(source, card):
    if "image_uris" in card:
        url = card["image_uris"]["normal"]
    else:
        url = card["card_faces"][0]["image_uris"]["normal"]
    caption = f"{card['name']} — {card['set_name']}"
    logger.debug("[send_full_image] Sending image: %s", url)
    await source.message.reply_photo(url, caption=caption)

# --- Error handler ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("🚨 Exception handled:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text("❌ An internal error occurred, please try again later.")

# --- Application setup ---
app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("search", search))
app.add_handler(CommandHandler("find", find))
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