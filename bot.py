#!/usr/bin/env python3
"""
bot.py — ICAGO Telegram Bot
Chạy local  : python bot.py
Deploy Render: gunicorn bot:flask_app --bind 0.0.0.0:$PORT --workers 1 --timeout 120
"""

import asyncio
import logging
import os
import re
import sys
import tempfile
from pathlib import Path

# ── Logging setup sớm nhất ────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("icago_bot")

# ── Telegram ───────────────────────────────────────────────────────────────────
try:
    from telegram import Update, Bot
    from telegram.ext import (
        Application, CommandHandler, MessageHandler,
        filters, ContextTypes
    )
except ImportError:
    sys.exit("❌ pip install python-telegram-bot")

# ── Flask ──────────────────────────────────────────────────────────────────────
try:
    from flask import Flask, request as flask_req, jsonify
except ImportError:
    sys.exit("❌ pip install flask")

# ── PDF converter ──────────────────────────────────────────────────────────────
try:
    from icago_itinerary import convert, DEFAULT_LOGO, DEFAULT_LUUY
except ImportError as e:
    sys.exit(f"❌ Không import được icago_itinerary: {e}")

# ── PNR screenshot (không bắt buộc) ───────────────────────────────────────────
try:
    from pnr_screenshot import pnr_to_image
    PNR_ENABLED = True
    log.info("PNR screenshot: ✅ enabled")
except ImportError:
    PNR_ENABLED = False
    pnr_to_image = None
    log.warning("PNR screenshot: ⚠️ disabled")


# ══════════════════════════════════════════════════════════════════════════════
# Config
# ══════════════════════════════════════════════════════════════════════════════

BOT_TOKEN  = os.environ.get("BOT_TOKEN", "8659136625:AAFcL4VweOqk5j6Sksu_HQCk3adz0bLH5gY").strip()
LOGO_PATH  = os.environ.get("LOGO_PATH", DEFAULT_LOGO)
LUUY_PATH  = os.environ.get("LUUY_PATH", DEFAULT_LUUY)
MAX_MB     = int(os.environ.get("MAX_MB", "20"))
RENDER_URL = os.environ.get("RENDER_URL", "").rstrip("/")
PORT       = int(os.environ.get("PORT", "10000"))

if not BOT_TOKEN:
    sys.exit("❌ Chưa set BOT_TOKEN trong Environment Variables!")

WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
WEBHOOK_URL  = f"{RENDER_URL}{WEBHOOK_PATH}" if RENDER_URL else ""


# ══════════════════════════════════════════════════════════════════════════════
# Telegram Application (dùng chung)
# ══════════════════════════════════════════════════════════════════════════════

def _build_app() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help",  cmd_help))
    app.add_handler(MessageHandler(filters.Document.ALL,            handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.ALL,                     handle_other))
    return app


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def looks_like_pnr(text: str) -> bool:
    t = text.strip()
    if len(t) < 10:
        return False
    patterns = [
        r"\d+\.[A-Z]+/[A-Z]",
        r"\b[A-Z]{2}\s+\d{2,4}\b",
        r"\b[A-Z]{6}\b",
        r"\b(HK|HL|RR|SS|UN)\d+\b",
        r"\b\d{1,2}[A-Z]{3}\b",
    ]
    return sum(1 for p in patterns if re.search(p, t)) >= 2


# ══════════════════════════════════════════════════════════════════════════════
# Handlers
# ══════════════════════════════════════════════════════════════════════════════

HELP_TEXT = (
    "📖 *ICAGO Bot — Hướng dẫn*\n\n"
    "📎 *Gửi file PDF* lịch trình GDS/Amadeus\n"
    "→ Nhận PDF chuẩn ICAGO\n\n"
    + (
        "✈️ *Gửi mã PNR* (text từ GDS)\n"
        "→ Nhận ảnh lịch trình từ pnrexpert.com\n\n"
        if PNR_ENABLED else ""
    )
    + "📌 Lệnh: /start  /help"
)


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    log.info(f"/start từ {update.effective_user.full_name}")
    await update.message.reply_text(
        "👋 Xin chào! Tôi là *ICAGO Bot*.\n\n" + HELP_TEXT,
        parse_mode="Markdown",
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")


async def handle_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc.file_name.lower().endswith(".pdf"):
        await update.message.reply_text("❌ Vui lòng gửi file *PDF*.", parse_mode="Markdown")
        return
    if doc.file_size > MAX_MB * 1024 * 1024:
        await update.message.reply_text(f"❌ File quá lớn (tối đa {MAX_MB} MB).")
        return

    status = await update.message.reply_text("⏳ Đang xử lý PDF...")
    log.info(f"PDF: {doc.file_name}")

    try:
        with tempfile.TemporaryDirectory() as tmp:
            inp  = os.path.join(tmp, "input.pdf")
            name = Path(doc.file_name).stem + "_ICAGO.pdf"
            out  = os.path.join(tmp, name)

            tgf = await ctx.bot.get_file(doc.file_id)
            await tgf.download_to_drive(inp)
            convert(inp, out, logo_path=LOGO_PATH, luuy_path=LUUY_PATH)

            with open(out, "rb") as f:
                await update.message.reply_document(
                    document=f, filename=name,
                    caption=f"✅ Hoàn thành! ({os.path.getsize(out)//1024} KB)",
                )
        await status.delete()

    except Exception as e:
        log.exception("Lỗi PDF")
        await status.edit_text(f"❌ Lỗi:\n`{e}`", parse_mode="Markdown")


async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if not text:
        return

    if not looks_like_pnr(text):
        await update.message.reply_text(
            "📎 Gửi *file PDF* hoặc *mã PNR*. /help",
            parse_mode="Markdown",
        )
        return

    if not PNR_ENABLED:
        await update.message.reply_text("⚠️ Tính năng PNR chưa sẵn sàng.")
        return

    log.info(f"PNR: {text[:60]}...")
    status = await update.message.reply_text(
        "✈️ Đang xử lý PNR...\n_(10–20 giây)_", parse_mode="Markdown",
    )

    try:
        with tempfile.TemporaryDirectory() as tmp:
            img_path = os.path.join(tmp, "pnr_result.png")
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: pnr_to_image(text, img_path))

            if not os.path.isfile(img_path) or os.path.getsize(img_path) < 1000:
                raise RuntimeError("Screenshot trống")

            with open(img_path, "rb") as f:
                await update.message.reply_photo(
                    photo=f, caption="✅ Lịch trình từ pnrexpert.com",
                )
        await status.delete()

    except Exception as e:
        log.exception("Lỗi PNR")
        await status.edit_text(f"❌ Lỗi:\n`{e}`", parse_mode="Markdown")


