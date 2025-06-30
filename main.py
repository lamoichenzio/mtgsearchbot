import os
import logging
import requests
from urllib.parse import quote_plus, unquote_plus

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InputMediaPhoto,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# --- CONFIGURA LOG ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.DEBUG
)
logger = logging.getLogger(__name__)

# --- TOKEN E HOST ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
HOST = os.getenv("RENDER_EXTERNAL_HOSTNAME", "")

# --- STATI CONVERSAZIONE ---
(
    CHOOSING_MODE,
    TYPING_NAME,
    TYPING_QUERY,
) = range(3)

# --- Start, menu funzionalità ---
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    logger.info("Comando /start ricevuto")
    keyboard = [
        [KeyboardButton("🔍 Cerca per nome")],
        [KeyboardButton("🧩 Ricerca avanzata")]
    ]
    await update.message.reply_text(
        "Scegli una modalità di ricerca:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )
    return CHOOSING_MODE

# --- Gestione scelta modalità ---
async def mode_choice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    logger.info("Modalità scelta: %s", text)
    if "Cerca per nome" in text:
        await update.message.reply_text("Inserisci il nome della carta:", reply_markup=ReplyKeyboardMarkup([], resize_keyboard=True))
        return TYPING_NAME
    elif "Ricerca avanzata" in text:
        await update.message.reply_text("Inserisci la query (es: c:B cmc=3 o:\"Whenever a creature\"):")
        return TYPING_QUERY
    else:
        return CHOOSING_MODE

# --- Cerca per nome ---
async def received_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    logger.info("Nome richiesto: %s", name)
    resp = requests.get(f"https://api.scryfall.com/cards/search", params={"q": f'!"{name}"'})
    data = resp.json()
    cards = data.get("data", [])
    if not cards:
        await update.message.reply_text(f"Nessuna carta trovata per '{name}'. Torna al menu con /start.")
        return ConversationHandler.END
    if len(cards) == 1:
        card = cards[0]
        await send_full_image(update, card)
        return ConversationHandler.END

    keyboard = [[InlineKeyboardButton(f"{c['name']} – {c['set_name']}", callback_data=f"namechoose:{c['id']}")] for c in cards[:5]]
    await update.message.reply_text("Seleziona la carta:", reply_markup=InlineKeyboardMarkup(keyboard))
    ctx.user_data["cards_by_id"] = {c["id"]: c for c in cards}
    return ConversationHandler.END

async def handle_name_choice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    cid = update.callback_query.data.split(":",1)[1]
    card = ctx.user_data.get("cards_by_id", {}).get(cid)
    if card:
        await send_full_image(update.callback_query, card)
    return ConversationHandler.END

# --- Ricerca avanzata ---
async def received_query(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.strip()
    logger.info("Query avanzata: %s", query)
    resp = requests.get("https://api.scryfall.com/cards/search", params={"q": query, "unique": "cards", "order": "relevance"})
    data = resp.json()
    cards = data.get("data", [])
    total = data.get("total_cards", 0)
    if not cards:
        await update.message.reply_text("Nessun risultato. Torna al menu con /start.")
        return ConversationHandler.END
    ctx.user_data["query"] = query
    ctx.user_data["total"] = total
    ctx.user_data["all_cards"] = cards
    ctx.user_data["offset"] = 0
    await send_query_page(update, ctx)
    return ConversationHandler.END

async def send_query_page(update, ctx):
    offset = ctx.user_data["offset"]
    cards = ctx.user_data["all_cards"][offset:offset+5]
    total = ctx.user_data["total"]
    logger.debug("Pagina query offset=%d, totale=%d", offset, total)
    media = [InputMediaPhoto(c.get("image_uris", c["card_faces"][0]["image_uris"])["small"], caption=c["name"]) for c in cards]
    await update.message.reply_media_group(media)
    keyboard = []
    for c in cards:
        keyboard.append([InlineKeyboardButton(c["name"], callback_data=f"querychoose:{c['id']}")])
    if offset + 5 < total:
        keyboard.append([InlineKeyboardButton("▶️ Altri 5", callback_data="querynext")])
    await update.message.reply_text(f"{offset+1}–{offset+len(cards)} di {total}", reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_query_choice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    data = update.callback_query.data
    if data == "querynext":
        ctx.user_data["offset"] += 5
        return await send_query_page(update.callback_query, ctx)
    cid = data.split(":",1)[1]
    card = next((c for c in ctx.user_data["all_cards"] if c["id"] == cid), None)
    if card:
        await send_full_image(update.callback_query, card)

# --- Utility: invia immagine full res ---
async def send_full_image(source, card):
    url = card.get("image_uris", card["card_faces"][0]["image_uris"])["normal"]
    caption = f"{card['name']} — {card['set_name']}"
    await source.message.reply_photo(url, caption=caption)

# --- Error handler ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("🚨 Exception while handling update:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text("❌ Si è verificato un errore interno, riprova più tardi.")

# --- Dispatcher setup ---
conv = ConversationHandler(
    entry_points=[CommandHandler("start", start)],
    states={
        CHOOSING_MODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, mode_choice)],
        TYPING_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_name)],
        TYPING_QUERY: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_query)],
    },
    fallbacks=[CommandHandler("start", start)],
    per_user=True
)

app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(conv)
app.add_handler(CallbackQueryHandler(handle_name_choice, pattern=r"^namechoose:"))
app.add_handler(CallbackQueryHandler(handle_query_choice, pattern=r"^(querychoose:|querynext$)"))
app.add_error_handler(error_handler)

app.run_webhook(
    listen="0.0.0.0",
    port=int(os.getenv("PORT", "8443")),
    url_path=TOKEN,
    webhook_url=f"https://{HOST}/{TOKEN}"
)