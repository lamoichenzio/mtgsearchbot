import os
import logging
import httpx
import asyncio
import io
from PIL import Image, ImageDraw

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InlineQueryResultPhoto,
    InlineQueryResultArticle,
    InputTextMessageContent,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    InlineQueryHandler,
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

# --- HTTP helper (async, non-blocking) ---
async def fetch_json(url, params=None, timeout=4.0):
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.get(url, params=params)
        # Scryfall returns 404 for no-match; we propagate json anyway
        try:
            data = r.json()
        except Exception:
            data = {}
        return r.status_code, data

# --- Utility to track sent message IDs ---
def track_message(ctx, chat_id, message_id):
    if "sent_messages" not in ctx.application.bot_data:
        ctx.application.bot_data["sent_messages"] = {}
    if chat_id not in ctx.application.bot_data["sent_messages"]:
        ctx.application.bot_data["sent_messages"][chat_id] = deque(maxlen=MAX_TRACKED_MESSAGES)
    ctx.application.bot_data["sent_messages"][chat_id].append(message_id)
    logger.debug("[track_message] Tracked message %d in chat %d", message_id, chat_id)

# --- Helpers for results rendering ---
def format_results_list(cards, offset, total):
    lines = [f"Results {offset+1}-{offset+len(cards)} of {total}:"]
    for idx, c in enumerate(cards, start=offset+1):
        lines.append(f"{idx}. {c.get('name','Unknown')}")
    return "\n".join(lines)

# --- Image helpers ---
async def fetch_bytes(url, timeout=4.0):
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.content

