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
)

# --- Config logging ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.DEBUG  # DEBUG per massima verbosità
)
logger = logging.getLogger(__name__)

# --- Config bot ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
HOST = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "")
FIELD_SUGGESTIONS = [
    ("tipo",    "type:creature"),
    ("colore",  "color:red"),
    ("set",     "set:khm"),
    ("rarità",  "rarity:mythic"),
    ("cmc≤",    "cmc<=3"),
    ("forza≥",  "pow>=6"),
]

# --- Error handler generale ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("🚨 Exception while handling update:", exc_info=context.error)
    # Rispondiamo all'utente in chat se possibile
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text(
            "❌ Si è verificato un errore interno. Sto cercando di risolverlo!"
        )

# --- /start ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("Ricevuto /start da chat_id=%s", update.effective_chat.id)
    await update.message.reply_text(
        "👋 Ciao! Sono il tuo Bot MTG.\n"
        "Usa /ricerca per cercare carte per parole chiave, o /cerca per nome esatto."
    )

# --- /ricerca con field suggestions e paginazione immagini ---
async def ricerca(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    chat_id = update.effective_chat.id
    if not query:
        logger.debug("Mostro field suggestions a chat_id=%s", chat_id)
        keyboard = [
            [InlineKeyboardButton(label, callback_data=f"field:{fld}")]
            for label, fld in FIELD_SUGGESTIONS
        ]
        return await update.message.reply_text(
            "🧐 Inserisci parole chiave o scegli uno di questi campi:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    logger.info("Avvio ricerca Scryfall per '%s' (chat_id=%s)", query, chat_id)
    resp = requests.get(
        "https://api.scryfall.com/cards/search",
        params={"q": query, "order": "relevance", "unique": "cards"}
    )
    data = resp.json()
    cards = data.get("data", [])[:5]
    total = data.get("total_cards", 0)
    logger.debug("Trovate %d carte per '%s'", total, query)
    await send_search_page(update, query, offset=0, total=total, cards=cards, edit=False)

async def search_page_callback(update, context):
    # callback_data = "search:<query_enc>:<offset>"
    _, q_enc, off = update.callback_query.data.split(":", 2)
    query  = unquote_plus(q_enc)
    offset = int(off)
    await update.callback_query.answer()

    # Calcolo quale pagina di Scryfall (ogni pagina = 175 carte)
    scry_page = offset // 175 + 1

    # Chiamo Scryfall solo su quella pagina
    resp = requests.get(
        "https://api.scryfall.com/cards/search",
        params={
            "q": query,
            "order": "relevance",
            "unique": "cards",
            "page": scry_page
        }
    )
    data      = resp.json()
    total     = data.get("total_cards", 0)
    all_cards = data.get("data", [])

    # Calcolo i 5 che mi servono, partendo dall'offset
    start_in_page = offset % 175
    cards = all_cards[start_in_page : start_in_page + 5]

    # Inoltra a send_search_page (edit=True perché è un callback)
    await send_search_page(update.callback_query, query, offset=offset, total=total, cards=cards, edit=True)



async def send_search_page(event, query, offset, total, cards, edit):
    # Se non ci sono carte
    if not cards:
        msg = f"😕 Non ci sono altri risultati per «{query}»."
        if edit:
            return await event.edit_message_text(msg)
        else:
            return await event.message.reply_text(msg)

    start = offset + 1
    end   = offset + len(cards)

    # --- 1) costruisci il media group ---
    media = []
    for i, card in enumerate(cards):
        url = (
            card["image_uris"]["small"]
            if "image_uris" in card
            else card["card_faces"][0]["image_uris"]["small"]
        )
        caption = f"*{card['name']}* — _{card['set_name']}_" if i == 0 else None
        media.append(InputMediaPhoto(media=url, caption=caption, parse_mode="Markdown"))

    # --- 2) in base a edit, invia un nuovo media group ---
    if edit:
        # qui non usiamo edit_message_media
        await event.message.reply_media_group(media)
    else:
        await event.message.reply_media_group(media)

    # --- 3) prepara il testo di paginazione ---
    text = f"Risultati {start}–{end} di {total} per *{query}*"
    buttons = []
    if end < total:
        buttons = [[
            InlineKeyboardButton(
                text="▶️ Altri 5",
                callback_data=f"search:{quote_plus(query)}:{offset + 5}"
            )
        ]]
    markup = InlineKeyboardMarkup(buttons) if buttons else None

    # --- 4) edita o invia il messaggio di testo con la tastiera ---
    if edit:
        await event.edit_message_caption(text, parse_mode="Markdown", reply_markup=markup)
    else:
        await event.message.reply_text(text, parse_mode="Markdown", reply_markup=markup)

# --- callback per field suggestion ---
async def field_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    field = update.callback_query.data.split(":", 1)[1]
    logger.info("Field suggestion selezionato: %s", field)
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        f"Esempio:\n/ricerca {field} <valore>\n"
        f"Puoi combinarlo con altre parole."
    )

# --- /cerca con fuzzy + autocomplete + suggerimenti paginati ---
async def cerca(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    logger.info("Ricevuto /cerca '%s'", query)
    if not query:
        return await update.message.reply_text("🧐 Usa: /cerca <nome carta>")

    resp = requests.get(f"https://api.scryfall.com/cards/named?fuzzy={query}")
    if resp.status_code == 200:
        logger.debug("Fuzzy match trovato per '%s'", query)
        return await send_card(update, resp.json())

    logger.debug("Fuzzy non trovato, chiamo autocomplete per '%s'", query)
    ac = requests.get(f"https://api.scryfall.com/cards/autocomplete?q={query}")
    suggestions = ac.json().get("data", [])
    if not suggestions:
        logger.warning("Nessun suggerimento per '%s'", query)
        return await update.message.reply_text("😕 Carta non trovata né suggerimenti.")
    await send_suggest_page(update, query, suggestions, offset=0, edit=False)

async def send_suggest_page(event, query, suggestions, offset, edit):
    page = suggestions[offset : offset + 5]
    logger.debug("Mostro suggerimenti %d–%d per '%s'", offset+1, offset+len(page), query)
    keyboard = [
        [InlineKeyboardButton(text=name, callback_data=f"suggest:{name}")]
        for name in page
    ]
    if offset + 5 < len(suggestions):
        keyboard.append([
            InlineKeyboardButton(
                text="▶️ Altri suggerimenti",
                callback_data=f"suggest_more:{quote_plus(query)}:{offset+5}"
            )
        ])
    markup = InlineKeyboardMarkup(keyboard)
    text = f"Suggerimenti per `{query}`:"
    if edit:
        await event.callback_query.edit_message_text(text, reply_markup=markup)
    else:
        await event.message.reply_text(text, reply_markup=markup)

async def suggest_more_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _, q_enc, off = update.callback_query.data.split(":", 2)
    query = unquote_plus(q_enc)
    offset = int(off)
    logger.info("Pagina successiva suggerimenti: '%s' offset=%d", query, offset)
    ac = requests.get(f"https://api.scryfall.com/cards/autocomplete?q={query}")
    suggestions = ac.json().get("data", [])
    await update.callback_query.answer()
    await send_suggest_page(update, query, suggestions, offset, edit=True)

async def suggestion_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chosen = update.callback_query.data.split(":", 1)[1]
    logger.info("Consiglio selezionato: %s", chosen)
    await update.callback_query.answer()
    resp = requests.get(f"https://api.scryfall.com/cards/named?exact={chosen}")
    if resp.status_code != 200:
        logger.error("Errore recupero esatto per %s", chosen)
        return await update.callback_query.message.reply_text(
            "❌ Errore nel recupero della carta."
        )
    await send_card(update.callback_query, resp.json(), use_query=True)

async def send_card(event_source, card, use_query=False):
    logger.debug("Invio carta %s", card.get("name"))
    caption = (
        f"*{card['name']}* — _{card['set_name']}_\n"
        f"Mana: `{card.get('mana_cost','')}`  Rarity: `{card['rarity']}`"
    )
    if "image_uris" in card:
        await event_source.message.reply_photo(
            card["image_uris"]["normal"], caption=caption, parse_mode="Markdown"
        )
    else:
        media = []
        for i, face in enumerate(card["card_faces"]):
            media.append(InputMediaPhoto(
                media=face["image_uris"]["normal"],
                caption=caption if i == 0 else None,
                parse_mode="Markdown"
            ))
        await event_source.message.reply_media_group(media)

# --- Avvio bot & Webhook ---
if __name__ == "__main__":
    logger.info("Avvio bot su Render, impostando webhook...")
    app = ApplicationBuilder().token(TOKEN).build()

    # Registrazione comandi e callback
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ricerca", ricerca))
    app.add_handler(CallbackQueryHandler(field_callback, pattern=r"^field:"))
    app.add_handler(CallbackQueryHandler(search_page_callback, pattern=r"^search:"))
    app.add_handler(CommandHandler("cerca", cerca))
    app.add_handler(CallbackQueryHandler(suggest_more_callback, pattern=r"^suggest_more:"))
    app.add_handler(CallbackQueryHandler(suggestion_callback, pattern=r"^suggest:"))
    app.add_error_handler(error_handler)

    PORT = int(os.environ.get("PORT", 8443))
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=f"https://{HOST}/{TOKEN}"
    )
    logger.info("Bot avviato e in ascolto su https://%s/%s", HOST, TOKEN)