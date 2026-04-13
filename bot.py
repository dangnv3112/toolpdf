#!/usr/bin/env python3
"""
bot.py — ICAGO Telegram Bot
Chạy local  : python bot.py
Deploy Render: gunicorn bot:flask_app
"""

import asyncio
import logging
import os
import sys
import tempfile
from pathlib import Path

# ── Telegram ───────────────────────────────────────────────────────────────────
try:
    from telegram import Update
    from telegram.ext import (
        Application, CommandHandler, MessageHandler,
        filters, ContextTypes
    )
except ImportError:
    sys.exit("❌ pip install python-telegram-bot")

# ── PDF converter (bắt buộc) ──────────────────────────────────────────────────
try:
    from icago_itinerary import convert, DEFAULT_LOGO, DEFAULT_LUUY
except ImportError as e:
    sys.exit(f"❌ Không import được icago_itinerary: {e}")

# ── PNR screenshot (tùy chọn — không crash nếu thiếu) ────────────────────────
try:
    from pnr_screenshot import pnr_to_image
    PNR_ENABLED = True
except ImportError:
    PNR_ENABLED = False
    pnr_to_image = None

# ══════════════════════════════════════════════════════════════════════════════
# Config từ biến môi trường
# ══════════════════════════════════════════════════════════════════════════════

BOT_TOKEN  = os.environ.get("BOT_TOKEN", "8659136625:AAFcL4VweOqk5j6Sksu_HQCk3adz0bLH5gY")
LOGO_PATH  = os.environ.get("LOGO_PATH", DEFAULT_LOGO)
LUUY_PATH  = os.environ.get("LUUY_PATH", DEFAULT_LUUY)
MAX_MB     = int(os.environ.get("MAX_MB", "20"))
RENDER_URL = os.environ.get("RENDER_URL", "")
PORT       = int(os.environ.get("PORT", "10000"))   # Render tự set PORT

if not BOT_TOKEN:
    sys.exit(
        "❌ Chưa set BOT_TOKEN.\n"
        "   Windows     : set BOT_TOKEN=xxx\n"
        "   macOS/Linux : export BOT_TOKEN=xxx\n"
        "   Render      : Environment → BOT_TOKEN"
    )

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("icago_bot")
log.info(f"PNR screenshot: {'✅ enabled' if PNR_ENABLED else '⚠️ disabled (pnr_screenshot not found)'}")


# ══════════════════════════════════════════════════════════════════════════════
# Nhận dạng PNR
# ══════════════════════════════════════════════════════════════════════════════

import re

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
    "→ Nhận PDF chuẩn ICAGO (logo, lưu ý, bold, bỏ rác)\n\n"
    + (
        "✈️ *Gửi mã PNR* (text từ GDS)\n"
        "→ Nhận ảnh lịch trình từ pnrexpert.com (bỏ dòng CO₂)\n\n"
        if PNR_ENABLED else
        "_(Tính năng PNR screenshot chưa được cấu hình)_\n\n"
    ) +
    "📌 Lệnh: /start  /help"
)


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Xin chào! Tôi là *ICAGO Bot*.\n\n" + HELP_TEXT,
        parse_mode="Markdown",
    )

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")


# ── PDF handler ───────────────────────────────────────────────────────────────

async def handle_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc.file_name.lower().endswith(".pdf"):
        await update.message.reply_text("❌ Vui lòng gửi file *PDF*.", parse_mode="Markdown")
        return
    if doc.file_size > MAX_MB * 1024 * 1024:
        await update.message.reply_text(f"❌ File quá lớn (tối đa {MAX_MB} MB).")
        return

    status = await update.message.reply_text("⏳ Đang xử lý PDF...")
    log.info(f"PDF: {doc.file_name} từ {update.effective_user.full_name}")

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


# ── PNR handler ───────────────────────────────────────────────────────────────

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if not text:
        return

    if not looks_like_pnr(text):
        await update.message.reply_text(
            "📎 Gửi *file PDF* hoặc *mã PNR*. Dùng /help để biết thêm.",
            parse_mode="Markdown",
        )
        return

    if not PNR_ENABLED:
        await update.message.reply_text(
            "⚠️ Tính năng PNR chưa sẵn sàng trên server này.",
            parse_mode="Markdown",
        )
        return

    log.info(f"PNR từ {update.effective_user.full_name}: {text[:60]}...")
    status = await update.message.reply_text(
        "✈️ Đang xử lý PNR trên pnrexpert.com...\n_(10–20 giây)_",
        parse_mode="Markdown",
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
        await status.edit_text(f"❌ Lỗi PNR:\n`{e}`", parse_mode="Markdown")


async def handle_other(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📎 Gửi *file PDF* hoặc *mã PNR*. /help",
        parse_mode="Markdown",
    )


# ══════════════════════════════════════════════════════════════════════════════
# Build app
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
# Flask app cho Render (gunicorn bot:flask_app)
# ══════════════════════════════════════════════════════════════════════════════

from flask import Flask, request as flask_req, jsonify

flask_app = Flask(__name__)
_tg_app   = _build_app()

@flask_app.route("/", methods=["GET"])
def health():
    return jsonify({
        "status": "running",
        "pnr_enabled": PNR_ENABLED,
        "webhook_mode": bool(RENDER_URL),
    })

@flask_app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    data   = flask_req.get_json(force=True)
    update = Update.de_json(data, _tg_app.bot)
    asyncio.run(_tg_app.process_update(update))
    return "ok", 200

# Set webhook tự động khi Render khởi động
@flask_app.before_request
def _once():
    pass  # webhook được set qua URL thủ công (xem README)


# ══════════════════════════════════════════════════════════════════════════════
# Local polling mode
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    if RENDER_URL:
        # Render: chạy gunicorn — không dùng __main__
        log.info("Render mode: dùng 'gunicorn bot:flask_app'")
    else:
        log.info("Local polling mode...")
        _build_app().run_polling(allowed_updates=Update.ALL_TYPES)