async def build_collage(cards):
    """Build a 2x3 collage of small images. Returns (bytes_io, caption_text)."""
    # Layout
    cols, rows = 3, 2
    cell_w, cell_h = 320, 220
    pad = 6
    W = cols * cell_w + (cols + 1) * pad
    H = rows * cell_h + (rows + 1) * pad
    canvas = Image.new("RGB", (W, H), (24, 24, 24))
    draw = ImageDraw.Draw(canvas)

    # Up to 6 cards
    caption_lines = []
    urls = []
    for c in cards[:6]:
        if "image_uris" in c:
            urls.append(c["image_uris"].get("small") or c["image_uris"].get("normal"))
        elif "card_faces" in c and c["card_faces"]:
            face0 = c["card_faces"][0]
            if "image_uris" in face0:
                urls.append(face0["image_uris"].get("small") or face0["image_uris"].get("normal"))
        else:
            urls.append(None)

    # Download and paste
    for idx, (c, url) in enumerate(zip(cards[:6], urls), start=1):
        col = (idx - 1) % cols
        row = (idx - 1) // cols
        x0 = pad + col * (cell_w + pad)
        y0 = pad + row * (cell_h + pad)
        if url:
            try:
                img_bytes = await fetch_bytes(url)
                im = Image.open(io.BytesIO(img_bytes)).convert("RGB")
                im = im.resize((cell_w, cell_h))
                canvas.paste(im, (x0, y0))
            except Exception:
                draw.rectangle([x0, y0, x0+cell_w, y0+cell_h], fill=(60,60,60))
        else:
            draw.rectangle([x0, y0, x0+cell_w, y0+cell_h], fill=(60,60,60))
        # index badge
        badge = f"{idx}"
        bx, by = x0 + 8, y0 + 8
        draw.rectangle([bx-4, by-4, bx+24, by+24], fill=(0,0,0,)),
        draw.text((bx, by), badge, fill=(255,255,255))
        caption_lines.append(f"{idx}. {c.get('name','Unknown')}")

    bio = io.BytesIO()
    canvas.save(bio, format="JPEG", quality=85)
    bio.seek(0)
    caption = "\n".join(caption_lines)
    return bio, caption

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

    # send a placeholder message to be updated later
    working = await update.message.reply_text("🔎 Cerco…")
    ctx.user_data["results_msg_id"] = working.message_id
    ctx.user_data["results_chat_id"] = update.effective_chat.id
    track_message(ctx, update.effective_chat.id, working.message_id)

    status, data = await fetch_json("https://api.scryfall.com/cards/named", params={"fuzzy": name})
    if status == 200:
        card = data
        caption, kb = build_card_caption_and_kb(card)
        rows = kb.inline_keyboard if kb else []
        rows = [row[:] for row in rows]
        rows.append([InlineKeyboardButton("🖼️ Mostra immagine", callback_data=f"showimg:{card.get('id')}")])
        kb = InlineKeyboardMarkup(rows)
        ctx.user_data["last_card"] = card
        await ctx.bot.edit_message_text(chat_id=ctx.user_data["results_chat_id"], message_id=ctx.user_data["results_msg_id"], text=caption, reply_markup=kb)
        return

    logger.debug("[/search] Fuzzy failed, trying autocomplete")
    status, ac_data = await fetch_json("https://api.scryfall.com/cards/autocomplete", params={"q": name})
    suggestions = ac_data.get("data", [])
    if not suggestions:
        await ctx.bot.edit_message_text(chat_id=ctx.user_data["results_chat_id"], message_id=ctx.user_data["results_msg_id"], text=f"No results found for '{name}'.")
        return

    keyboard = [[InlineKeyboardButton(s, callback_data=f"namesuggest:{s}")] for s in suggestions[:10]]
    await ctx.bot.edit_message_text(
        chat_id=ctx.user_data["results_chat_id"],
        message_id=ctx.user_data["results_msg_id"],
        text="No exact match found. Did you mean:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return

async def handle_name_suggestion(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    name = update.callback_query.data.split(":", 1)[1]
    logger.info("[suggestion] Selected: %s", name)
    status, data = await fetch_json("https://api.scryfall.com/cards/named", params={"fuzzy": name})
    chat_id = ctx.user_data.get("results_chat_id") or update.callback_query.message.chat.id
    msg_id = ctx.user_data.get("results_msg_id") or update.callback_query.message.message_id
    if status == 200:
        card = data
        caption, kb = build_card_caption_and_kb(card)
        rows = kb.inline_keyboard if kb else []
        rows = [row[:] for row in rows]
        rows.append([InlineKeyboardButton("🖼️ Mostra immagine", callback_data=f"showimg:{card.get('id')}")])
        kb = InlineKeyboardMarkup(rows)
        ctx.user_data["last_card"] = card
        await ctx.bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=caption, reply_markup=kb)
    else:
        await ctx.bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text="❌ Failed to retrieve this card.")

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

    status, data = await fetch_json("https://api.scryfall.com/cards/search", params={"q": query, "unique": "cards", "order": "relevance"})
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
    ctx.user_data["page_offset"] = 0  # offset in groups of 6

    # Build a 2x3 collage preview and a single message with name buttons
    show_cards = cards[:6]
    collage_io, caption = await build_collage(show_cards)
    ctx.user_data["page_offset"] = 0  # offset in groups of 6
    # One button per card with the card name
    name_rows = [[InlineKeyboardButton(c.get("name","Unknown"), callback_data=f"findchoose:{c['id']}")] for c in show_cards]
    nav_row = []
    if total > 6:
        nav_row.append(InlineKeyboardButton("◀️ Prev", callback_data="findprev"))
        nav_row.append(InlineKeyboardButton("▶️ Next", callback_data="findnext"))
    keyboard_rows = name_rows + ([nav_row] if nav_row else [])
    sent = await update.message.reply_photo(photo=collage_io, caption=caption, reply_markup=InlineKeyboardMarkup(keyboard_rows))
    ctx.user_data["results_msg_id"] = sent.message_id
    ctx.user_data["results_chat_id"] = update.effective_chat.id
    track_message(ctx, update.effective_chat.id, sent.message_id)
    return

