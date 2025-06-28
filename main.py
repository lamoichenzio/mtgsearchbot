import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# Funzione che cerca le carte su Scryfall
def search_card(query):
    url = f"https://api.scryfall.com/cards/named?fuzzy={query}"
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        return f"{data['name']} - {data['set_name']}\n{data['image_uris']['normal']}"
    else:
        return "Carta non trovata."

# Comando /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Ciao! Scrivimi il nome di una carta Magic.")

# Comando di ricerca carte
async def card_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = ' '.join(context.args)
    if not query:
        await update.message.reply_text("Scrivi il nome di una carta dopo il comando /cerca")
        return
    result = search_card(query)
    await update.message.reply_text(result)

if __name__ == '__main__':
    # Inserisci qui il token del tuo bot
    application = ApplicationBuilder().token('7882279505:AAGEGOygm27Vw5a1_tqzPUGa97Wf_ydOze8').build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("cerca", card_search))
    application.run_polling()