import os
import logging
import requests

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
from collections import deque

# --- Logging setup ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.DEBUG
)
logger = logging.getLogger(__name__)

# --- Config ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
HOST = os.getenv("RENDER_EXTERNAL_HOSTNAME", "")
MAX_TRACKED_MESSAGES = 500

# --- Utility to track sent message IDs ---
def track_message(ctx, chat_id, message_id):
    if "sent_messages" not in ctx.application.bot_data:
        ctx.application.bot_data["sent_messages"] = {}
    if chat_id not in ctx.application.bot_data["sent_messages"]:
        ctx.application.bot_data["sent_messages"][chat_id] = deque(maxlen=MAX_TRACKED_MESSAGES)
    ctx.application.bot_data["sent_messages"][chat_id].append(message_id)
    logger.debug("[track_message] Tracked message %d in chat %d", message_id, chat_id)

# --- Helpers ---
def format_results_list(cards, offset, total):
    lines = [f"Results {offset+1}-{offset+len(cards)} of {total}:"]
    for idx, c in enumerate(cards, start=offset+1):
        lines.append(f"{idx}. {c.get('name','Unknown')}")
    return "\n".join(lines)

def base_card_kb(card_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Oracle", callback_data=f"oracle:{card_id}"),
         InlineKeyboardButton("🎨 Illustrazioni", callback_data=f"arts:{card_id}")]
    ])

# --- Preview album helpers ---
async def send_preview_album(message, ctx, cards):
    """Send a media group of small images for the current page, deleting any previous previews."""
    # 1) Delete previous preview album, if any
    album_ids = ctx.user_data.get("album_msg_ids") or []
    chat_id = ctx.user_data.get("results_chat_id") or message.chat.id
    for mid in album_ids:
        try:
            await ctx.bot.delete_message(chat_id, mid)
        except Exception:
            pass
    ctx.user_data["album_msg_ids"] = []

    # 2) Build media group with small thumbs
    media = []
    for c in cards:
        try:
            if "image_uris" in c:
                img_url = c["image_uris"].get("small") or c["image_uris"].get("normal")
            else:
                img_url = c["card_faces"][0]["image_uris"].get("small") or c["card_faces"][0]["image_uris"].get("normal")
            media.append(InputMediaPhoto(img_url))
        except Exception:
            continue

    if not media:
        return

    sent_msgs = await message.reply_media_group(media)
    album_ids = []
    for m in sent_msgs:
        album_ids.append(m.message_id)
        track_message(ctx, chat_id, m.message_id)
    ctx.user_data["album_msg_ids"] = album_ids

# --- Arts preview album helper ---
async def send_arts_preview_album(message, ctx, prints_page):
    """Send a media group of small images for the current arts page; delete previous arts previews."""
    # Delete previous arts album if any
    arts_album = ctx.user_data.get("arts_album_msg_ids") or []
    chat_id = ctx.user_data.get("results_chat_id") or message.chat.id
    for mid in arts_album:
        try:
            await ctx.bot.delete_message(chat_id, mid)
        except Exception:
            pass
    ctx.user_data["arts_album_msg_ids"] = []

    media = []
    for p in prints_page:
        try:
            if "image_uris" in p:
                url = p["image_uris"].get("small") or p["image_uris"].get("normal")
            elif "card_faces" in p and p["card_faces"]:
                url = p["card_faces"][0]["image_uris"].get("small") or p["card_faces"][0]["image_uris"].get("normal")
            else:
                url = None
            if url:
                media.append(InputMediaPhoto(url))
        except Exception:
            continue

    if not media:
        return

    sent_msgs = await message.reply_media_group(media)
    arts_ids = []
    for m in sent_msgs:
        arts_ids.append(m.message_id)
        track_message(ctx, chat_id, m.message_id)
    ctx.user_data["arts_album_msg_ids"] = arts_ids