async def handle_find_choice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    data = update.callback_query.data
    chat_id = ctx.user_data.get("results_chat_id") or update.callback_query.message.chat.id
    msg_id = ctx.user_data.get("results_msg_id") or update.callback_query.message.message_id

    if data == "findnext":
        ctx.user_data["page_offset"] = ctx.user_data.get("page_offset", 0) + 1
    elif data == "findprev":
        ctx.user_data["page_offset"] = max(0, ctx.user_data.get("page_offset", 0) - 1)
    elif data.startswith("findchoose:"):
        cid = data.split(":", 1)[1]
        card = next((c for c in ctx.user_data["all_cards"] if c["id"] == cid), None)
        if not card:
            await update.callback_query.edit_message_text("❌ Could not find this card.")
            return
        # Show single card image + two buttons: details toggle and art navigation
        ctx.user_data["selected_card_id"] = cid
        ctx.user_data["show_details"] = False
        # Prepare art list
        prints_url = card.get("prints_search_uri")
        prints = []
        if prints_url:
            _, pdata = await fetch_json(prints_url)
            prints = pdata.get("data", [])
        ctx.user_data["prints"] = prints
        ctx.user_data["print_index"] = 0

        # Send/replace message with the card image
        caption, _ = build_card_caption_and_kb(card)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📝 Dettagli", callback_data="toggledetails")],
            [InlineKeyboardButton("◀️", callback_data="artprev"), InlineKeyboardButton("▶️", callback_data="artnext")]
        ])
        # Replace the collage message with the single photo
        try:
            await ctx.bot.delete_message(chat_id, msg_id)
        except Exception:
            pass
        await send_full_image(update.callback_query.message, ctx, chat_id, card, caption=card.get("name",""), kb=kb)
        return
    else:
        return

    # For pagination branches, rebuild the collage in the same message (6 per page)
    page_size = 6
    total = ctx.user_data["total"]
    all_cards = ctx.user_data["all_cards"]
    page_offset = ctx.user_data.get("page_offset", 0)
    start = page_offset * page_size
    end = min(start + page_size, total)
    show_cards = all_cards[start:end]
    collage_io, caption = await build_collage(show_cards)
    # Build buttons with card names
    name_rows = [[InlineKeyboardButton(c.get("name","Unknown"), callback_data=f"findchoose:{c['id']}")] for c in show_cards]
    nav_row = []
    if page_offset > 0:
        nav_row.append(InlineKeyboardButton("◀️ Prev", callback_data="findprev"))
    if end < total:
        nav_row.append(InlineKeyboardButton("▶️ Next", callback_data="findnext"))

    keyboard_rows = name_rows + ([nav_row] if nav_row else [])
    media = InputMediaPhoto(collage_io)
    await ctx.bot.edit_message_media(chat_id=chat_id, message_id=msg_id, media=media, reply_markup=InlineKeyboardMarkup(keyboard_rows))
    await ctx.bot.edit_message_caption(chat_id=chat_id, message_id=msg_id, caption=caption)
    return
