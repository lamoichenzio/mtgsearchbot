import os
import requests
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

TOKEN = os.getenv("TELEGRAM_TOKEN")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Ciao! Bot MTG pronto!")

async def cerca(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = ' '.join(context.args)
    if not query:
        return await update.message.reply_text("🧐 Usa: /cerca <nome carta>")

    # Proviamo prima la ricerca fuzzy “named”
    resp = requests.get(f"https://api.scryfall.com/cards/named?fuzzy={query}")
    if resp.status_code == 200:
        card = resp.json()
        return await send_card(update, card)

    # Se non trovata, chiediamo i suggerimenti
    ac = requests.get(f"https://api.scryfall.com/cards/autocomplete?q={query}")
    if ac.status_code == 200 and ac.json().get("data"):
        suggestions = ac.json()["data"][:5]  # ne prendiamo max 5
        keyboard = [
            [InlineKeyboardButton(text=s, callback_data=f"suggest:{s}")]
            for s in suggestions
        ]
        markup = InlineKeyboardMarkup(keyboard)
        return await update.message.reply_text(
            "❓ Carta non trovata. Forse intendevi:",
            reply_markup=markup
        )

    # Nessun suggerimento
    await update.message.reply_text("😕 Carta non trovata e nessun suggerimento disponibile.")

async def suggestion_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # L’utente ha cliccato uno dei suggerimenti
    await update.callback_query.answer()  # “ack” al callback
    chosen = update.callback_query.data.split(":", 1)[1]

    # Rifacciamo una ricerca “named” con il nome scelto
    resp = requests.get(f"https://api.scryfall.com/cards/named?exact={chosen}")
    if resp.status_code == 200:
        card = resp.json()
        # Inviamo la carta nella chat principale
        await send_card(update.callback_query, card, use_query=True)
    else:
        await update.callback_query.message.reply_text(
            "❌ Errore nel recuperare la carta selezionata."
        )

async def send_card(event_source, card, use_query: bool = False):
    """
    Se use_query=False, event_source è un Update (per .message.reply_*).
    Se True, event_source è un CallbackQuery (per .message.reply_*).
    """
    img = card["image_uris"]["normal"]
    caption = (
        f"*{card['name']}* — _{card['set_name']}_\n"
        f"Mana: `{card.get('mana_cost','')}`  Rarity: `{card['rarity']}`"
    )
    if use_query:
        await event_source.message.reply_photo(img, caption=caption, parse_mode="Markdown")
    else:
        await event_source.message.reply_photo(img, caption=caption, parse_mode="Markdown")

if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cerca", cerca))
    app.add_handler(CallbackQueryHandler(suggestion_callback, pattern=r"^suggest:"))

    PORT = int(os.environ.get("PORT", 8443))
    HOST = os.environ["RENDER_EXTERNAL_HOSTNAME"]
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=f"https://{HOST}/{TOKEN}"
    )