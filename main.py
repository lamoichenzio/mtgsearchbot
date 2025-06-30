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
    ContextTypes,
    ConversationHandler,
    filters,
)

# --- Logging setup ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.DEBUG
)
logger = logging.getLogger(__name__)

# --- Token e Host ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
HOST = os.getenv("RENDER_EXTERNAL_HOSTNAME", "")

# --- Stati conversazione ---
CHOOSING_MODE, TYPING_NAME, TYPING_QUERY = range(3)

# --- /start e tastiera principale ---
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    logger.info("[/start] Avviato da %s", update.effective_user.username)
    keyboard = [
        [KeyboardButton("🔍 Cerca per nome")],
        [KeyboardButton("🧩 Ricerca avanzata")]
    ]
    await update.message.reply_text(
        "Scegli una modalità di ricerca:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )
    return CHOOSING_MODE

# --- Scelta modalità ---
async def mode_choice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    logger.info("[Mode] Selezionato: %s", text)
    if "Cerca per nome" in text:
        await update.message.reply_text("Inserisci il nome della carta:")
        return TYPING_NAME
    elif "Ricerca avanzata" in text:
        await update.message.reply_text("Inserisci la query (es: c:B cmc=3 o:\"Whenever a creature\"):")
        return TYPING_QUERY
    else:
        await update.message.reply_text("Scelta non valida, riprova con /start.")
        return CHOOSING_MODE

# --- Ricerca per nome con fuzzy + suggerimenti ---
async def received_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    logger.info("[Name] Cercando: %s", name)

    # Prova fuzzy
    resp = requests.get("https://api.scryfall.com/cards/named", params={"fuzzy": name})
    if resp.status_code == 200:
        card = resp.json()
        logger.debug("[Name] Fuzzy trovato: %s", card["name"])
        await send_full_image(update, card)
        return ConversationHandler.END

    logger.debug("[Name] Fuzzy fallito, provo autocomplete")
    ac_resp = requests.get("https://api.scryfall.com/cards/autocomplete", params={"q": name})
    suggestions = ac_resp.json().get("data", [])
    if not suggestions:
        await update.message.reply_text(f"❌ Nessuna carta trovata per '{name}'. Usa /start per riprovare.")
        return ConversationHandler.END

    keyboard = [[InlineKeyboardButton(s, callback_data=f"namesuggest:{s}")] for s in suggestions[:10]]
    await update.message.reply_text(
        "Non ho trovato una corrispondenza esatta. Ecco dei suggerimenti:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return ConversationHandler.END

async def handle_name_suggestion(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    name = update.callback_query.data.split(":", 1)[1]
    logger.info("[NameSuggest] Selezionato: %s", name)
    resp = requests.get("https://api.scryfall.com/cards/named", params={"fuzzy": name})
    if resp.status_code == 200:
        card = resp.json()
        await send_full_image(update.callback_query, card)
    else:
        logger.error("[NameSuggest] Errore nel recupero della carta per %s", name)
        await update.callback_query.message.reply_text("❌ Errore nel recupero della carta.")

# --- Ricerca avanzata ---
async def received_query(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.message.text.strip()
    logger.info("[Query] Ricevuta: %s", query)
    resp = requests.get("https://api.scryfall.com/cards/search", params={"q": query, "unique": "cards", "order": "relevance"})
    data = resp.json()
    cards = data.get("data", [])
    total = data.get("total_cards", 0)
    logger.debug("[Query] Trovate %d carte", total)

    if not cards:
        await update.message.reply_text("❌ Nessun risultato per questa query. Usa /start per riprovare.")
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
    logger.debug("[QueryPage] Offset=%d Totale=%d Mostrando %d carte", offset, total, len(cards))

    media = []
    for c in cards:
        img_url = c["image_uris"]["small"] if "image_uris" in c else c["card_faces"][0]["image_uris"]["small"]
        media.append(InputMediaPhoto(img_url, caption=c["name"]))
    await update.message.reply_media_group(media)

    keyboard = [[InlineKeyboardButton(c["name"], callback_data=f"querychoose:{c['id']}")] for c in cards]
    if offset + 5 < total:
        keyboard.append([InlineKeyboardButton("▶️ Altri 5", callback_data="querynext")])

    await update.message.reply_text(
        f"Risultati {offset+1}-{offset+len(cards)} di {total}",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_query_choice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    data = update.callback_query.data
    if data == "querynext":
        ctx.user_data["offset"] += 5
        logger.debug("[QueryNext] Offset aggiornato a %d", ctx.user_data["offset"])
        await send_query_page(update.callback_query, ctx)
        return
    cid = data.split(":", 1)[1]
    card = next((c for c in ctx.user_data["all_cards"] if c["id"] == cid), None)
    if card:
        logger.debug("[QueryChoose] Carta selezionata: %s", card['name'])
        await send_full_image(update.callback_query, card)
    else:
        logger.error("[QueryChoose] Carta con ID %s non trovata", cid)

# --- Invia immagine HD ---
async def send_full_image(source, card):
    if "image_uris" in card:
        url = card["image_uris"]["normal"]
    else:
        url = card["card_faces"][0]["image_uris"]["normal"]
    caption = f"{card['name']} — {card['set_name']}"
    logger.debug("[SendImage] Inviando immagine: %s", url)
    await source.message.reply_photo(url, caption=caption)

# --- Error handler ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("🚨 Exception gestita:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text("❌ Errore interno, riprova più tardi.")

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
app.add_handler(CallbackQueryHandler(handle_name_suggestion, pattern=r"^namesuggest:"))
app.add_handler(CallbackQueryHandler(handle_query_choice, pattern=r"^(querychoose:|querynext$)"))
app.add_error_handler(error_handler)

PORT = int(os.getenv("PORT", "8443"))
app.run_webhook(
    listen="0.0.0.0",
    port=PORT,
    url_path=TOKEN,
    webhook_url=f"https://{HOST}/{TOKEN}"
)