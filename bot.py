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
import threading
import time
from pathlib import Path

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("icago_bot")

# ── Telegram ───────────────────────────────────────────────────────────────────
try:
    from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import (
        Application, CommandHandler, MessageHandler,
        filters, ContextTypes, CallbackQueryHandler,
    )
except ImportError:
    sys.exit("❌ pip install python-telegram-bot")

# ── Flask ──────────────────────────────────────────────────────────────────────
try:
    from flask import Flask, request as flask_req, jsonify
except ImportError:
    sys.exit("❌ pip install flask")

# ── HTTP (self-ping) ───────────────────────────────────────────────────────────
try:
    import requests as _requests
except ImportError:
    _requests = None

# ── PDF converter ──────────────────────────────────────────────────────────────
try:
    from icago_itinerary import convert, DEFAULT_LOGO, DEFAULT_LUUY
except ImportError as e:
    sys.exit(f"❌ Không import được icago_itinerary: {e}")

# ── Word (.docx) converter ─────────────────────────────────────────────────────
try:
    from icago_word import convert_docx
    DOCX_ENABLED = True
    log.info("DOCX converter: ✅ enabled")
except ImportError:
    DOCX_ENABLED = False
    convert_docx = None
    log.info("DOCX converter: ⚠️ disabled (icago_word.py không tìm thấy)")

# ── PNR screenshot (không bắt buộc) ───────────────────────────────────────────
try:
    from pnr_screenshot import pnr_to_image
    PNR_ENABLED = True
    log.info("PNR screenshot: ✅ enabled")
except ImportError:
    PNR_ENABLED = False
    pnr_to_image = None
    log.info("PNR screenshot: ⚠️ disabled (pnr_screenshot.py không tìm thấy)")


# ══════════════════════════════════════════════════════════════════════════════
# Config từ biến môi trường
# ══════════════════════════════════════════════════════════════════════════════

BOT_TOKEN  = os.environ.get("BOT_TOKEN", "").strip()
LOGO_PATH  = os.environ.get("LOGO_PATH", DEFAULT_LOGO)
LUUY_PATH  = os.environ.get("LUUY_PATH", DEFAULT_LUUY)
MAX_MB     = int(os.environ.get("MAX_MB", "20"))
RENDER_URL = os.environ.get("RENDER_URL", "").rstrip("/")
PORT       = int(os.environ.get("PORT", "10000"))
# Interval tự ping (giây). Render sleep sau 15 phút → ping mỗi 10 phút
PING_INTERVAL = int(os.environ.get("PING_INTERVAL", str(10 * 60)))

if not BOT_TOKEN:
    sys.exit("❌ Chưa set BOT_TOKEN trong Environment Variables!")

WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
WEBHOOK_URL  = f"{RENDER_URL}{WEBHOOK_PATH}" if RENDER_URL else ""

# ── State lưu file đang chờ đặt tên (user_id → {file_id, file_name, is_docx, default_name}) ──
_pending_input: dict[int, dict] = {}


# ══════════════════════════════════════════════════════════════════════════════
# Self-ping — giữ Render không sleep
# ══════════════════════════════════════════════════════════════════════════════

def _self_ping_loop():
    """
    Gọi chính URL Render mỗi PING_INTERVAL giây để tránh bị sleep.
    Chạy trong daemon thread riêng — không ảnh hưởng bot.
    """
    if not RENDER_URL or not _requests:
        log.info("Self-ping: tắt (RENDER_URL chưa set hoặc thiếu requests)")
        return

    ping_url = f"{RENDER_URL}/"
    log.info(f"Self-ping: bắt đầu, interval={PING_INTERVAL}s → {ping_url}")

    # Chờ server khởi động hoàn tất rồi mới bắt đầu ping
    time.sleep(30)

    while True:
        try:
            r = _requests.get(ping_url, timeout=10)
            log.info(f"Self-ping: ✅ {r.status_code} ({r.elapsed.total_seconds():.1f}s)")
        except Exception as e:
            log.warning(f"Self-ping: ⚠️ lỗi — {e}")
        time.sleep(PING_INTERVAL)


def start_self_ping():
    t = threading.Thread(target=_self_ping_loop, daemon=True, name="self-ping")
    t.start()


# ══════════════════════════════════════════════════════════════════════════════
# Event loop riêng cho PTB (tránh conflict với gunicorn/Flask)
# ══════════════════════════════════════════════════════════════════════════════

_loop   = asyncio.new_event_loop()
_thread = threading.Thread(
    target=lambda: (_loop.run_forever()),
    daemon=True, name="ptb-loop"
)
_thread.start()


def run_async(coro, timeout=60):
    """Chạy coroutine trong PTB loop, block cho đến khi xong."""
    fut = asyncio.run_coroutine_threadsafe(coro, _loop)
    return fut.result(timeout=timeout)


