from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
)
from telegram.ext import CommandHandler, CallbackQueryHandler, ContextTypes
import requests
from urllib.parse import quote_plus, unquote_plus
import os

TOKEN = os.getenv("TELEGRAM_TOKEN")

# 1) Definiamo i campi da suggerire
FIELD_SUGGESTIONS = [
    ("tipo",    "type:creature"),
    ("colore",  "color:red"),
    ("set",     "set:khm"),
    ("rarità",  "rarity:mythic"),
    ("cmc≤",    "cmc<=3"),
    ("forza≥",  "pow>=6"),
]

# 2) Handler /ricerca unificato
async def ricerca(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = ' '.join(context.args).strip()
    if not query:
        # Mostriamo i suggerimenti di campo
        keyboard = [
            [InlineKeyboardButton(label, callback_data=f"field:{fld}")]
            for label, fld in FIELD_SUGGESTIONS
        ]
        markup = InlineKeyboardMarkup(keyboard)
        return await update.message.reply_text(
            "🧐 Inserisci parole chiave o scegli uno di questi campi:",
            reply_markup=markup
        )

    # Se ho argomenti, eseguo la ricerca con media-group e paginazione
    # (riutilizzo send_search_page e search_page_callback come descritto prima)
    resp = requests.get(
        "https://api.scryfall.com/cards/search",
        params={"q": query, "order": "relevance", "unique": "cards"}
    )
    data = resp.json()
    cards = data.get("data", [])[:5]
    total = data.get("total_cards", 0)
    await send_search_page(update, query, page_num=0, total=total, cards=cards, edit=False)

# 3) Quando l’utente tocca un campo, mostriamo un template di comando
async def field_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    field = update.callback_query.data.split(":",1)[1]
    await update.callback_query.message.reply_text(
        f"Esempio:\n/ricerca {field} <valore>\n"
        f"Puoi anche combinare più campi:\n"
        f"/ricerca {field} goblin"
    )




async def disclaimer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text
    if "lebbombe" in txt.upper():
        await update.message.reply_text("⚠️⚠️⚠️ALLARME LEBBOMBE⚠️⚠️⚠️")
    if "ironia" in txt.upper():
        await update.message.reply_text("⚠️⚠️⚠️ALLARME IRONIA⚠️⚠️⚠️")
    if "puntializzi" in txt.upper():
        await update.message.reply_text("⚠️⚠️⚠️ALLARME PUNTUALIZZATORE⚠️⚠️⚠️")
    if "puntualizzare" in txt.upper():
        await update.message.reply_text("⚠️⚠️⚠️ALLARME PUNTUALIZZATORE⚠️⚠️⚠️")
    if "scherzo" in txt.upper():
        await update.message.reply_text("⚠️⚠️⚠️ALLARME SCHERZO⚠️⚠️⚠️")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Vi servono lebbombe?")

# ------ HANDLER /ricerca con paginazione ------

from urllib.parse import quote_plus, unquote_plus

# Callback “▶️ Altri 5”
async def search_page_callback(update, context):
    _, q_enc, page_str = update.callback_query.data.split(":")
    q = unquote_plus(q_enc)
    page = int(page_str)

    r = requests.get("https://api.scryfall.com/cards/search",
                     params={"q": q, "order": "relevance", "unique": "cards", "page": page+1})
    data = r.json()
    cards = data.get("data", [])[:5]
    total = data.get("total_cards", 0)

    await update.callback_query.answer()
    await send_search_page(update, q, page_num=page, total=total, cards=cards, edit=True)



async def send_search_page(event, query: str, page_num: int, total: int, cards: list, edit: bool):
    # Prepara il media group
    media = []
    for i, card in enumerate(cards):
        # scegli l'URL giusto
        if "image_uris" in card:
            url = card["image_uris"]["small"]
        else:
            url = card["card_faces"][0]["image_uris"]["small"]
        # solo la prima foto ha caption con nome e set
        caption = (f"*{card['name']}* — _{card['set_name']}_") if i == 0 else None
        media.append(InputMediaPhoto(media=url, caption=caption, parse_mode="Markdown"))

    # Invia o aggiorna il media group
    if edit:
        await event.callback_query.edit_message_media(media=media)
    else:
        await event.message.reply_media_group(media)

    # Costruisci il messaggio di controllo paginazione
    start = page_num * 5 + 1
    end = start + len(cards) - 1
    text = f"Risultati {start}–{end} di {total} per *{query}*"
    buttons = []
    # Se ci sono altri risultati
    if end < total:
        buttons = [[
            InlineKeyboardButton(
                text="▶️ Altri 5",
                callback_data=f"search:{quote_plus(query)}:{page_num+1}"
            )
        ]]
    markup = InlineKeyboardMarkup(buttons) if buttons else None

    if edit:
        await event.callback_query.edit_message_caption(text, parse_mode="Markdown", reply_markup=markup)
    else:
        await event.message.reply_text(text, parse_mode="Markdown", reply_markup=markup)


# ------ HANDLER /cerca (named + autocomplete) con “Altri suggerimenti” ------

async def cerca(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = ' '.join(context.args)
    if not query:
        return await update.message.reply_text("🧐 Usa: /cerca <nome carta>")

    # tenta fuzzy named
    resp = requests.get(f"https://api.scryfall.com/cards/named?fuzzy={query}")
    if resp.status_code == 200:
        return await send_card(update, resp.json())

    # autocomplete
    ac = requests.get(f"https://api.scryfall.com/cards/autocomplete?q={query}")
    suggestions = ac.json().get("data", [])
    if not suggestions:
        return await update.message.reply_text("😕 Carta non trovata né suggerimenti.")

    # mostra prima pagina di suggerimenti
    await send_suggest_page(update, query, suggestions, offset=0, edit=False)

async def suggest_more_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data.split(":", 2)
    _, q_enc, off = data
    query = unquote_plus(q_enc)
    offset = int(off)

    # Ricava di nuovo tutti i suggerimenti
    ac = requests.get(f"https://api.scryfall.com/cards/autocomplete?q={query}")
    suggestions = ac.json().get("data", [])

    await update.callback_query.answer()
    await send_suggest_page(update, query, suggestions, offset, edit=True)


# -- Funzione rivista per mostrare SOLO bottoni con i nomi --
async def send_suggest_page(event, query: str, suggestions: list, offset: int, edit: bool):
    page = suggestions[offset: offset+5]

    # Costruiamo solo i bottoni
    keyboard = [
        [InlineKeyboardButton(text=name, callback_data=f"suggest:{name}")]
        for name in page
    ]

    # Se ci sono altri suggerimenti, aggiungiamo “Altri suggerimenti”
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
        await event.callback_query.edit_message_text(
            text,
            parse_mode="Markdown",
            reply_markup=markup
        )
    else:
        await event.message.reply_text(
            text,
            parse_mode="Markdown",
            reply_markup=markup
        )

async def suggestion_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    chosen = update.callback_query.data.split(":", 1)[1]
    resp = requests.get(f"https://api.scryfall.com/cards/named?exact={chosen}")
    if resp.status_code != 200:
        return await update.callback_query.message.reply_text("❌ Errore nel recupero.")
    await send_card(update.callback_query, resp.json(), use_query=True)


# ------ REUTILITY: send_card come prima ------

async def send_card(event_source, card, use_query: bool = False):
    caption = (
        f"*{card['name']}*"
    )
    
    if "image_uris" in card:
        coro = event_source.message.reply_photo(card["image_uris"]["normal"], caption=caption, parse_mode="Markdown")
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

# ------ BUILD & REGISTER HANDLERS ------

if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("find", ricerca))
    app.add_handler(CallbackQueryHandler(search_page_callback, pattern=r"^search:"))
    app.add_handler(CommandHandler("search", cerca))
    app.add_handler(CallbackQueryHandler(suggest_more_callback, pattern=r"^suggest_more:"))
    app.add_handler(CallbackQueryHandler(suggestion_callback, pattern=r"^suggest:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, disclaimer))
    app.add_handler(CallbackQueryHandler(field_callback, pattern=r"^field:"))
    app.add_handler(CommandHandler("ricerca", ricerca))

    PORT = int(os.environ.get("PORT", 8443))
    HOST = os.environ["RENDER_EXTERNAL_HOSTNAME"]
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=f"https://{HOST}/{TOKEN}"
    )