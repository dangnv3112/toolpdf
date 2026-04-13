#!/usr/bin/env python3
"""
bot.py — ICAGO Telegram Bot
=============================
Flow:
  User gửi file PDF lịch trình
  → Bot xử lý bằng icago_itinerary.py
  → Bot trả về file PDF đã format chuẩn ICAGO

Deploy:
  Local  : python bot.py
  Render : gunicorn bot:flask_app  (xem README_BOT.md)

Cài đặt:
  pip install python-telegram-bot reportlab pillow pdfplumber
"""

import os
import logging
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

# ── Core converter ─────────────────────────────────────────────────────────────
from icago_itinerary import convert, DEFAULT_LOGO, DEFAULT_LUUY

# ══════════════════════════════════════════════════════════════════════════════
# Config — lấy từ biến môi trường
# ══════════════════════════════════════════════════════════════════════════════

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8659136625:AAFcL4VweOqk5j6Sksu_HQCk3adz0bLH5gY")          # BẮT BUỘC
LOGO_PATH = os.environ.get("LOGO_PATH", DEFAULT_LOGO)
LUUY_PATH = os.environ.get("LUUY_PATH", DEFAULT_LUUY)
MAX_MB    = int(os.environ.get("MAX_MB", "20"))       # giới hạn file upload

if not BOT_TOKEN:
    raise SystemExit(
        "❌ Chưa set BOT_TOKEN.\n"
        "   Linux/macOS : export BOT_TOKEN='xxx'\n"
        "   Windows     : set BOT_TOKEN=xxx\n"
        "   Render      : Environment → BOT_TOKEN"
    )

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Handlers
# ══════════════════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Xin chào! Tôi là *ICAGO PDF Bot*.\n\n"
        "📎 Gửi file PDF lịch trình GDS/Amadeus, tôi sẽ trả về bản định dạng ICAGO chuẩn:\n"
        "  • Xóa địa chỉ đại lý, DATE\n"
        "  • In đậm FLIGHT / DEPARTURE / ARRIVAL\n"
        "  • Xóa CO2, URL Amadeus, Data Protection Notice\n"
        "  • Thêm logo ICAGO + ảnh Lưu ý\n"
        "  • Mỗi cụm FLIGHT luôn giữ nguyên trang\n\n"
        "📌 Lệnh: /start /help",
        parse_mode="Markdown",
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Cách dùng*\n\n"
        "1️⃣ Gửi file PDF lịch trình vào chat này\n"
        "2️⃣ Chờ vài giây\n"
        "3️⃣ Nhận lại file PDF đã format chuẩn ICAGO\n\n"
        f"⚠️ Giới hạn file: {MAX_MB} MB",
        parse_mode="Markdown",
    )


async def handle_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Xử lý khi user gửi file PDF."""
    doc = update.message.document

    # ── Kiểm tra định dạng ────────────────────────────────────────────────────
    if not doc.file_name.lower().endswith(".pdf"):
        await update.message.reply_text("❌ Vui lòng gửi file *PDF*.", parse_mode="Markdown")
        return

    # ── Kiểm tra kích thước ───────────────────────────────────────────────────
    if doc.file_size > MAX_MB * 1024 * 1024:
        await update.message.reply_text(f"❌ File quá lớn (tối đa {MAX_MB} MB).")
        return

    # ── Thông báo đang xử lý ──────────────────────────────────────────────────
    status_msg = await update.message.reply_text("⏳ Đang xử lý PDF...")
    user = update.effective_user
    log.info(f"Nhận file từ {user.full_name} (@{user.username}): {doc.file_name}")

    try:
        # ── Download file từ Telegram ─────────────────────────────────────────
        with tempfile.TemporaryDirectory() as tmp_dir:
            input_path  = os.path.join(tmp_dir, "input.pdf")
            output_name = Path(doc.file_name).stem + "_ICAGO.pdf"
            output_path = os.path.join(tmp_dir, output_name)

            tg_file = await ctx.bot.get_file(doc.file_id)
            await tg_file.download_to_drive(input_path)

            # ── Chạy converter ────────────────────────────────────────────────
            convert(
                input_path=input_path,
                output_path=output_path,
                logo_path=LOGO_PATH,
                luuy_path=LUUY_PATH,
                verbose=False,
            )

            size_kb = os.path.getsize(output_path) // 1024

            # ── Gửi kết quả ───────────────────────────────────────────────────
            with open(output_path, "rb") as f:
                await update.message.reply_document(
                    document=f,
                    filename=output_name,
                    caption=f"✅ Hoàn thành! ({size_kb} KB)",
                )

        await status_msg.delete()
        log.info(f"✅ Gửi thành công: {output_name} ({size_kb} KB)")

    except Exception as e:
        log.exception("Lỗi khi xử lý PDF")
        await status_msg.edit_text(f"❌ Lỗi xử lý:\n`{e}`", parse_mode="Markdown")


async def handle_other(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📎 Hãy gửi file PDF lịch trình để tôi xử lý.")


# ══════════════════════════════════════════════════════════════════════════════
# Khởi động bot
# ══════════════════════════════════════════════════════════════════════════════

def run_bot():
    log.info("🤖 Khởi động ICAGO Bot...")
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help",  cmd_help))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_other))

    log.info("✅ Bot đang chạy. Nhấn Ctrl+C để dừng.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


# ── Entry point cho Render (webhook mode) ─────────────────────────────────────
# Render chạy: gunicorn bot:flask_app
# Telegram gửi updates qua HTTPS webhook thay vì polling

RENDER_URL = os.environ.get("RENDER_URL", "")   # vd: https://icago-bot.onrender.com

if RENDER_URL:
    try:
        from flask import Flask, request as flask_request
        import asyncio, json

        flask_app = Flask(__name__)
        _tg_app   = Application.builder().token(BOT_TOKEN).build()

        _tg_app.add_handler(CommandHandler("start", cmd_start))
        _tg_app.add_handler(CommandHandler("help",  cmd_help))
        _tg_app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
        _tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_other))

        @flask_app.route(f"/{BOT_TOKEN}", methods=["POST"])
        def webhook():
            data = flask_request.get_json(force=True)
            update = Update.de_json(data, _tg_app.bot)
            asyncio.run(_tg_app.process_update(update))
            return "ok", 200

        @flask_app.route("/", methods=["GET"])
        def health():
            return "ICAGO Bot is running 🚀", 200

    except ImportError:
        pass   # Flask không cần khi chạy local


if __name__ == "__main__":
    run_bot()
