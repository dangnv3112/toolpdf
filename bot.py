#!/usr/bin/env python3
"""
bot.py — ICAGO Telegram Bot
=============================
Flow 1 (PDF):
  User gửi file PDF lịch trình
  → Bot xử lý bằng icago_itinerary.py
  → Bot trả về file PDF đã format chuẩn ICAGO

Flow 2 (PNR Code):
  User dán mã PNR text
  → Bot mở pnrexpert.com bằng Playwright
  → Quick Convert → chụp ảnh (bỏ dòng CO2) → gửi ảnh Telegram

Deploy:
  Local  : python bot.py
  Render : gunicorn bot:flask_app  (set RENDER_URL trong Environment)

Cài đặt:
  pip install python-telegram-bot reportlab pillow pdfplumber playwright flask gunicorn
  playwright install chromium
"""

import os
import re
import logging
import tempfile
import asyncio
import threading
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
# Config
# ══════════════════════════════════════════════════════════════════════════════

BOT_TOKEN  = os.environ.get("BOT_TOKEN", "")
LOGO_PATH  = os.environ.get("LOGO_PATH", DEFAULT_LOGO)
LUUY_PATH  = os.environ.get("LUUY_PATH", DEFAULT_LUUY)
MAX_MB     = int(os.environ.get("MAX_MB", "20"))
RENDER_URL = os.environ.get("RENDER_URL", "").rstrip("/")

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
# Nhận diện mã PNR
# ══════════════════════════════════════════════════════════════════════════════

def looks_like_pnr(text: str) -> bool:
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    if len(lines) < 2:
        return False
    pnr_patterns = [
        r'\b[A-Z]{2}\d{2,4}\b',
        r'\b\d+\.[A-Z]+/[A-Z]+\b',
        r'\bHK\d+\b',
        r'\bRM\b|\bOSI\b|\bSSR\b',
        r'^\s*\d+\s+[A-Z]{2}\s+\d{3,4}',
    ]
    combined = '\n'.join(lines[:10])
    matches = sum(1 for p in pnr_patterns if re.search(p, combined))
    return matches >= 2


# ══════════════════════════════════════════════════════════════════════════════
# PNR → pnrexpert.com → Screenshot
# ══════════════════════════════════════════════════════════════════════════════

async def pnr_to_screenshot(pnr_text: str, output_png: str) -> str:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise RuntimeError(
            "Chưa cài Playwright:\n"
            "pip install playwright && playwright install chromium"
        )

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        page = await browser.new_page(viewport={"width": 1280, "height": 900})

        log.info("🌐 Mở pnrexpert.com...")
        await page.goto("https://www.pnrexpert.com/", timeout=30000)
        await page.wait_for_load_state("networkidle", timeout=20000)

        log.info("📋 Dán mã PNR...")
        textarea = await page.wait_for_selector(
            "textarea, [placeholder*='PNR'], [placeholder*='pnr']",
            timeout=10000,
        )
        await textarea.click()
        await textarea.fill(pnr_text)
        await asyncio.sleep(0.5)

        log.info("🔄 Nhấn Quick Convert...")
        quick_btn = None
        for sel in [
            "button:has-text('Quick Convert')",
            "button:has-text('Quick')",
            "input[value*='Quick']",
        ]:
            try:
                quick_btn = await page.wait_for_selector(sel, timeout=5000)
                if quick_btn:
                    break
            except Exception:
                continue

        if not quick_btn:
            raise RuntimeError("Không tìm thấy nút Quick Convert.")

        await quick_btn.click()

        log.info("⏳ Chờ kết quả render...")
        await asyncio.sleep(4)

        # Ẩn dòng CO2 bằng JS
        await page.evaluate("""
            () => {
                const keywords = ['CO2', 'Tonnes', 'tonne', 'carbon', 'CARBON', 'CO\u2082'];
                document.querySelectorAll('*').forEach(el => {
                    if (el.children.length === 0) {
                        const txt = (el.textContent || '').trim();
                        if (keywords.some(k => txt.includes(k))) {
                            let node = el;
                            for (let i = 0; i < 5; i++) {
                                if (!node) break;
                                const tag = node.tagName;
                                if (['TR','LI','DIV','P','SPAN','TD'].includes(tag)) {
                                    node.style.setProperty('display', 'none', 'important');
                                    break;
                                }
                                node = node.parentElement;
                            }
                        }
                    }
                });
            }
        """)
        await asyncio.sleep(0.3)

        log.info("📸 Chụp screenshot...")
        await page.screenshot(path=output_png, full_page=True)
        await browser.close()
        log.info(f"✅ Screenshot: {output_png}")
        return output_png


# ══════════════════════════════════════════════════════════════════════════════
# Handlers
# ══════════════════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Xin chào! Tôi là *ICAGO PDF Bot*.\n\n"
        "📎 *Gửi file PDF* lịch trình → nhận PDF chuẩn ICAGO\n\n"
        "✈️ *Dán mã PNR* (text) → tôi tự convert trên pnrexpert.com và gửi ảnh\n\n"
        "📌 /help để xem hướng dẫn",
        parse_mode="Markdown",
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Cách dùng*\n\n"
        "*1️⃣ PDF Mode:*\n"
        "   Gửi file PDF lịch trình → nhận PDF chuẩn ICAGO\n\n"
        "*2️⃣ PNR Code Mode:*\n"
        "   Dán mã PNR GDS/Amadeus vào chat\n"
        "   Bot tự convert trên pnrexpert.com và gửi ảnh kết quả\n\n"
        f"⚠️ Giới hạn file: {MAX_MB} MB",
        parse_mode="Markdown",
    )