# ══════════════════════════════════════════════════════════════════════════════
# Nhận dạng PNR
# ══════════════════════════════════════════════════════════════════════════════

def looks_like_pnr(text: str) -> bool:
    t = text.strip()
    if len(t) < 10:
        return False
    patterns = [
        r"\d+\.[A-Z]+/[A-Z]",       # 1.NGUYEN/VAN
        r"\b[A-Z]{2}\s+\d{2,4}\b",  # VN 363
        r"\b[A-Z]{6}\b",             # DFHBKI
        r"\b(HK|HL|RR|SS|UN)\d+\b", # HK1
        r"\b\d{1,2}[A-Z]{3}\b",     # 10JUL
    ]
    return sum(1 for p in patterns if re.search(p, t)) >= 2


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _sanitize_filename(name: str) -> str:
    """Làm sạch tên file, bỏ ký tự không hợp lệ."""
    safe = re.sub(r'[\\/*?:"<>|]', '_', name).strip().rstrip('.')
    return safe or "output"


# ══════════════════════════════════════════════════════════════════════════════
# Handlers
# ══════════════════════════════════════════════════════════════════════════════

HELP_TEXT = (
    "📖 *ICAGO Bot — Hướng dẫn*\n\n"
    "📎 *Gửi file PDF* lịch trình GDS/Amadeus\n"
    "→ Nhận PDF chuẩn ICAGO (logo, bold, bỏ rác)\n\n"
    "📝 *Gửi file Word (.docx)* lịch trình\n"
    "→ Nhận PDF chuẩn ICAGO (bỏ logo cũ, thêm ICAGO logo)\n\n"
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
    """
    Nhận file PDF hoặc DOCX.
    Hỏi tên file output TRƯỚC khi xử lý.
    """
    doc = update.message.document
    fname_lower = doc.file_name.lower()
    is_pdf  = fname_lower.endswith(".pdf")
    is_docx = fname_lower.endswith(".docx")

    if not is_pdf and not is_docx:
        await update.message.reply_text(
            "❌ Vui lòng gửi file *PDF* hoặc *Word (.docx)*.",
            parse_mode="Markdown"
        )
        return
    if is_docx and not DOCX_ENABLED:
        await update.message.reply_text("⚠️ Tính năng Word chưa sẵn sàng (thiếu icago_word.py).")
        return
    if doc.file_size > MAX_MB * 1024 * 1024:
        await update.message.reply_text(f"❌ File quá lớn (tối đa {MAX_MB} MB).")
        return

    user_id = update.effective_user.id
    file_type = "Word (.docx)" if is_docx else "PDF"
    default_name = Path(doc.file_name).stem + "_ICAGO"

    # Xóa pending cũ nếu có (user gửi file mới trước khi xử lý file cũ)
    _pending_input.pop(user_id, None)

    # Lưu thông tin file vào pending_input — chờ user đặt tên
    _pending_input[user_id] = {
        "file_id":    doc.file_id,
        "file_name":  doc.file_name,
        "is_docx":    is_docx,
        "file_type":  file_type,
        "default_name": default_name,
    }

    log.info(f"📥 Nhận {file_type}: {doc.file_name} từ {update.effective_user.full_name} — chờ đặt tên")

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            f"📄 Dùng tên mặc định: {default_name}.pdf",
            callback_data=f"use_default_name|{user_id}"
        )
    ]])
    await update.message.reply_text(
        f"✅ Đã nhận file *{doc.file_name}* ({file_type})\n\n"
        f"📝 *Nhập tên file output* (không cần đuôi `.pdf`)\n"
        f"hoặc nhấn nút bên dưới để dùng tên mặc định:",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


async def _process_and_send(update_or_query, ctx, user_id: int, output_name: str):
    """
    Download file, chuyển đổi, rồi gửi PDF về cho user.
    Dùng cho cả callback (nút mặc định) lẫn nhập tên thủ công.
    """
    pending = _pending_input.pop(user_id, None)
    if not pending:
        msg = "❌ Không tìm thấy file đang chờ. Vui lòng gửi lại file."
        if hasattr(update_or_query, 'message') and update_or_query.message:
            await update_or_query.message.reply_text(msg)
        else:
            await ctx.bot.send_message(chat_id=update_or_query.from_user.id, text=msg)
        return

    if not output_name.lower().endswith(".pdf"):
        output_name += ".pdf"

    file_type    = pending["file_type"]
    is_docx      = pending["is_docx"]
    original_name = pending["file_name"]

    # Thông báo đang xử lý
    if hasattr(update_or_query, 'message') and update_or_query.message:
        status = await update_or_query.message.reply_text(
            f"⏳ Đang xử lý *{file_type}*: `{original_name}` → `{output_name}`...",
            parse_mode="Markdown"
        )
        send_doc_to = update_or_query.message
    else:
        # Callback query
        await update_or_query.edit_message_text(
            f"⏳ Đang xử lý *{file_type}*: `{original_name}` → `{output_name}`...",
            parse_mode="Markdown"
        )
        status = None
        send_doc_to = None

    log.info(f"⚙️ Xử lý {file_type}: {original_name} → {output_name}")

    try:
        import tempfile as _tf
        tmp_obj = _tf.TemporaryDirectory()
        tmp = tmp_obj.name

        ext_in = ".docx" if is_docx else ".pdf"
        inp    = os.path.join(tmp, f"input{ext_in}")
        out    = os.path.join(tmp, output_name)

        # Download file từ Telegram
        tgf = await ctx.bot.get_file(pending["file_id"])
        await tgf.download_to_drive(inp)

        # Chuyển đổi
        if is_docx:
            convert_docx(inp, out, logo_path=LOGO_PATH, luuy_path=LUUY_PATH)
        else:
            convert(inp, out, logo_path=LOGO_PATH, luuy_path=LUUY_PATH)

        size_kb = os.path.getsize(out) // 1024

        # Gửi file PDF
        with open(out, "rb") as f:
            caption = f"✅ Hoàn thành! ({size_kb} KB)\n📄 {output_name}"
            if send_doc_to:
                await send_doc_to.reply_document(
                    document=f, filename=output_name, caption=caption
                )
            else:
                await ctx.bot.send_document(
                    chat_id=update_or_query.from_user.id,
                    document=open(out, "rb"),
                    filename=output_name,
                    caption=caption,
                )

        if status:
            await status.delete()

        log.info(f"✅ Đã gửi PDF: {output_name} ({size_kb} KB)")

    except Exception as e:
        log.exception("Lỗi xử lý/gửi file")
        err_msg = f"❌ Lỗi:\n`{e}`"
        if status:
            await status.edit_text(err_msg, parse_mode="Markdown")
        elif send_doc_to:
            await send_doc_to.reply_text(err_msg, parse_mode="Markdown")
        else:
            await ctx.bot.send_message(
                chat_id=update_or_query.from_user.id,
                text=err_msg, parse_mode="Markdown"
            )
    finally:
        try: tmp_obj.cleanup()
        except Exception: pass


async def handle_name_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Xử lý khi user nhấn nút 'Dùng tên mặc định'."""
    query = update.callback_query
    await query.answer()
    parts = query.data.split("|")
    if len(parts) < 2 or parts[0] != "use_default_name":
        return

    user_id = int(parts[1])
    pending = _pending_input.get(user_id)
    if not pending:
        await query.edit_message_text("❌ Phiên đã hết hạn. Vui lòng gửi lại file.")
        return

    output_name = pending["default_name"]
    await _process_and_send(query, ctx, user_id, output_name)


async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if not text:
        return

    user_id = update.effective_user.id

    # ── Ưu tiên 1: user đang nhập tên file output cho file vừa gửi ────────────
    if user_id in _pending_input:
        if not looks_like_pnr(text) and len(text) < 200 and '\n' not in text:
            safe_name = _sanitize_filename(text)
            if not safe_name:
                await update.message.reply_text("❌ Tên file không hợp lệ. Vui lòng nhập lại.")
                return
            await _process_and_send(update, ctx, user_id, safe_name)
            return

    # ── Ưu tiên 2: PNR ────────────────────────────────────────────────────────
    if not looks_like_pnr(text):
        await update.message.reply_text(
            "📎 Gửi *file PDF* hoặc *Word (.docx)* để chuyển đổi.\n/help để biết thêm.",
            parse_mode="Markdown",
        )
        return

    if not PNR_ENABLED:
        await update.message.reply_text("⚠️ Tính năng PNR chưa sẵn sàng.")
        return

    import asyncio as _asyncio
    log.info(f"PNR từ {update.effective_user.full_name}: {text[:60]}")
    status = await update.message.reply_text(
        "✈️ Đang xử lý PNR...\n_(10–20 giây)_", parse_mode="Markdown",
    )
    try:
        with tempfile.TemporaryDirectory() as tmp:
            img_path = os.path.join(tmp, "pnr_result.png")
            await _asyncio.get_event_loop().run_in_executor(
                None, lambda: pnr_to_image(text, img_path)
            )
            if not os.path.isfile(img_path) or os.path.getsize(img_path) < 1000:
                raise RuntimeError("Screenshot trống hoặc thất bại")
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
        "📎 Gửi *file PDF* hoặc *Word (.docx)*. /help",
        parse_mode="Markdown",
    )


# ══════════════════════════════════════════════════════════════════════════════
# PTB Application
# ══════════════════════════════════════════════════════════════════════════════

def _build_ptb() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help",  cmd_help))
    app.add_handler(CallbackQueryHandler(handle_name_callback, pattern=r"^use_default_name\|"))
    app.add_handler(MessageHandler(filters.Document.ALL,            handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.ALL,                     handle_other))
    return app


# Khởi tạo PTB trong loop riêng
_ptb = _build_ptb()
run_async(_ptb.initialize(), timeout=30)
run_async(_ptb.start(),      timeout=30)
log.info("✅ PTB initialized")

# Tự động set webhook
if WEBHOOK_URL:
    try:
        run_async(
            _ptb.bot.set_webhook(
                url=WEBHOOK_URL,
                allowed_updates=["message", "edited_message", "callback_query"],
                drop_pending_updates=True,
            ),
            timeout=20,
        )
        log.info(f"✅ Webhook set → {WEBHOOK_URL}")
    except Exception as e:
        log.error(f"⚠️ Set webhook lỗi: {e}")
else:
    log.warning("⚠️ RENDER_URL chưa set — bật /set_webhook sau khi có URL")

# Khởi động self-ping
start_self_ping()


# ══════════════════════════════════════════════════════════════════════════════
# Flask app (gunicorn bot:flask_app)
# ══════════════════════════════════════════════════════════════════════════════

flask_app = Flask(__name__)


@flask_app.route("/", methods=["GET"])
def health():
    """Health check + trạng thái webhook."""
    try:
        info = run_async(_ptb.bot.get_webhook_info(), timeout=10)
        wh   = info.url or "❌ chưa set"
        err  = info.last_error_message or "none"
        pending = info.pending_update_count
    except Exception as e:
        wh, err, pending = "error", str(e), -1

    return jsonify({
        "status":       "✅ running",
        "webhook":      wh,
        "pending":      pending,
        "last_error":   err,
        "pnr_enabled":  PNR_ENABLED,
        "docx_enabled": DOCX_ENABLED,
        "ping_interval": f"{PING_INTERVAL}s",
    })


@flask_app.route(WEBHOOK_PATH, methods=["POST"])
def webhook():
    """Nhận update từ Telegram."""
    data   = flask_req.get_json(force=True)
    update = Update.de_json(data, _ptb.bot)
    try:
        run_async(_ptb.process_update(update), timeout=60)
    except Exception as e:
        log.exception(f"Lỗi process_update: {e}")
    return "ok", 200


@flask_app.route("/set_webhook", methods=["GET"])
def set_webhook_route():
    """Set/reset webhook thủ công — truy cập URL này trên trình duyệt."""
    if not WEBHOOK_URL:
        return jsonify({"error": "RENDER_URL chưa set trong Environment"}), 400
    try:
        run_async(
            _ptb.bot.set_webhook(url=WEBHOOK_URL, drop_pending_updates=True),
            timeout=15,
        )
        return jsonify({"ok": True, "webhook": WEBHOOK_URL})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@flask_app.route("/ping", methods=["GET"])
def ping():
    """Endpoint đơn giản cho self-ping."""
    return "pong", 200


@flask_app.route("/debug_browserless", methods=["GET"])
def debug_browserless():
    """Test kết nối Browserless — truy cập URL này để kiểm tra."""
    token = os.environ.get("BROWSERLESS_TOKEN", "")
    if not token:
        return jsonify({"error": "BROWSERLESS_TOKEN chưa set"}), 400

    results = {}

    endpoints = {
        "v2_sfo":  f"wss://production-sfo.browserless.io?token={token}",
        "v2_lon":  f"wss://production-lon.browserless.io?token={token}",
        "v1_legacy": f"wss://chrome.browserless.io?token={token}",
    }

    async def _test_one(name, ws_url):
        try:
            from playwright.async_api import async_playwright
            async with async_playwright() as pw:
                browser = await pw.chromium.connect_over_cdp(ws_url, timeout=15_000)
                page = await (await browser.new_context()).new_page()
                await page.goto("https://example.com", timeout=10_000)
                title = await page.title()
                await browser.close()
                return {"ok": True, "title": title}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}

    async def _run_all():
        for name, url in endpoints.items():
            results[name] = await _test_one(name, url)

    try:
        asyncio.run(_run_all())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({
        "token_set": bool(token),
        "token_preview": token[:8] + "..." if token else "",
        "results": results,
    })


# ══════════════════════════════════════════════════════════════════════════════
# Local polling mode (python bot.py)
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Dừng PTB đã init ở trên, khởi động lại bằng run_polling
    run_async(_ptb.stop(), timeout=10)
    _loop.call_soon_threadsafe(_loop.stop)

    log.info("🤖 Local polling mode...")
    local_app = _build_ptb()
    local_app.run_polling(allowed_updates=Update.ALL_TYPES)
