#!/usr/bin/env python3
"""
bot.py — ICAGO Telegram Bot
=============================
Flow 1 (PDF):
  User gửi file PDF lịch trình → nhận PDF chuẩn ICAGO

Flow 2 (PNR Code):
  User dán mã PNR text
  → Gọi API pnrexpert.com (không cần browser/Chromium)
  → Render HTML kết quả thành ảnh PNG bằng html2image / imgkit
  → Bỏ dòng CO2 → gửi ảnh Telegram

Deploy Render:
  Build Command : pip install -r requirements.txt
  Start Command : gunicorn bot:flask_app
  Environment   : BOT_TOKEN, RENDER_URL

Cài đặt local:
  pip install python-telegram-bot reportlab pillow pdfplumber
               flask gunicorn requests beautifulsoup4 imgkit
  + cài wkhtmltoimage: https://wkhtmltopdf.org/downloads.html
"""

import os, re, logging, tempfile, asyncio, threading, json
from pathlib import Path

# ── Telegram ──────────────────────────────────────────────────────────────────
try:
    from telegram import Update
    from telegram.ext import (
        Application, CommandHandler, MessageHandler,
        filters, ContextTypes
    )
except ImportError:
    raise SystemExit("❌ pip install python-telegram-bot")

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
    raise SystemExit("❌ Chưa set BOT_TOKEN.")

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Nhận diện mã PNR
# ══════════════════════════════════════════════════════════════════════════════

def looks_like_pnr(text: str) -> bool:
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    if len(lines) < 2:
        return False
    patterns = [
        r'\b[A-Z]{2}\d{2,4}\b',
        r'\b\d+\.[A-Z]+/[A-Z]+\b',
        r'\bHK\d+\b',
        r'\bRM\b|\bOSI\b|\bSSR\b',
        r'^\s*\d+\s+[A-Z]{2}\s+\d{3,4}',
    ]
    combined = '\n'.join(lines[:10])
    return sum(1 for p in patterns if re.search(p, combined)) >= 2


# ══════════════════════════════════════════════════════════════════════════════
# PNR → pnrexpert.com → PNG  (không dùng browser)
# ══════════════════════════════════════════════════════════════════════════════

def _fetch_pnr_html(pnr_text: str) -> str:
    """
    Gửi PNR lên pnrexpert.com qua HTTP POST (giống như nhấn Quick Convert).
    Trả về HTML của phần kết quả.
    """
    import requests
    from bs4 import BeautifulSoup

    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.pnrexpert.com/",
        "Origin":  "https://www.pnrexpert.com",
    })

    # Bước 1: Load trang để lấy token / cookie
    r = session.get("https://www.pnrexpert.com/", timeout=20)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")

    # Lấy CSRF token nếu có
    csrf_input = soup.find("input", {"name": re.compile(r"csrf|token", re.I)})
    csrf_value = csrf_input["value"] if csrf_input else ""

    # Bước 2: POST PNR (quick convert endpoint)
    # pnrexpert.com dùng AJAX — thử endpoint phổ biến
    endpoints = [
        "https://www.pnrexpert.com/api/convert",
        "https://www.pnrexpert.com/convert",
        "https://www.pnrexpert.com/",
    ]

    payload = {
        "pnr":    pnr_text,
        "layout": "3lines",   # layout mặc định free
        "action": "quick",
        "_token": csrf_value,
    }

    result_html = None
    for ep in endpoints:
        try:
            resp = session.post(ep, data=payload, timeout=30)
            if resp.ok and len(resp.text) > 200:
                result_html = resp.text
                log.info(f"✅ PNR convert thành công từ {ep}")
                break
        except Exception as e:
            log.warning(f"Endpoint {ep} lỗi: {e}")

    if not result_html:
        raise RuntimeError(
            "Không thể lấy kết quả từ pnrexpert.com.\n"
            "Trang có thể yêu cầu JavaScript. Xem hướng dẫn bên dưới."
        )

    return result_html


def _html_to_png_imgkit(html: str, output_png: str):
    """Dùng imgkit (wkhtmltoimage) để render HTML → PNG."""
    import imgkit
    options = {
        "format":        "png",
        "width":         "900",
        "quiet":         "",
        "disable-javascript": "",
    }
    imgkit.from_string(html, output_png, options=options)


def _html_to_png_pillow(html_content: str, output_png: str):
    """
    Fallback: parse HTML bằng BeautifulSoup, vẽ text bằng Pillow.
    Đơn giản nhưng không cần wkhtmltoimage.
    """
    from bs4 import BeautifulSoup
    from PIL import Image, ImageDraw, ImageFont

    soup = BeautifulSoup(html_content, "html.parser")

    # Xóa phần CO2 trước khi lấy text
    for tag in soup.find_all(string=re.compile(r"CO2|Tonnes|tonne|carbon", re.I)):
        parent = tag.parent
        for _ in range(5):
            if parent and parent.name in ("tr", "li", "div", "p", "td", "span"):
                parent.decompose()
                break
            parent = parent.parent if parent else None

    # Lấy text sạch từ phần itinerary
    result_div = (
        soup.find(class_=re.compile(r"itinerary|result|preview|output", re.I))
        or soup.find("body")
        or soup
    )
    lines = [l for l in result_div.get_text("\n").splitlines() if l.strip()]

    # Vẽ lên ảnh
    font_size  = 14
    line_h     = font_size + 6
    width      = 900
    padding    = 20
    height     = max(400, len(lines) * line_h + padding * 2)

    img  = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", font_size)
        bold = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", font_size)
    except Exception:
        font = bold = ImageFont.load_default()

    y = padding
    bold_kw = re.compile(r"FLIGHT|DEPARTURE|ARRIVAL|OUTBOUND|RETURN", re.I)
    for line in lines:
        f = bold if bold_kw.search(line) else font
        draw.text((padding, y), line, fill="black", font=f)
        y += line_h
        if y > height - padding:
            break

    img.save(output_png, "PNG")