async def handle_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document

    if not doc.file_name.lower().endswith(".pdf"):
        await update.message.reply_text("❌ Vui lòng gửi file *PDF*.", parse_mode="Markdown")
        return

    if doc.file_size > MAX_MB * 1024 * 1024:
        await update.message.reply_text(f"❌ File quá lớn (tối đa {MAX_MB} MB).")
        return

    status_msg = await update.message.reply_text("⏳ Đang xử lý PDF...")
    user = update.effective_user
    log.info(f"PDF từ {user.full_name}: {doc.file_name}")

    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            input_path  = os.path.join(tmp_dir, "input.pdf")
            output_name = Path(doc.file_name).stem + "_ICAGO.pdf"
            output_path = os.path.join(tmp_dir, output_name)

            tg_file = await ctx.bot.get_file(doc.file_id)
            await tg_file.download_to_drive(input_path)

            convert(
                input_path=input_path,
                output_path=output_path,
                logo_path=LOGO_PATH,
                luuy_path=LUUY_PATH,
                verbose=False,
            )

            size_kb = os.path.getsize(output_path) // 1024
            with open(output_path, "rb") as f:
                await update.message.reply_document(
                    document=f,
                    filename=output_name,
                    caption=f"✅ Hoàn thành! ({size_kb} KB)",
                )

        await status_msg.delete()

    except Exception as e:
        log.exception("Lỗi PDF")
        await status_msg.edit_text(f"❌ Lỗi:\n`{e}`", parse_mode="Markdown")


async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    if looks_like_pnr(text):
        await handle_pnr(update, ctx, text)
    else:
        await update.message.reply_text(
            "📎 Gửi *file PDF* hoặc dán *mã PNR* để tôi xử lý.\n/help để xem hướng dẫn.",
            parse_mode="Markdown",
        )


async def handle_pnr(update: Update, ctx: ContextTypes.DEFAULT_TYPE, pnr_text: str):
    user = update.effective_user
    log.info(f"PNR từ {user.full_name}: {pnr_text[:60]}...")

    status_msg = await update.message.reply_text("✈️ Đang mở pnrexpert.com và convert...")

    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_png = os.path.join(tmp_dir, "pnr_result.png")
            await pnr_to_screenshot(pnr_text, output_png)

            size_kb = os.path.getsize(output_png) // 1024
            with open(output_png, "rb") as f:
                await update.message.reply_photo(
                    photo=f,
                    caption="✅ Kết quả pnrexpert.com (đã ẩn dòng CO2)",
                )

        await status_msg.delete()
        log.info(f"✅ Gửi ảnh PNR ({size_kb} KB)")

    except Exception as e:
        log.exception("Lỗi PNR")
        await status_msg.edit_text(f"❌ Lỗi xử lý PNR:\n`{e}`", parse_mode="Markdown")


# ══════════════════════════════════════════════════════════════════════════════
# Build Application
# ══════════════════════════════════════════════════════════════════════════════

def _build_app() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help",  cmd_help))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    return app


# ══════════════════════════════════════════════════════════════════════════════
# LOCAL — polling
# ══════════════════════════════════════════════════════════════════════════════

def run_bot():
    log.info("🤖 ICAGO Bot khởi động (polling)...")
    _build_app().run_polling(allowed_updates=Update.ALL_TYPES)


# ══════════════════════════════════════════════════════════════════════════════
# RENDER — webhook qua Flask
# gunicorn bot:flask_app
# ══════════════════════════════════════════════════════════════════════════════

try:
    from flask import Flask, request as flask_request

    flask_app = Flask(__name__)

    # --- Event loop nền để chạy async handlers ---
    _loop = asyncio.new_event_loop()

    def _run_loop(loop):
        asyncio.set_event_loop(loop)
        loop.run_forever()

    threading.Thread(target=_run_loop, args=(_loop,), daemon=True).start()

    # --- PTB Application khởi tạo trong loop nền ---
    _tg_app = _build_app()
    asyncio.run_coroutine_threadsafe(_tg_app.initialize(), _loop).result(timeout=10)

    @flask_app.route(f"/{BOT_TOKEN}", methods=["POST"])
    def webhook():
        data   = flask_request.get_json(force=True)
        update = Update.de_json(data, _tg_app.bot)
        future = asyncio.run_coroutine_threadsafe(
            _tg_app.process_update(update), _loop
        )
        future.result(timeout=60)   # chờ tối đa 60s
        return "ok", 200

    @flask_app.route("/", methods=["GET"])
    def health():
        return "ICAGO Bot is running 🚀", 200

    @flask_app.route("/set_webhook", methods=["GET"])
    def set_webhook():
        """Truy cập URL này một lần sau khi deploy để đăng ký webhook."""
        import urllib.request, json as _json
        if not RENDER_URL:
            return "❌ RENDER_URL chưa set trong Environment Variables", 400
        wh_url = (
            f"https://api.telegram.org/bot{BOT_TOKEN}"
            f"/setWebhook?url={RENDER_URL}/{BOT_TOKEN}"
        )
        with urllib.request.urlopen(wh_url) as r:
            result = _json.loads(r.read())
        log.info(f"setWebhook: {result}")
        return f"✅ Webhook đã đăng ký: {result}", 200

except ImportError:
    flask_app = None
    log.warning("Flask không có → chỉ dùng polling local")


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    run_bot()
