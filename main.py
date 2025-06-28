import os
import requests
from aiohttp import web
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

TOKEN = os.getenv("TELEGRAM_TOKEN")

# --- Handlers Telegram ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Ciao! Bot MTG pronto!")

async def cerca(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = ' '.join(context.args)
    if not query:
        return await update.message.reply_text("🧐 Usa: /cerca <nome carta>")
    resp = requests.get(f"https://api.scryfall.com/cards/named?fuzzy={query}")
    if resp.status_code != 200:
        return await update.message.reply_text("😕 Carta non trovata.")
    card = resp.json()
    caption = (
        f"*{card['name']}* — _{card['set_name']}_\n"
        f"Mana: `{card.get('mana_cost','')}`\n"
        f"Rarity: `{card['rarity']}`"
    )
    await update.message.reply_photo(
        card['image_uris']['normal'],
        caption=caption,
        parse_mode="Markdown"
    )

# --- Health check per uptime ping ---

async def healthz(request):
    return web.Response(text="OK")

# --- Build dell’app e registrazione handler ---

app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("cerca", cerca))

# Aggiungi la route per /healthz
app.router.add_get("/healthz", healthz)

# --- Avvio del webhook ---

if __name__ == "__main__":
    PORT = int(os.environ.get("PORT", 8443))
    HOSTNAME = os.environ["RENDER_EXTERNAL_HOSTNAME"]
    # url_path fa corrispondere il percorso del token al webhook di Telegram
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=f"https://{HOSTNAME}/{TOKEN}"
    )