def _remove_co2_from_html(html: str) -> str:
    """Xóa dòng/block chứa CO2 khỏi HTML trước khi render."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")

    keywords = re.compile(r"CO2|Tonnes|tonne|carbon emission|CARBON", re.I)
    removed = 0
    for tag in soup.find_all(string=keywords):
        parent = tag.parent
        for _ in range(6):
            if not parent:
                break
            if parent.name in ("tr", "li", "div", "p", "td", "section", "span"):
                parent.decompose()
                removed += 1
                break
            parent = parent.parent
    log.info(f"🧹 Đã xóa {removed} block CO2")
    return str(soup)


async def pnr_to_image(pnr_text: str, output_png: str) -> str:
    """
    Tổng hợp: lấy HTML từ pnrexpert.com → bỏ CO2 → render PNG.
    Chạy blocking IO trong executor để không block event loop.
    """
    loop = asyncio.get_event_loop()

    def _sync():
        html = _fetch_pnr_html(pnr_text)
        html = _remove_co2_from_html(html)

        # Thử imgkit trước, fallback Pillow
        try:
            _html_to_png_imgkit(html, output_png)
            log.info("✅ Render bằng imgkit")
        except Exception as e1:
            log.warning(f"imgkit lỗi ({e1}), dùng Pillow fallback...")
            _html_to_png_pillow(html, output_png)
            log.info("✅ Render bằng Pillow")

        return output_png

    return await loop.run_in_executor(None, _sync)


# ══════════════════════════════════════════════════════════════════════════════
# Handlers
# ══════════════════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Xin chào! Tôi là *ICAGO PDF Bot*.\n\n"
        "📎 *Gửi file PDF* lịch trình → nhận PDF chuẩn ICAGO\n\n"
        "✈️ *Dán mã PNR* (text GDS/Amadeus) → tôi convert trên pnrexpert.com và gửi ảnh\n\n"
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
        "   Bot tự convert và gửi ảnh kết quả (đã ẩn CO2)\n\n"
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
    log.info(f"PDF từ {update.effective_user.full_name}: {doc.file_name}")

    try:
        with tempfile.TemporaryDirectory() as tmp:
            inp = os.path.join(tmp, "input.pdf")
            out_name = Path(doc.file_name).stem + "_ICAGO.pdf"
            out = os.path.join(tmp, out_name)

            tg_file = await ctx.bot.get_file(doc.file_id)
            await tg_file.download_to_drive(inp)
            convert(inp, out, logo_path=LOGO_PATH, luuy_path=LUUY_PATH, verbose=False)

            with open(out, "rb") as f:
                await update.message.reply_document(
                    document=f, filename=out_name,
                    caption=f"✅ Hoàn thành! ({os.path.getsize(out)//1024} KB)",
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
    log.info(f"PNR từ {update.effective_user.full_name}: {pnr_text[:50]}...")
    status_msg = await update.message.reply_text("✈️ Đang convert PNR trên pnrexpert.com...")

    try:
        with tempfile.TemporaryDirectory() as tmp:
            out_png = os.path.join(tmp, "pnr_result.png")
            await pnr_to_image(pnr_text, out_png)

            with open(out_png, "rb") as f:
                await update.message.reply_photo(
                    photo=f,
                    caption="✅ Kết quả từ pnrexpert.com (đã ẩn dòng CO2)",
                )
        await status_msg.delete()
    except Exception as e:
        log.exception("Lỗi PNR")
        await status_msg.edit_text(f"❌ Lỗi xử lý PNR:\n`{e}`", parse_mode="Markdown")


# ══════════════════════════════════════════════════════════════════════════════
# Build PTB Application
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
# RENDER — Flask webhook (gunicorn bot:flask_app)
# ══════════════════════════════════════════════════════════════════════════════

try:
    from flask import Flask, request as flask_request

    flask_app = Flask(__name__)

    # Event loop nền
    _loop = asyncio.new_event_loop()
    threading.Thread(
        target=lambda: (_loop.run_forever()),
        daemon=True
    ).start()

    # Khởi tạo PTB trong loop nền
    _tg_app = _build_app()
    asyncio.run_coroutine_threadsafe(_tg_app.initialize(), _loop).result(timeout=15)

    @flask_app.route(f"/{BOT_TOKEN}", methods=["POST"])
    def webhook():
        data   = flask_request.get_json(force=True)
        update = Update.de_json(data, _tg_app.bot)
        fut    = asyncio.run_coroutine_threadsafe(_tg_app.process_update(update), _loop)
        fut.result(timeout=60)
        return "ok", 200

    @flask_app.route("/", methods=["GET"])
    def health():
        return "ICAGO Bot is running 🚀", 200

    @flask_app.route("/set_webhook", methods=["GET"])
    def set_webhook():
        import urllib.request
        if not RENDER_URL:
            return "❌ RENDER_URL chưa set", 400
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={RENDER_URL}/{BOT_TOKEN}"
        with urllib.request.urlopen(url) as r:
            result = json.loads(r.read())
        log.info(f"setWebhook: {result}")
        return f"✅ Webhook: {result}", 200

except ImportError:
    flask_app = None

# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    run_bot()
