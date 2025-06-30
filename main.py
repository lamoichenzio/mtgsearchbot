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

# --- Configurazione logging (DEBUG per verbosità) ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.DEBUG
)
logger = logging.getLogger(__name__)

# --- Variabili d'ambiente ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
HOST  = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "")

# --- Field suggestions per /ricerca senza argomenti ---
FIELD_SUGGESTIONS = [
    ("tipo",    "type:creature"),
    ("colore",  "color:red"),
    ("set",     "set:khm"),
    ("rarità",  "rarity:mythic"),
    ("cmc≤",    "cmc<=3"),
    ("forza≥",  "pow>=6"),
]

# --- Error handler globale ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("🚨 Exception while handling update:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text(
            "❌ Si è verificato un errore interno."
        )

# --- /start ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("Ricevuto /start da chat_id=%s", update.effective_chat.id)
    await update.message.reply_text(
        "👋 Ciao! Bot MTG attivo.\n"
        "• `/ricerca` per ricerca per parole chiave\n"
        "• `/cerca <nome>` per lookup fuzzy/autocomplete",
        parse_mode="Markdown"
    )

# --- /ricerca con field suggestions e paginazione immagini ---
async def ricerca(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    if not query:
        # Mostra i campi disponibili
        logger.debug("Mostro field suggestions")
        keyboard = [
            [InlineKeyboardButton(label, callback_data=f"field:{fld}")]
            for label, fld in FIELD_SUGGESTIONS
        ]
        await update.message.reply_text(
            "🧐 Inserisci parole chiave o scegli uno di questi campi:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    # Fai la chiamata full‐text
    logger.info("Avvio ricerca full-text per '%s'", query)
    resp = requests.get(
        "https://api.scryfall.com/cards/search",
        params={"q": query, "order": "relevance", "unique": "cards"}
    )
    data = resp.json()
    total = data.get("total_cards", 0)
    cards = data.get("data", [])[:5]
    logger.debug("Trovate %d carte totali, invio 5 carte", total)

    # Prima pagina: offset = 0
    await send_search_page(
        event=update,
        query=query,
        offset=0,
        total=total,
        cards=cards,
        edit=False
    )

# --- Callback “Altri 5” per /ricerca ---
async def search_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _, q_enc, off = update.callback_query.data.split(":", 2)
    query  = unquote_plus(q_enc)
    offset = int(off)
    logger.info("Callback ricerca: '%s', offset=%d", query, offset)
    await update.callback_query.answer()

    # Quale pagina di Scryfall? ogni page = 175 carte
    scry_page = offset // 175 + 1
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

    # Calcola i 5 da mostrare
    start_in_page = offset % 175
    cards = all_cards[start_in_page : start_in_page + 5]
    logger.debug("Estrazione slice %d:%d su %d carte della pagina",
                 start_in_page, start_in_page+5, len(all_cards))

    # Invia / edita
    await send_search_page(
        event=update.callback_query,
        query=query,
        offset=offset,
        total=total,
        cards=cards,
        edit=True
    )

async def send_search_page(event, query, offset, total, cards, edit):
    # Se non ci sono più carte
    if not cards:
        msg = f"😕 Non ci sono altri risultati per «{query}»."
        if edit:
            return await event.edit_message_text(msg)
        else:
            return await event.message.reply_text(msg)

    # Indici per UI
    start = offset + 1
    end   = offset + len(cards)
    logger.debug("Invio media group %d–%d di %d", start, end, total)

    # 1) Miniature
    media = []
    for i, card in enumerate(cards):
        if "image_uris" in card:
            url = card["image_uris"]["small"]
        else:
            url = card["card_faces"][0]["image_uris"]["small"]
        caption = f"*{card['name']}* — _{card['set_name']}_" if i == 0 else None
        media.append(InputMediaPhoto(media=url, caption=caption, parse_mode="Markdown"))

    # 2) Manda sempre un nuovo media_group
    if isinstance(event, Update):
        await event.message.reply_media_group(media)
    else:
        # event è un CallbackQuery
        await event.message.reply_media_group(media)

    # 3) Costruisci testo + pulsante
    text = f"Risultati {start}–{end} di {total} per *{query}*"
    buttons = []
    if end < total:
        buttons = [[
            InlineKeyboardButton(
                "▶️ Altri 5",
                callback_data=f"search:{quote_plus(query)}:{offset+5}"
            )
        ]]
    markup = InlineKeyboardMarkup(buttons) if buttons else None

    # 4) Invia o edita SOLO il messaggio di testo
    if edit:
        await event.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)
    else:
        await event.message.reply_text(text, parse_mode="Markdown", reply_markup=markup)

# --- Callback per field suggestion ---
async def field_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    field = update.callback_query.data.split(":",1)[1]
    logger.info("Field selezionato: %s", field)
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        f"Esempio:\n/ricerca {field} <valore>\n"
        f"Puoi anche combinare più campi: /ricerca {field} goblin"
    )

