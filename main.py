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

# --- Logging setup ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.DEBUG
)
logger = logging.getLogger(__name__)

# --- Config ---
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

# --- Error handler ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("🚨 Exception:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text("❌ Errore interno, riprova più tardi.")

# --- /start ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("Received /start")
    await update.message.reply_text(
        "👋 Ciao! Bot MTG attivo.\n"
        "• `/ricerca` per ricerca parole chiave\n"
        "• `/cerca <nome>` per lookup fuzzy/autocomplete",
        parse_mode="Markdown"
    )

# --- /ricerca ---
async def ricerca(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    if not query:
        keyboard = [
            [InlineKeyboardButton(label, callback_data=f"field:{fld}")]
            for label, fld in FIELD_SUGGESTIONS
        ]
        await update.message.reply_text(
            "🧐 Inserisci parole chiave o scegli un campo:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    logger.info("Ricerca full-text: %s", query)
    resp = requests.get("https://api.scryfall.com/cards/search",
                        params={"q": query, "order": "relevance", "unique": "cards"})
    data = resp.json()
    total = data.get("total_cards", 0)
    cards = data.get("data", [])[:5]
    logger.debug("Found %d cards, sending first 5", total)

    await send_search_page(
        context=context,
        chat_id=update.effective_chat.id,
        query=query,
        offset=0,
        total=total,
        cards=cards
    )

# --- Callback /ricerca page ---
async def search_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    _, q_enc, off = update.callback_query.data.split(":", 2)
    query = unquote_plus(q_enc)
    offset = int(off)
    logger.info("Paginating search %s offset=%d", query, offset)

    page_num = offset // 175 + 1
    resp = requests.get("https://api.scryfall.com/cards/search",
                        params={"q": query, "order": "relevance", "unique": "cards", "page": page_num})
    data = resp.json()
    total = data.get("total_cards", 0)
    all_cards = data.get("data", [])
    start_in_page = offset % 175
    cards = all_cards[start_in_page:start_in_page+5]
    logger.debug("Slicing cards %d to %d of %d", start_in_page, start_in_page+5, len(all_cards))

    await send_search_page(context, update.effective_chat.id, query, offset, total, cards)

# --- Sending paginated cards ---
async def send_search_page(context: ContextTypes.DEFAULT_TYPE, chat_id: int,
                           query: str, offset: int, total: int, cards: list):
    if not cards:
        await context.bot.send_message(chat_id, f"😕 Nessun altro risultato per «{query}».")
        return

    start = offset+1
    end = offset+len(cards)
    logger.debug("Sending cards %d–%d of %d", start, end, total)

    media = []
    for i, card in enumerate(cards):
        url = card["image_uris"]["small"] if "image_uris" in card else card["card_faces"][0]["image_uris"]["small"]
        caption = f"*{card['name']}* — _{card['set_name']}_" if i == 0 else None
        media.append(InputMediaPhoto(url, caption=caption, parse_mode="Markdown"))
    await context.bot.send_media_group(chat_id=chat_id, media=media)

    buttons = []
    if end < total:
        buttons = [[InlineKeyboardButton("▶️ Altri 5", callback_data=f"search:{quote_plus(query)}:{offset+5}")]]
    markup = InlineKeyboardMarkup(buttons) if buttons else None

    text = f"Risultati {start}–{end} di {total} per *{query}*"
    await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown", reply_markup=markup)

# --- Field callback ---
async def field_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    field = update.callback_query.data.split(":",1)[1]
    await update.callback_query.message.reply_text(f"Esempio:\n/ricerca {field} <valore>\n")

# --- /cerca ---
async def cerca(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    if not query:
        return await update.message.reply_text("🧐 Usa: /cerca <nome carta>")

    logger.info("Fuzzy lookup for %s", query)
    resp = requests.get(f"https://api.scryfall.com/cards/named?fuzzy={query}")
    if resp.status_code == 200:
        return await send_card(update, resp.json())

    logger.debug("Fuzzy failed, using autocomplete")
    ac = requests.get(f"https://api.scryfall.com/cards/autocomplete?q={query}")
    suggestions = ac.json().get("data", [])
    if not suggestions:
        return await update.message.reply_text("😕 Carta non trovata né suggerimenti.")
    await send_suggest_page(update, query, suggestions, offset=0)

# --- Send suggestions page ---
async def send_suggest_page(update: Update, query: str, suggestions: list, offset: int):
    page = suggestions[offset:offset+5]
    keyboard = [[InlineKeyboardButton(name, callback_data=f"suggest:{name}")] for name in page]
    if offset+5 < len(suggestions):
        keyboard.append([InlineKeyboardButton("▶️ Altri suggerimenti", callback_data=f"suggest_more:{quote_plus(query)}:{offset+5}")])
    markup = InlineKeyboardMarkup(keyboard)
    text = f"Suggerimenti {offset+1}–{offset+len(page)} per `{query}`:"
    await context_bot_send(update, text, markup)

# --- Callback for suggest more ---
async def suggest_more_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    _, q_enc, off = update.callback_query.data.split(":",2)
    query = unquote_plus(q_enc)
    offset = int(off)
    logger.info("Paginating suggestions %s offset=%d", query, offset)
    ac = requests.get(f"https://api.scryfall.com/cards/autocomplete?q={query}")
    suggestions = ac.json().get("data", [])
    await send_suggest_page(update, query, suggestions, offset)

# --- Callback for suggestion click ---
async def suggestion_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    name = update.callback_query.data.split(":",1)[1]
    resp = requests.get(f"https://api.scryfall.com/cards/named?exact={name}")
    if resp.status_code != 200:
        return await context.bot.send_message(update.effective_chat.id, "❌ Errore fetch carta.")
    await send_card(update, resp.json())

# --- Helper to send message from Update ---
async def context_bot_send(update: Update, text: str, markup):
    chat_id = update.effective_chat.id
    await update.message.reply_text(text, reply_markup=markup) if update.message else await context.bot.send_message(chat_id, text=text, reply_markup=markup)

# --- Send single card ---
async def send_card(update: Update, card: dict):
    caption = f"*{card['name']}* — _{card['set_name']}_\nMana: `{card.get('mana_cost','')}` Rarity: `{card['rarity']}`"
    if "image_uris" in card:
        await update.message.reply_photo(card["image_uris"]["normal"], caption=caption, parse_mode="Markdown")
    else:
        media = [InputMediaPhoto(face["image_uris"]["normal"], caption=caption if i==0 else None, parse_mode="Markdown") for i,face in enumerate(card["card_faces"])]
        await update.message.reply_media_group(media)

# --- Main ---
if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ricerca", ricerca))
    app.add_handler(CallbackQueryHandler(field_callback, pattern=r"^field:"))
    app.add_handler(CallbackQueryHandler(search_page_callback, pattern=r"^search:"))
    app.add_handler(CommandHandler("cerca", cerca))
    app.add_handler(CallbackQueryHandler(suggest_more_callback, pattern=r"^suggest_more:"))
    app.add_handler(CallbackQueryHandler(suggestion_callback, pattern=r"^suggest:"))
    app.add_error_handler(error_handler)

    PORT = int(os.getenv("PORT", "8443"))
    app.run_webhook(listen="0.0.0.0", port=PORT, url_path=TOKEN, webhook_url=f"https://{HOST}/{TOKEN}")