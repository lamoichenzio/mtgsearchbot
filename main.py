import os
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

TOKEN = os.getenv("TELEGRAM_TOKEN")  # prendi il token dalle variabili d'ambiente

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Ciao! Bot MTG pronto!")

async def card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Funzione ricerca carte in arrivo...")

app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("cerca", card))

if __name__ == "__main__":
    # Per Render dobbiamo avviare un webserver (webhook)
    PORT = int(os.environ.get('PORT', 8443))
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        webhook_url=f"https://{os.environ['RENDER_EXTERNAL_HOSTNAME']}/{TOKEN}"
    )