# --- /start ---
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    logger.info("[/start] Triggered by %s", update.effective_user.username)
    sent = await update.message.reply_text(
        "👋 MTG Search Bot ready.\n\n"
        "Commands:\n"
        "/search <card name> - Find a card by name\n"
        "/find <query> - Advanced card search\n"
        "/cleanup <N> - Delete last N bot messages\n\n"
        "Example /find queries:\n"
        "• c:r cmc=1\n"
        "• t:creature o:\"draw a card\"\n"
        "• o:flying c:u cmc<=3\n"
        "Full syntax: https://scryfall.com/docs/syntax"
    )
    track_message(ctx, update.effective_chat.id, sent.message_id)

# --- /search ---
async def search(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        sent = await update.message.reply_text("Usage: /search <card name>")
        track_message(ctx, update.effective_chat.id, sent.message_id)
        return
    name = " ".join(ctx.args).strip()
    logger.info("[/search] Searching for: %s", name)

    # Single placeholder to avoid spamming the chat
    working = await update.message.reply_text("🔎 Cerco…")
    ctx.user_data["results_msg_id"] = working.message_id
    ctx.user_data["results_chat_id"] = update.effective_chat.id
    track_message(ctx, update.effective_chat.id, working.message_id)

    resp = requests.get("https://api.scryfall.com/cards/named", params={"fuzzy": name})
    if resp.status_code == 200:
        card = resp.json()
        logger.debug("[/search] Fuzzy found: %s", card["name"])
        try:
            await ctx.bot.delete_message(ctx.user_data["results_chat_id"], ctx.user_data["results_msg_id"])
        except Exception:
            pass
        await send_full_image(update.message, ctx, update.effective_chat.id, card, kb=base_card_kb(card["id"]))
        return

    logger.debug("[/search] Fuzzy failed, trying autocomplete")
    ac_resp = requests.get("https://api.scryfall.com/cards/autocomplete", params={"q": name})
    suggestions = ac_resp.json().get("data", [])
    if not suggestions:
        await ctx.bot.edit_message_text(
            chat_id=ctx.user_data["results_chat_id"],
            message_id=ctx.user_data["results_msg_id"],
            text=f"No results found for '{name}'."
        )
        return

    keyboard = [[InlineKeyboardButton(s, callback_data=f"namesuggest:{s}")] for s in suggestions[:10]]
    await ctx.bot.edit_message_text(
        chat_id=ctx.user_data["results_chat_id"],
        message_id=ctx.user_data["results_msg_id"],
        text="No exact match found. Did you mean:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_name_suggestion(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    name = update.callback_query.data.split(":", 1)[1]
    logger.info("[suggestion] Selected: %s", name)
    resp = requests.get("https://api.scryfall.com/cards/named", params={"fuzzy": name})
    if resp.status_code == 200:
        card = resp.json()
        try:
            chat_id = ctx.user_data.get("results_chat_id") or update.callback_query.message.chat.id
            msg_id = ctx.user_data.get("results_msg_id") or update.callback_query.message.message_id
            await ctx.bot.delete_message(chat_id, msg_id)
        except Exception:
            pass
        await send_full_image(update.callback_query.message, ctx, update.callback_query.message.chat.id, card, kb=base_card_kb(card["id"]))
        return
    else:
        chat_id = ctx.user_data.get("results_chat_id") or update.callback_query.message.chat.id
        msg_id = ctx.user_data.get("results_msg_id") or update.callback_query.message.message_id
        await ctx.bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text="❌ Failed to retrieve this card.")
        return

# --- /find ---
async def find(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        sent = await update.message.reply_text(
            "Usage: /find <query>\n\n"
            "Examples:\n"
            "• c:r cmc=1\n"
            "• t:creature o:\"draw a card\"\n"
            "• o:flying c:u cmc<=3\n"
            "Full syntax: https://scryfall.com/docs/syntax"
        )
        track_message(ctx, update.effective_chat.id, sent.message_id)
        return
    query = " ".join(ctx.args).strip()
    logger.info("[/find] Query: %s", query)

    resp = requests.get("https://api.scryfall.com/cards/search", params={"q": query, "unique": "cards", "order": "relevance"})
    data = resp.json()
    cards = data.get("data", [])
    total = data.get("total_cards", 0)
    logger.debug("[/find] Found %d cards", total)

    if not cards:
        sent = await update.message.reply_text("No results found for this query.")
        track_message(ctx, update.effective_chat.id, sent.message_id)
        return

    ctx.user_data["query"] = query
    ctx.user_data["total"] = total
    ctx.user_data["all_cards"] = cards
    ctx.user_data["offset"] = 0

    offset = ctx.user_data["offset"]
    window = ctx.user_data["all_cards"][offset:offset+5]
    keyboard = [[InlineKeyboardButton(c["name"], callback_data=f"findchoose:{c['id']}")] for c in window]
    row = []
    if offset > 0:
        row.append(InlineKeyboardButton("◀️ Prev", callback_data="findprev"))
    if offset + 5 < total:
        row.append(InlineKeyboardButton("▶️ Next", callback_data="findnext"))
    if row:
        keyboard.append(row)

    sent = await update.message.reply_text("Scegli una carta:", reply_markup=InlineKeyboardMarkup(keyboard))
    ctx.user_data["results_msg_id"] = sent.message_id
    ctx.user_data["results_chat_id"] = update.effective_chat.id
    track_message(ctx, update.effective_chat.id, sent.message_id)

    # Also show a visual preview album for the current window (deleted/updated on pagination)
    await send_preview_album(update.message, ctx, window)

    return

# Removed send_query_page function entirely

async def handle_find_choice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    data = update.callback_query.data
    chat_id = ctx.user_data.get("results_chat_id") or update.callback_query.message.chat.id
    msg_id = ctx.user_data.get("results_msg_id") or update.callback_query.message.message_id

    if data == "findnext":
        ctx.user_data["offset"] = min(ctx.user_data["offset"] + 5, max(0, ctx.user_data["total"] - 5))
    elif data == "findprev":
        ctx.user_data["offset"] = max(0, ctx.user_data["offset"] - 5)
    elif data.startswith("findchoose:"):
        cid = data.split(":", 1)[1]
        card = next((c for c in ctx.user_data["all_cards"] if c["id"] == cid), None)
        if card:
            # Replace the list message by deleting it, then send the image
            try:
                # Delete the list message
                await ctx.bot.delete_message(chat_id, msg_id)
            except Exception:
                pass
            # Delete preview album messages, if any
            for mid in ctx.user_data.get("album_msg_ids", []):
                try:
                    await ctx.bot.delete_message(chat_id, mid)
                except Exception:
                    pass
            ctx.user_data["album_msg_ids"] = []
            await send_full_image(update.callback_query.message, ctx, chat_id, card, kb=base_card_kb(card["id"]))
        else:
            await ctx.bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text="❌ Could not find this card.")
        return
    else:
        return

    # Rebuild current window and edit the same message
    offset = ctx.user_data["offset"]
    total = ctx.user_data["total"]
    window = ctx.user_data["all_cards"][offset:offset+5]
    keyboard = [[InlineKeyboardButton(c["name"], callback_data=f"findchoose:{c['id']}")] for c in window]
    row = []
    if offset > 0:
        row.append(InlineKeyboardButton("◀️ Prev", callback_data="findprev"))
    if offset + 5 < total:
        row.append(InlineKeyboardButton("▶️ Next", callback_data="findnext"))
    if row:
        keyboard.append(row)

    # Update the visual preview album for the new page
    await send_preview_album(update.callback_query.message, ctx, window)

    await ctx.bot.edit_message_reply_markup(chat_id=chat_id, message_id=msg_id, reply_markup=InlineKeyboardMarkup(keyboard))

# --- /cleanup ---
async def cleanup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    count = int(args[0]) if args and args[0].isdigit() else 15
    chat_id = update.effective_chat.id
    logger.info("[/cleanup] Requested by %s to delete last %d messages", update.effective_user.username, count)

    sent = await update.message.reply_text(f"🧹 Cleaning up last {count} bot messages...")
    track_message(ctx, chat_id, sent.message_id)

    try:
        messages = list(ctx.application.bot_data.get("sent_messages", {}).get(chat_id, []))[-count:]
        deleted = 0
        for msg_id in messages:
            try:
                await ctx.bot.delete_message(chat_id, msg_id)
                deleted += 1
            except Exception as e:
                logger.warning("[/cleanup] Could not delete message %d: %s", msg_id, str(e))
        done = await update.message.reply_text(f"✅ Cleanup completed. Deleted {deleted} messages.")
        track_message(ctx, chat_id, done.message_id)
    except Exception as e:
        logger.error("[/cleanup] Error: %s", str(e))
        sent = await update.message.reply_text("❌ An error occurred during cleanup.")
        track_message(ctx, chat_id, sent.message_id)

# --- Send card image ---
async def send_full_image(message, ctx, chat_id, card, kb=None, caption=None):
    if "image_uris" in card:
        url = card["image_uris"]["normal"]
    else:
        url = card["card_faces"][0]["image_uris"]["normal"]
    if caption is None:
        caption = f"{card['name']} — {card['set_name']}"
    sent = await message.reply_photo(url, caption=caption, reply_markup=kb)
    track_message(ctx, chat_id, sent.message_id)

# --- Error handler ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("🚨 Exception handled:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        sent = await update.effective_message.reply_text("❌ An internal error occurred, please try again later.")
        track_message(context, update.effective_chat.id, sent.message_id)

# --- Oracle and arts handlers ---
async def handle_oracle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    card_id = update.callback_query.data.split(":", 1)[1]
    # Fetch full card by id to ensure oracle text present
    try:
        r = requests.get(f"https://api.scryfall.com/cards/{card_id}")
        c = r.json()
    except Exception:
        await update.callback_query.message.reply_text("❌ Failed to load oracle text.")
        return
    # Build caption with oracle (single-line, trimmed)
    name = c.get("name", "Unknown")
    set_name = c.get("set_name", "")
    oracle = c.get("oracle_text") or (c.get("card_faces", [{}])[0].get("oracle_text")) or ""
    oracle = oracle.replace("\n", " ")
    if len(oracle) > 900:
        oracle = oracle[:897].rstrip() + "…"
    header = f"{name} — {set_name}" if set_name else name
    caption = f"{header}\n{oracle}" if oracle else header
    try:
        await update.callback_query.message.edit_caption(caption, reply_markup=base_card_kb(card_id))
    except Exception:
        # If the current message is text (unlikely here), edit text instead
        await update.callback_query.message.edit_text(caption, reply_markup=base_card_kb(card_id))


# --- Arts menu pagination helpers ---
async def render_arts_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    state = ctx.user_data.get("arts_state") or {}
    prints = state.get("prints", [])
    offset = state.get("offset", 0)
    card_id = state.get("card_id")

    page_items = prints[offset:offset+10]
    rows = []
    for p in page_items:
        label = f"{p.get('set','').upper()} #{p.get('collector_number','?')}"
        rows.append([InlineKeyboardButton(label, callback_data=f"pickart:{p.get('id')}")])

    # Navigation row only if needed
    nav_row = []
    if offset > 0:
        nav_row.append(InlineKeyboardButton("◀️ Prev", callback_data="artsnav:prev"))
    has_more_local = offset + 10 < len(prints)
    has_more_remote = state.get("has_more", False)
    if has_more_local or has_more_remote:
        nav_row.append(InlineKeyboardButton("▶️ Next", callback_data="artsnav:next"))
    if nav_row:
        rows.append(nav_row)

    # Single Back row at the end
    rows.append([InlineKeyboardButton("⬅️ Indietro", callback_data=f"back:{card_id}")])

    await update.callback_query.message.edit_reply_markup(InlineKeyboardMarkup(rows))

    # Also (re)send visual previews for the current arts page
    await send_arts_preview_album(update.callback_query.message, ctx, page_items)

async def handle_arts_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    card_id = update.callback_query.data.split(":", 1)[1]
    # Fetch base card to get prints_search_uri
    r = requests.get(f"https://api.scryfall.com/cards/{card_id}")
    base = r.json()
    prints_url = base.get("prints_search_uri")
    if not prints_url:
        await update.callback_query.answer("No alternate illustrations")
        return

    pr = requests.get(prints_url)
    pdata = pr.json()
    prints = pdata.get("data", [])

    ctx.user_data["arts_state"] = {
        "card_id": card_id,
        "prints": prints,
        "offset": 0,
        "has_more": pdata.get("has_more", False),
        "next_url": pdata.get("next_page"),
    }

    await render_arts_menu(update, ctx)

async def handle_arts_nav(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    direction = update.callback_query.data.split(":", 1)[1]
    state = ctx.user_data.get("arts_state") or {}
    if not state:
        await update.callback_query.answer("No art list loaded")
        return

    offset = state.get("offset", 0)
    prints = state.get("prints", [])
    has_more = state.get("has_more", False)
    next_url = state.get("next_url")

    if direction == "prev":
        offset = max(0, offset - 10)
    else:  # next
        offset += 10
        # If we need more items to fulfill this page and remote has more, fetch next page and extend
        if offset + 10 > len(prints) and has_more and next_url:
            pr = requests.get(next_url)
            pdata = pr.json()
            new_prints = pdata.get("data", [])
            prints.extend(new_prints)
            state["has_more"] = pdata.get("has_more", False)
            state["next_url"] = pdata.get("next_page")

    # Clamp offset to available range
    if offset >= len(prints):
        offset = max(0, len(prints) - 10)

    state["offset"] = offset
    state["prints"] = prints
    ctx.user_data["arts_state"] = state

    await render_arts_menu(update, ctx)

async def handle_pick_art(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    art_id = update.callback_query.data.split(":", 1)[1]
    # Fetch selected print
    r = requests.get(f"https://api.scryfall.com/cards/{art_id}")
    c = r.json()
    # Extract image
    if "image_uris" in c:
        url = c["image_uris"].get("normal")
    elif "card_faces" in c and c["card_faces"]:
        url = c["card_faces"][0]["image_uris"].get("normal")
    else:
        await update.callback_query.answer("No image for this print")
        return
    name = c.get("name", "")
    set_name = c.get("set_name", "")
    caption = f"{name} — {set_name}" if set_name else name
    try:
        await update.callback_query.message.edit_media(InputMediaPhoto(url, caption=caption))
    except Exception as e:
        logger.warning("[pickart] edit_media failed: %s", e)
        return
    # Remove arts preview album if present
    chat_id = ctx.user_data.get("results_chat_id") or update.callback_query.message.chat.id
    for mid in ctx.user_data.get("arts_album_msg_ids", []):
        try:
            await ctx.bot.delete_message(chat_id, mid)
        except Exception:
            pass
    ctx.user_data["arts_album_msg_ids"] = []

    # Restore base two buttons for the newly selected print
    await update.callback_query.message.edit_reply_markup(base_card_kb(c.get("id")))

async def handle_back_from_arts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    card_id = update.callback_query.data.split(":", 1)[1]
    # Clean arts preview album messages
    chat_id = ctx.user_data.get("results_chat_id") or update.callback_query.message.chat.id
    for mid in ctx.user_data.get("arts_album_msg_ids", []):
        try:
            await ctx.bot.delete_message(chat_id, mid)
        except Exception:
            pass
    ctx.user_data["arts_album_msg_ids"] = []
    await update.callback_query.message.edit_reply_markup(base_card_kb(card_id))

# --- Application setup ---
app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("search", search))
app.add_handler(CommandHandler("find", find))
app.add_handler(CommandHandler("cleanup", cleanup))
app.add_handler(CallbackQueryHandler(handle_name_suggestion, pattern=r"^namesuggest:"))
app.add_handler(CallbackQueryHandler(handle_find_choice, pattern=r"^(findchoose:|findnext$|findprev$)"))
app.add_handler(CallbackQueryHandler(handle_oracle, pattern=r"^oracle:"))
app.add_handler(CallbackQueryHandler(handle_arts_menu, pattern=r"^arts:"))
app.add_handler(CallbackQueryHandler(handle_pick_art, pattern=r"^pickart:"))
app.add_handler(CallbackQueryHandler(handle_back_from_arts, pattern=r"^back:"))
app.add_handler(CallbackQueryHandler(handle_arts_nav, pattern=r"^artsnav:(prev|next)$"))
app.add_error_handler(error_handler)

PORT = int(os.getenv("PORT", "8443"))
app.run_webhook(
    listen="0.0.0.0",
    port=PORT,
    url_path=TOKEN,
    webhook_url=f"https://{HOST}/{TOKEN}"
)