import os, requests
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
    MessageHandler,
    filters,
)

TOKEN = os.getenv("TELEGRAM_TOKEN")

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

async def ricerca(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = ' '.join(context.args)
    if not query:
        return await update.message.reply_text("🧐 Usa: /ricerca <keywords>")

    # genera la prima pagina (offset=0)
    await send_search_page(update, query, offset=0, edit=False)

async def search_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # callback_data = "search:<query_enc>:<offset>"
    data = update.callback_query.data.split(":", 2)
    _, q_enc, off = data
    query = unquote_plus(q_enc)
    offset = int(off)
    await update.callback_query.answer()
    await send_search_page(update, query, offset, edit=True)

async def send_search_page(event, query: str, offset: int, edit: bool):
    # chiama Scryfall search
    resp = requests.get(
        "https://api.scryfall.com/cards/search",
        params={"q": query, "order": "relevance", "unique": "cards", "page": offset//5 + 1}
    )
    data = resp.json().get("data", [])
    if not data:
        text = f"😕 Nessuna carta trovata per “{query}”."
        if edit:
            return await event.callback_query.edit_message_text(text)
        else:
            return await event.message.reply_text(text)

    # Prendi 5 risultati da offset
    slice_ = data[offset % 5: offset % 5 + 5]
    lines = [f"*{c['name']}* — _{c['set_name']}_" for c in slice_]
    text = f"Risultati {offset+1}–{offset+len(slice_)} per *{query}*:\n" + "\n".join(lines)

    # Inline keyboard
    keyboard = []
    # Bottone dettaglio: ogni riga potrebbe avere il suo bottone ma per semplicità no
    # Paginazione
    total = resp.json().get("total_cards", len(data))
    next_offset = offset + 5
    if next_offset < total:
        keyboard.append([
            InlineKeyboardButton(
                text="▶️ Altri 5",
                callback_data=f"search:{quote_plus(query)}:{next_offset}"
            )
        ])

    markup = InlineKeyboardMarkup(keyboard) if keyboard else None

    if edit:
        await event.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)
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
    # callback_data = "suggest_more:<query_enc>:<offset>"
    data = update.callback_query.data.split(":", 2)
    _, q_enc, off = data
    query = unquote_plus(q_enc)
    offset = int(off)

    # ricava di nuovo la lista completa di suggerimenti
    ac = requests.get(f"https://api.scryfall.com/cards/autocomplete?q={query}")
    suggestions = ac.json().get("data", [])
    await update.callback_query.answer()
    await send_suggest_page(update, query, suggestions, offset, edit=True)

async def send_suggest_page(event, query: str, suggestions: list, offset: int, edit: bool):
    page = suggestions[offset: offset+5]
    lines = [f"{i+offset+1}. {name}" for i, name in enumerate(page)]
    text = f"Suggerimenti per `{query}` ({offset+1}-{offset+len(page)}):\n" + "\n".join(lines)

    keyboard = [
        [InlineKeyboardButton(text=f"{i+offset+1}", callback_data=f"suggest:{name}")]
        for i, name in enumerate(page)
    ]
    # se ci sono altri suggerimenti
    if offset + 5 < len(suggestions):
        keyboard.append([
            InlineKeyboardButton(
                text="▶️ Altri suggerimenti",
                callback_data=f"suggest_more:{quote_plus(query)}:{offset+5}"
            )
        ])
    markup = InlineKeyboardMarkup(keyboard)

    if edit:
        await event.callback_query.edit_message_text(text, reply_markup=markup)
    else:
        await event.message.reply_text(text, reply_markup=markup)

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
        f"*{card['name']}* — _{card['set_name']}_\n"
        f"Mana: `{card.get('mana_cost','')}`  Rarity: `{card['rarity']}`"
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

    PORT = int(os.environ.get("PORT", 8443))
    HOST = os.environ["RENDER_EXTERNAL_HOSTNAME"]
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=f"https://{HOST}/{TOKEN}"
    )