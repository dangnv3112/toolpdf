#!/usr/bin/env python3
"""
bot.py — ICAGO Telegram Bot
=============================
Hỗ trợ 2 chức năng:

  📎 Gửi file PDF lịch trình  → Bot chuyển sang định dạng ICAGO chuẩn
  ✈️  Gửi mã PNR (text)       → Bot dán vào pnrexpert.com, Quick Convert,
                                 chụp ảnh kết quả (bỏ dòng Co2) và gửi lại

Cài đặt:
  pip install python-telegram-bot reportlab pillow pdfplumber playwright
  python -m playwright install chromium

Chạy local:
  set BOT_TOKEN=xxx        (Windows)
  export BOT_TOKEN=xxx     (macOS/Linux)
  python bot.py

Deploy Render:
  Start command : gunicorn bot:flask_app
  Env vars      : BOT_TOKEN, RENDER_URL
"""

import asyncio
import logging
import os
import re
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
    raise SystemExit("❌ pip install python-telegram-bot")

# ── Core modules ───────────────────────────────────────────────────────────────
from icago_itinerary import convert, DEFAULT_LOGO, DEFAULT_LUUY
from pnr_screenshot   import pnr_to_image

# ══════════════════════════════════════════════════════════════════════════════
# Config
# ══════════════════════════════════════════════════════════════════════════════

BOT_TOKEN  = os.environ.get("BOT_TOKEN", "")
LOGO_PATH  = os.environ.get("LOGO_PATH", DEFAULT_LOGO)
LUUY_PATH  = os.environ.get("LUUY_PATH", DEFAULT_LUUY)
MAX_MB     = int(os.environ.get("MAX_MB", "20"))
RENDER_URL = os.environ.get("RENDER_URL", "")

if not BOT_TOKEN:
    raise SystemExit(
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


# ══════════════════════════════════════════════════════════════════════════════
# Nhận dạng PNR
# ══════════════════════════════════════════════════════════════════════════════

def looks_like_pnr(text: str) -> bool:
    """
    Heuristic: văn bản được coi là PNR nếu có ít nhất một trong các dấu hiệu:
      - Dòng có dạng  "1.LASTNAME/FIRSTNAME"
      - Dòng chứa mã hãng bay 2 ký tự + số chuyến bay (vd: BA 284, UA 7941)
      - Có booking ref dạng 6 ký tự chữ in hoa (vd: DFHBKI)
      - Nhiều dòng liên tiếp viết hoa toàn bộ (đặc trưng PNR)
    """
    t = text.strip()
    if len(t) < 10:
        return False

    patterns = [
        r"\d+\.[A-Z]+/[A-Z]",            # 1.SMITH/JOHN
        r"\b[A-Z]{2}\s+\d{2,4}\b",       # BA 284 / UA 7941
        r"\b[A-Z]{6}\b",                  # BOOKING REF 6 chars
        r"\b(HK|HL|RR|SS|UN)\d+\b",      # GDS status codes
        r"\b\d{1,2}[A-Z]{3}\b",          # ngày kiểu GDS: 10APR 07JUL
    ]
    matches = sum(1 for p in patterns if re.search(p, t))
    return matches >= 2


# ══════════════════════════════════════════════════════════════════════════════
# Handlers
# ══════════════════════════════════════════════════════════════════════════════

HELP_TEXT = (
    "📖 *Hướng dẫn dùng ICAGO Bot*\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n"
    "📎 *Chuyển đổi PDF lịch trình*\n"
    "Gửi file PDF từ GDS/Amadeus → nhận lại PDF chuẩn ICAGO\n\n"
    "✈️ *Xem lịch trình từ mã PNR*\n"
    "Paste mã PNR vào đây → nhận ảnh lịch trình từ pnrexpert.com\n"
    "_(Bỏ qua dòng CO₂)_\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n"
    "Ví dụ PNR:\n"
    "`1.NGUYEN/VAN A`\n"
    "`3 VN 363 H 10JUL 4*SGNHAN HK1 0700 0900`\n\n"
    "📌 Lệnh: /start /help"
)


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Xin chào! Tôi là *ICAGO Bot*.\n\n" + HELP_TEXT,
        parse_mode="Markdown",
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")


# ── Handler: nhận file PDF ────────────────────────────────────────────────────

async def handle_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document

    if not doc.file_name.lower().endswith(".pdf"):
        await update.message.reply_text("❌ Vui lòng gửi file *PDF*.", parse_mode="Markdown")
        return

    if doc.file_size > MAX_MB * 1024 * 1024:
        await update.message.reply_text(f"❌ File quá lớn (tối đa {MAX_MB} MB).")
        return

    status = await update.message.reply_text("⏳ Đang xử lý PDF...")
    user   = update.effective_user
    log.info(f"PDF từ {user.full_name}: {doc.file_name}")

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
                    document=f,
                    filename=name,
                    caption=f"✅ Hoàn thành!  ({os.path.getsize(out)//1024} KB)",
                )
        await status.delete()
        log.info(f"✅ PDF xong: {name}")

    except Exception as e:
        log.exception("Lỗi PDF")
        await status.edit_text(f"❌ Lỗi:\n`{e}`", parse_mode="Markdown")