async def handle_other(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📎 Gửi *file PDF* hoặc *mã PNR*. /help",
                                    parse_mode="Markdown")


# ══════════════════════════════════════════════════════════════════════════════
# Flask — Webhook mode (Render)
# ══════════════════════════════════════════════════════════════════════════════

flask_app  = Flask(__name__)
_ptb_app   = _build_app()          # PTB Application instance

# ── Khởi động PTB event loop trong thread riêng ───────────────────────────────
import threading

_loop = asyncio.new_event_loop()

def _start_loop():
    asyncio.set_event_loop(_loop)
    _loop.run_forever()

_thread = threading.Thread(target=_start_loop, daemon=True)
_thread.start()

# Khởi tạo PTB application (initialize + start) trong loop đó
async def _init_ptb():
    await _ptb_app.initialize()
    await _ptb_app.start()

asyncio.run_coroutine_threadsafe(_init_ptb(), _loop).result(timeout=30)
log.info("✅ PTB application initialized")

# Tự động set webhook khi server khởi động
if WEBHOOK_URL:
    async def _set_webhook():
        await _ptb_app.bot.set_webhook(
            url=WEBHOOK_URL,
            allowed_updates=["message", "edited_message", "callback_query"],
            drop_pending_updates=True,
        )
        info = await _ptb_app.bot.get_webhook_info()
        log.info(f"✅ Webhook set: {info.url}")

    fut = asyncio.run_coroutine_threadsafe(_set_webhook(), _loop)
    try:
        fut.result(timeout=15)
    except Exception as e:
        log.error(f"⚠️ Set webhook lỗi: {e}")
else:
    log.warning("⚠️ RENDER_URL chưa set — webhook chưa được đăng ký")


# ── Routes ─────────────────────────────────────────────────────────────────────

@flask_app.route("/", methods=["GET"])
def health():
    """Health check — Render dùng để kiểm tra service live."""
    info = asyncio.run_coroutine_threadsafe(
        _ptb_app.bot.get_webhook_info(), _loop
    ).result(timeout=10)
    return jsonify({
        "status":      "running ✅",
        "webhook_url": info.url or "❌ chưa set",
        "pending":     info.pending_update_count,
        "pnr":         PNR_ENABLED,
    })


@flask_app.route(WEBHOOK_PATH, methods=["POST"])
def webhook():
    """Nhận update từ Telegram và xử lý bất đồng bộ."""
    data   = flask_req.get_json(force=True)
    update = Update.de_json(data, _ptb_app.bot)

    # Đẩy vào event loop riêng — không block gunicorn worker
    future = asyncio.run_coroutine_threadsafe(
        _ptb_app.process_update(update), _loop
    )
    try:
        future.result(timeout=60)
    except Exception as e:
        log.exception(f"Lỗi xử lý update: {e}")

    return "ok", 200


@flask_app.route("/set_webhook", methods=["GET"])
def set_webhook_manually():
    """Endpoint tiện lợi để set/reset webhook thủ công."""
    if not WEBHOOK_URL:
        return jsonify({"error": "RENDER_URL chưa set"}), 400
    fut = asyncio.run_coroutine_threadsafe(
        _ptb_app.bot.set_webhook(url=WEBHOOK_URL, drop_pending_updates=True),
        _loop
    )
    try:
        fut.result(timeout=15)
        return jsonify({"ok": True, "webhook": WEBHOOK_URL})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@flask_app.route("/webhook_info", methods=["GET"])
def webhook_info():
    """Kiểm tra trạng thái webhook hiện tại."""
    fut = asyncio.run_coroutine_threadsafe(
        _ptb_app.bot.get_webhook_info(), _loop
    )
    info = fut.result(timeout=10)
    return jsonify({
        "url":     info.url,
        "pending": info.pending_update_count,
        "error":   info.last_error_message,
    })


# ══════════════════════════════════════════════════════════════════════════════
# Local polling mode
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    log.info("🤖 Local polling mode...")
    # Stop background thread loop trước
    _loop.call_soon_threadsafe(_loop.stop)
    _thread.join(timeout=2)

    # Chạy polling bình thường
    app = _build_app()
    app.run_polling(allowed_updates=Update.ALL_TYPES)