# --- Toggle details callback handler ---
async def handle_toggle_details(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    chat_id = ctx.user_data.get("results_chat_id") or update.callback_query.message.chat.id
    msg = update.callback_query.message
    ctx.user_data["show_details"] = not ctx.user_data.get("show_details", False)
    # Rebuild caption
    card = ctx.user_data.get("last_card")
    if not card:
        await msg.reply_text("❌ No card selected.")
        return
    if ctx.user_data["show_details"]:
        caption, _ = build_card_caption_and_kb(card)
    else:
        caption = card.get("name", "")
    try:
        await msg.edit_caption(caption)
    except Exception:
        # If it's a text message fallback, edit text
        await msg.edit_text(caption)

# --- Art navigation callback handler ---
async def handle_art_nav(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    direction = update.callback_query.data
    delta = -1 if direction == "artprev" else 1
    prints = ctx.user_data.get("prints", [])
    if not prints:
        await update.callback_query.answer("No alternate arts")
        return
    idx = (ctx.user_data.get("print_index", 0) + delta) % len(prints)
    ctx.user_data["print_index"] = idx
    p = prints[idx]
    # Extract image url
    if "image_uris" in p:
        url = p["image_uris"].get("normal")
    elif "card_faces" in p and p["card_faces"]:
        url = p["card_faces"][0]["image_uris"].get("normal")
    else:
        await update.callback_query.answer("No image for this print")
        return
    # Edit media in-place
    media = InputMediaPhoto(url)
    try:
        await update.callback_query.message.edit_media(media)
    except Exception as e:
        logger.warning("[art_nav] edit_media failed: %s", e)

# --- Helpers for captions & inline buttons ---
def build_card_caption_and_kb(card):
    name = card.get("name", "Unknown")
    set_name = card.get("set_name", "")
    type_line = card.get("type_line") or (card.get("card_faces", [{}])[0].get("type_line")) or ""
    mana_cost = card.get("mana_cost") or (card.get("card_faces", [{}])[0].get("mana_cost")) or ""
    oracle_text = card.get("oracle_text") or (card.get("card_faces", [{}])[0].get("oracle_text")) or ""

    # compact single-line mana/type row
    head = f"{name} — {set_name}" if set_name else name
    mt_row = " ".join(filter(None, [mana_cost, "•", type_line])) if (mana_cost or type_line) else ""

    # trim oracle text to avoid overly long captions
    if oracle_text:
        oracle_clean = oracle_text.replace("\n", " ")
        if len(oracle_clean) > 220:
            oracle_clean = oracle_clean[:217].rstrip() + "…"
    else:
        oracle_clean = ""

    parts = [head]
    if mt_row:
        parts.append(mt_row)
    if oracle_clean:
        parts.append(oracle_clean)
    caption = "\n".join(parts)

    # Build inline keyboard with Rulings and Variants
    rulings_url = card.get("rulings_uri")
    prints_url = card.get("prints_search_uri")
    buttons = []
    if rulings_url:
        buttons.append(InlineKeyboardButton("📜 Rulings", url=rulings_url))
    if prints_url:
        buttons.append(InlineKeyboardButton("🖼️ Varianti", url=prints_url))
    kb = InlineKeyboardMarkup([buttons]) if buttons else None

    return caption, kb

# --- Inline Mode (@bot query) ---
async def inline_query(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = (update.inline_query.query or "").strip()
    logger.debug("[inline_query] Query='%s' offset='%s' from=%s", q, update.inline_query.offset, update.inline_query.from_user.id)
    # Telegram sends empty queries while the user is typing; avoid spamming the API
    if not q:
        logger.debug("[inline_query] Empty query, sending empty results")
        await update.inline_query.answer([], cache_time=1, is_personal=True)
        return

    # Map Telegram offset -> Scryfall page (as a string)
    try:
        page = int(update.inline_query.offset) if update.inline_query.offset else 1
    except ValueError:
        page = 1

    params = {
        "q": q,
        "unique": "cards",
        "order": "relevance",
        "page": page
    }
    try:
        status, data = await fetch_json("https://api.scryfall.com/cards/search", params=params, timeout=3.5)
    except Exception as e:
        logger.warning("[inline_query] fetch error: %s", e)
        data = {"data": [], "has_more": False}

    cards = data.get("data", [])
    has_more = data.get("has_more", False)
    total_cards = len(cards)
    logger.debug("[inline_query] Scryfall returned %d cards (has_more=%s) for page=%d", total_cards, has_more, page)

    # Telegram allows up to 50 results; keep a margin
    cards = cards[:48]

    results = []
    for c in cards:
        cid = c.get("id") or str(hash(c.get("name", "unknown")))
        name = c.get("name", "Unknown")
        caption, kb = build_card_caption_and_kb(c)
        if len(caption) > 1024:
            caption = caption[:1021] + "…"

        # Prefer images when available, fallback to text result
        img_small = None
        img_normal = None
        if "image_uris" in c:
            img_small = c["image_uris"].get("small")
            img_normal = c["image_uris"].get("normal")
        elif "card_faces" in c and c["card_faces"]:
            face0 = c["card_faces"][0]
            if "image_uris" in face0:
                img_small = face0["image_uris"].get("small")
                img_normal = face0["image_uris"].get("normal")

        if not (img_small and img_normal) and not name:
            logger.debug("[inline_query] Skipping card without usable data: %s", c.get("id"))
            continue

        if img_small and img_normal:
            results.append(
                InlineQueryResultPhoto(
                    id=cid,
                    title=name,
                    description=c.get("set_name", ""),
                    thumb_url=img_small,
                    photo_url=img_normal,
                    caption=caption,
                    reply_markup=kb
                )
            )
        else:
            results.append(
                InlineQueryResultArticle(
                    id=cid,
                    title=name,
                    description=c.get("set_name", "") or "Card",
                    input_message_content=InputTextMessageContent(caption),
                    reply_markup=kb
                )
            )

    next_offset = str(page + 1) if has_more else ""
    logger.debug("[inline_query] Sending %d results, next_offset='%s'", len(results), next_offset)
    # is_personal keeps results scoped to the querying user
    await update.inline_query.answer(results, cache_time=0, is_personal=True, next_offset=next_offset)

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
async def send_full_image(message, ctx, chat_id, card, caption=None, kb=None):
    if "image_uris" in card:
        url = card["image_uris"]["normal"]
    else:
        url = card["card_faces"][0]["image_uris"]["normal"]
    if caption is None:
        caption = f"{card['name']} — {card['set_name']}"
    sent = await message.reply_photo(url, caption=caption, reply_markup=kb)
    track_message(ctx, chat_id, sent.message_id)

# --- Show image callback handler ---
async def handle_show_image(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    cid = update.callback_query.data.split(":", 1)[1]
    chat_id = ctx.user_data.get("results_chat_id") or update.callback_query.message.chat.id
    msg_id = ctx.user_data.get("results_msg_id") or update.callback_query.message.message_id

    # Try to find the card by id from stored results; fallback to last_card
    card = None
    if "all_cards" in ctx.user_data:
        card = next((c for c in ctx.user_data["all_cards"] if c.get("id") == cid), None)
    if card is None:
        card = ctx.user_data.get("last_card")
    if card is None:
        await update.callback_query.edit_message_text("❌ Could not load image for this card.")
        return

    caption, kb = build_card_caption_and_kb(card)

    # Delete the text message to keep chat clean, then send the photo with caption+buttons
    try:
        await ctx.bot.delete_message(chat_id, msg_id)
    except Exception:
        pass
    await send_full_image(update.callback_query.message, ctx, chat_id, card, caption=caption, kb=kb)

# --- Error handler ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("🚨 Exception handled:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        sent = await update.effective_message.reply_text("❌ An internal error occurred, please try again later.")
        track_message(context, update.effective_chat.id, sent.message_id)

# NOTE: Enable Inline Mode for this bot via @BotFather (/setinline)
# --- Application setup ---
app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("search", search))
app.add_handler(CommandHandler("find", find))
app.add_handler(CommandHandler("cleanup", cleanup))
app.add_handler(CallbackQueryHandler(handle_name_suggestion, pattern=r"^namesuggest:"))
app.add_handler(CallbackQueryHandler(handle_find_choice, pattern=r"^(findchoose:|findnext$|findprev$)"))
app.add_handler(CallbackQueryHandler(handle_show_image, pattern=r"^showimg:"))
app.add_handler(CallbackQueryHandler(handle_toggle_details, pattern=r"^toggledetails$"))
app.add_handler(CallbackQueryHandler(handle_art_nav, pattern=r"^(artprev|artnext)$"))
app.add_handler(InlineQueryHandler(inline_query))
app.add_error_handler(error_handler)

PORT = int(os.getenv("PORT", "8443"))
app.run_webhook(
    listen="0.0.0.0",
    port=PORT,
    url_path=TOKEN,
    webhook_url=f"https://{HOST}/{TOKEN}",
    allowed_updates=Update.ALL_TYPES
)