# ── Handler: nhận mã PNR (text) ───────────────────────────────────────────────

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()

    if not text:
        return

    if not looks_like_pnr(text):
        await update.message.reply_text(
            "📎 Gửi *file PDF* để chuyển đổi lịch trình ICAGO.\n"
            "✈️ Hoặc gửi *mã PNR* để xem lịch trình từ pnrexpert.com.\n\n"
            "Dùng /help để biết thêm.",
            parse_mode="Markdown",
        )
        return

    user   = update.effective_user
    log.info(f"PNR từ {user.full_name}: {text[:60]}...")
    status = await update.message.reply_text(
        "✈️ Đang xử lý mã PNR trên pnrexpert.com...\n"
        "_(Thường mất 10–20 giây)_",
        parse_mode="Markdown",
    )

    try:
        with tempfile.TemporaryDirectory() as tmp:
            img_path = os.path.join(tmp, "pnr_result.png")

            # Chạy playwright trong thread riêng để không block event loop
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: _sync_pnr(text, img_path)
            )

            if not os.path.isfile(img_path) or os.path.getsize(img_path) < 1000:
                raise RuntimeError("Screenshot trống hoặc thất bại")

            with open(img_path, "rb") as f:
                await update.message.reply_photo(
                    photo=f,
                    caption="✅ Lịch trình từ pnrexpert.com",
                )

        await status.delete()
        log.info("✅ PNR screenshot gửi thành công")

    except Exception as e:
        log.exception("Lỗi PNR screenshot")
        await status.edit_text(
            f"❌ Không thể lấy kết quả từ pnrexpert.com.\n`{e}`",
            parse_mode="Markdown",
        )


def _sync_pnr(pnr_text: str, output_path: str):
    """Wrapper đồng bộ để gọi trong executor."""
    pnr_to_image(pnr_text, output_path, verbose=True)


async def handle_other(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📎 Gửi *file PDF* hoặc *mã PNR*. Dùng /help để biết thêm.",
        parse_mode="Markdown",
    )


# ══════════════════════════════════════════════════════════════════════════════
# App builder
# ══════════════════════════════════════════════════════════════════════════════

def _build_app() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help",  cmd_help))
    app.add_handler(MessageHandler(filters.Document.ALL,               handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,    handle_text))
    app.add_handler(MessageHandler(filters.ALL,                        handle_other))
    return app


# ══════════════════════════════════════════════════════════════════════════════
# Render webhook mode
# ══════════════════════════════════════════════════════════════════════════════

flask_app = None

if RENDER_URL:
    try:
        from flask import Flask, request as flask_req
        import json

        flask_app = Flask(__name__)
        _tg_app   = _build_app()

        @flask_app.route(f"/{BOT_TOKEN}", methods=["POST"])
        def webhook():
            data   = flask_req.get_json(force=True)
            update = Update.de_json(data, _tg_app.bot)
            asyncio.run(_tg_app.process_update(update))
            return "ok", 200

        @flask_app.route("/", methods=["GET"])
        def health():
            return "ICAGO Bot 🚀 running", 200

    except ImportError:
        pass

if flask_app is None:
    # Dummy flask_app để gunicorn không crash nếu RENDER_URL chưa set
    try:
        from flask import Flask
        flask_app = Flask(__name__)

        @flask_app.route("/")
        def _health():
            return "Set RENDER_URL env var to enable webhook mode.", 200
    except ImportError:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

def run_bot():
    log.info("🤖 Khởi động ICAGO Bot (polling mode)...")
    app = _build_app()
    log.info("✅ Bot đang chạy. Nhấn Ctrl+C để dừng.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    run_bot()
