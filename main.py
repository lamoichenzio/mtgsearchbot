import os
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

# --- Config ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
HOST = os.environ.get("RENDER_EXTERNAL_HOSTNAME")

FIELD_SUGGESTIONS = [
    ("tipo",    "type:creature"),
    ("colore",  "color:red"),
    ("set",     "set:khm"),
    ("rarità",  "rarity:mythic"),
    ("cmc≤",    "cmc<=3"),
    ("forza≥",  "pow>=6"),
]

# --- /start ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Ciao! Sono il tuo Bot MTG.\n"
        "Usa /ricerca per cercare carte per parole chiave, o /cerca per nome esatto."
    )

# --- /ricerca con field suggestions e paginazione immagini ---
async def ricerca(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    if not query:
        keyboard = [
            [InlineKeyboardButton(label, callback_data=f"field:{fld}")]
            for label, fld in FIELD_SUGGESTIONS
        ]
        await update.message.reply_text(
            "🧐 Inserisci parole chiave o scegli uno di questi campi:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    resp = requests.get(
        "https://api.scryfall.com/cards/search",
        params={"q": query, "order": "relevance", "unique": "cards"}
    )
    data = resp.json()
    cards = data.get("data", [])[:5]
    total = data.get("total_cards", 0)
    await send_search_page(update, query, page_num=0, total=total, cards=cards, edit=False)

async def search_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _, q_enc, page_str = update.callback_query.data.split(":", 2)
    query = unquote_plus(q_enc)
    page_num = int(page_str)
    resp = requests.get(
        "https://api.scryfall.com/cards/search",
        params={
            "q": query,
            "order": "relevance",
            "unique": "cards",
            "page": page_num + 1
        }
    )
    data = resp.json()
    cards = data.get("data", [])[:5]
    total = data.get("total_cards", 0)
    await update.callback_query.answer()
    await send_search_page(update, query, page_num, total, cards, edit=True)

async def send_search_page(event, query, page_num, total, cards, edit):
    start = page_num * 5 + 1
    end = start + len(cards) - 1

    media = []
    for i, card in enumerate(cards):
        # seleziona URL della miniatura
        if "image_uris" in card:
            url = card["image_uris"]["small"]
        else:
            url = card["card_faces"][0]["image_uris"]["small"]
        caption = f"*{card['name']}* — _{card['set_name']}_" if i == 0 else None
        media.append(InputMediaPhoto(media=url, caption=caption, parse_mode="Markdown"))

    if edit:
        await event.callback_query.edit_message_media(media=media)
    else:
        await event.message.reply_media_group(media=media)

    text = f"Risultati {start}–{end} di {total} per *{query}*"
    buttons = []
    if end < total:
        buttons.append([
            InlineKeyboardButton(
                text="▶️ Altri 5",
                callback_data=f"search:{quote_plus(query)}:{page_num+1}"
            )
        ])
    markup = InlineKeyboardMarkup(buttons) if buttons else None

    if edit:
        await event.callback_query.edit_message_caption(
            text, parse_mode="Markdown", reply_markup=markup
        )
    else:
        await event.message.reply_text(
            text, parse_mode="Markdown", reply_markup=markup
        )

# --- callback per field suggestion ---
async def field_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    field = update.callback_query.data.split(":", 1)[1]
    await update.callback_query.message.reply_text(
        f"Ecco un esempio di utilizzo:\n"
        f"`/ricerca {field} <valore>`\n"
        f"Puoi combinare più campi, ad es:\n"
        f"`/ricerca {field} goblin`",
        parse_mode="Markdown"
    )

# --- /cerca con fuzzy + autocomplete + pulsanti di suggerimento ---
async def cerca(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    if not query:
        return await update.message.reply_text("🧐 Usa: /cerca <nome carta>")

    # prova fuzzy
    resp = requests.get(f"https://api.scryfall.com/cards/named?fuzzy={query}")
    if resp.status_code == 200:
        return await send_card(update, resp.json())

    # autocomplete
    ac = requests.get(f"https://api.scryfall.com/cards/autocomplete?q={query}")
    suggestions = ac.json().get("data", [])
    if not suggestions:
        return await update.message.reply_text("😕 Carta non trovata né suggerimenti.")
    await send_suggest_page(update, query, suggestions, offset=0, edit=False)

async def send_suggest_page(event, query, suggestions, offset, edit):
    page = suggestions[offset : offset + 5]
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
    text = f"Suggerimenti per `{query}` (mostrati {offset+1}–{offset+len(page)}):"
    if edit:
        await event.callback_query.edit_message_text(text, reply_markup=markup)
    else:
        await event.message.reply_text(text, reply_markup=markup)

async def suggest_more_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _, q_enc, off = update.callback_query.data.split(":", 2)
    query = unquote_plus(q_enc)
    offset = int(off)
    ac = requests.get(f"https://api.scryfall.com/cards/autocomplete?q={query}")
    suggestions = ac.json().get("data", [])
    await update.callback_query.answer()
    await send_suggest_page(update, query, suggestions, offset, edit=True)

async def suggestion_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    chosen = update.callback_query.data.split(":", 1)[1]
    resp = requests.get(f"https://api.scryfall.com/cards/named?exact={chosen}")
    if resp.status_code != 200:
        return await update.callback_query.message.reply_text("❌ Errore nel recupero della carta.")
    await send_card(update.callback_query, resp.json(), use_query=True)

async def send_card(event_source, card, use_query=False):
    caption = (
        f"*{card['name']}* — _{card['set_name']}_\n"
        f"Mana: `{card.get('mana_cost','')}`  Rarity: `{card['rarity']}`"
    )
    if "image_uris" in card:
        coro = event_source.message.reply_photo(
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
        coro = event_source.message.reply_media_group(media)
    await coro

# --- Main & Webhook Setup ---
if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()

    # comandi
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ricerca", ricerca))
    app.add_handler(CallbackQueryHandler(field_callback, pattern=r"^field:"))
    app.add_handler(CallbackQueryHandler(search_page_callback, pattern=r"^search:"))
    app.add_handler(CommandHandler("cerca", cerca))
    app.add_handler(CallbackQueryHandler(suggest_more_callback, pattern=r"^suggest_more:"))
    app.add_handler(CallbackQueryHandler(suggestion_callback, pattern=r"^suggest:"))

    # webhook
    PORT = int(os.environ.get("PORT", 8443))
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=f"https://{HOST}/{TOKEN}"
    )