# --- /cerca fuzzy + autocomplete con suggeritori paginati ---
async def cerca(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    logger.info("Ricevuto /cerca '%s'", query)
    if not query:
        return await update.message.reply_text("🧐 Usa: /cerca <nome carta>")

    # Fuzzy lookup
    resp = requests.get(f"https://api.scryfall.com/cards/named?fuzzy={query}")
    if resp.status_code == 200:
        return await send_card(update, resp.json())

    # Autocomplete
    ac_s = requests.get(f"https://api.scryfall.com/cards/autocomplete?q={query}")
    suggestions = ac_s.json().get("data", [])
    if not suggestions:
        return await update.message.reply_text("😕 Carta non trovata né suggerimenti.")
    await send_suggest_page(update, query, suggestions, offset=0, edit=False)

async def send_suggest_page(event, query, suggestions, offset, edit):
    page = suggestions[offset : offset+5]
    keyboard = [
        [InlineKeyboardButton(n, callback_data=f"suggest:{n}")]
        for n in page
    ]
    if offset + 5 < len(suggestions):
        keyboard.append([
            InlineKeyboardButton(
                "▶️ Altri suggerimenti",
                callback_data=f"suggest_more:{quote_plus(query)}:{offset+5}"
            )
        ])
    markup = InlineKeyboardMarkup(keyboard)
    text = f"Suggerimenti per `{query}`:"
    if edit:
        await event.edit_message_text(text, reply_markup=markup)
    else:
        await event.message.reply_text(text, reply_markup=markup)

async def suggest_more_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _, q_enc, off = update.callback_query.data.split(":", 2)
    query = unquote_plus(q_enc)
    offset = int(off)
    logger.info("Pagina suggerimenti %s offset=%d", query, offset)
    ac_s = requests.get(f"https://api.scryfall.com/cards/autocomplete?q={query}")
    suggestions = ac_s.json().get("data", [])
    await update.callback_query.answer()
    await send_suggest_page(update, query, suggestions, offset, edit=True)

async def suggestion_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.callback_query.data.split(":",1)[1]
    logger.info("Suggerimento scelto: %s", name)
    await update.callback_query.answer()
    resp = requests.get(f"https://api.scryfall.com/cards/named?exact={name}")
    if resp.status_code != 200:
        return await update.callback_query.message.reply_text("❌ Errore recupero carta.")
    await send_card(update.callback_query, resp.json(), use_query=True)

async def send_card(event, card, use_query=False):
    caption = (
        f"*{card['name']}* — _{card['set_name']}_\n"
        f"Mana: `{card.get('mana_cost','')}`  Rarity: `{card['rarity']}`"
    )
    if "image_uris" in card:
        await event.message.reply_photo(card["image_uris"]["normal"],
            caption=caption, parse_mode="Markdown")
    else:
        media = []
        for i, face in enumerate(card["card_faces"]):
            media.append(InputMediaPhoto(
                media=face["image_uris"]["normal"],
                caption=caption if i==0 else None,
                parse_mode="Markdown"
            ))
        await event.message.reply_media_group(media)

# --- Setup e avvio webhook ---
if __name__ == "__main__":
    logger.info("Avvio bot e configuro webhook su Render...")
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start",      start))
    app.add_handler(CommandHandler("ricerca",    ricerca))
    app.add_handler(CallbackQueryHandler(field_callback, pattern=r"^field:"))
    app.add_handler(CallbackQueryHandler(search_page_callback, pattern=r"^search:"))
    app.add_handler(CommandHandler("cerca",      cerca))
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