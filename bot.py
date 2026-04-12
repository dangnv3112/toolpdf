#!/usr/bin/env python3
"""
bot.py — ICAGO Telegram Bot
=============================
Flow 1 (PDF):
  User gửi file PDF lịch trình
  → Bot xử lý bằng icago_itinerary.py
  → Bot trả về file PDF đã format chuẩn ICAGO

Flow 2 (PNR Code) — MỚI:
  User gửi mã PNR (text bắt đầu bằng dòng có tên hành khách hoặc số hiệu chuyến bay)
  → Bot mở https://www.pnrexpert.com/
  → Dán code, nhấn Quick Convert
  → Chụp ảnh kết quả (bỏ dòng Tonnes of CO2)
  → Gửi ảnh PNG vào Telegram

Deploy:
  Local  : python bot.py
  Render : gunicorn bot:flask_app  (xem README_BOT.md)

Cài đặt:
  pip install python-telegram-bot reportlab pillow pdfplumber playwright
  playwright install chromium
"""

import os
import re
import logging
import tempfile
import asyncio
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

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8659136625:AAFcL4VweOqk5j6Sksu_HQCk3adz0bLH5gY")
LOGO_PATH = os.environ.get("LOGO_PATH", DEFAULT_LOGO)
LUUY_PATH = os.environ.get("LUUY_PATH", DEFAULT_LUUY)
MAX_MB    = int(os.environ.get("MAX_MB", "20"))

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
    """
    Phát hiện mã PNR GDS/Amadeus.
    Điều kiện: text có ít nhất 2 dòng VÀ chứa pattern GDS
    (số hiệu chuyến bay như VN123, tên hành khách SMITH/JOHN,
    hoặc các keyword đặc trưng PNR).
    """
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    if len(lines) < 2:
        return False

    pnr_patterns = [
        r'\b[A-Z]{2}\d{2,4}\b',          # Số hiệu chuyến bay: VN123, QH204
        r'\b\d+\.[A-Z]+/[A-Z]+\b',       # Tên hành khách: 1.NGUYEN/VAN
        r'\bHK\d+\b',                     # HK2 (số ghế)
        r'\bRM\b|\bOSI\b|\bSSR\b',        # Remarks GDS
        r'^\s*\d+\s+[A-Z]{2}\s+\d{3,4}', # dòng PNR chuẩn Amadeus
    ]
    combined = '\n'.join(lines[:10])
    matches = sum(1 for p in pnr_patterns if re.search(p, combined))
    return matches >= 2


# ══════════════════════════════════════════════════════════════════════════════
# PNR → pnrexpert.com → Screenshot
# ══════════════════════════════════════════════════════════════════════════════

async def pnr_to_screenshot(pnr_text: str, output_png: str) -> str:
    """
    Mở pnrexpert.com, dán PNR, nhấn Quick Convert,
    chụp ảnh kết quả (đã crop bỏ dòng CO2), lưu ra output_png.
    Trả về đường dẫn file PNG.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise RuntimeError(
            "❌ Chưa cài Playwright:\n"
            "   pip install playwright\n"
            "   playwright install chromium"
        )

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        page = await browser.new_page(viewport={"width": 1280, "height": 900})

        # ── 1. Mở trang ─────────────────────────────────────────────────────
        log.info("🌐 Mở pnrexpert.com...")
        await page.goto("https://www.pnrexpert.com/", timeout=30000)
        await page.wait_for_load_state("networkidle", timeout=20000)

        # ── 2. Dán PNR vào textarea ──────────────────────────────────────────
        log.info("📋 Dán mã PNR...")
        textarea = await page.wait_for_selector(
            "textarea, [placeholder*='PNR'], [placeholder*='pnr'], .pnr-input",
            timeout=10000,
        )
        await textarea.click()
        await textarea.fill(pnr_text)
        await asyncio.sleep(0.5)

        # ── 3. Nhấn Quick Convert ────────────────────────────────────────────
        log.info("🔄 Nhấn Quick Convert...")
        # Thử các selector phổ biến
        quick_btn = None
        for sel in [
            "button:has-text('Quick Convert')",
            "input[value*='Quick']",
            "[class*='quick']",
            "button:has-text('Quick')",
        ]:
            try:
                quick_btn = await page.wait_for_selector(sel, timeout=5000)
                if quick_btn:
                    break
            except Exception:
                continue

        if not quick_btn:
            raise RuntimeError("Không tìm thấy nút Quick Convert trên trang.")

        await quick_btn.click()

        # ── 4. Chờ kết quả render ────────────────────────────────────────────
        log.info("⏳ Chờ kết quả...")
        # Chờ phần preview/result xuất hiện
        result_sel = None
        for sel in [
            ".itinerary-preview",
            ".result-container",
            ".output-section",
            "[class*='preview']",
            "[class*='result']",
            "[class*='itinerary']",
        ]:
            try:
                await page.wait_for_selector(sel, timeout=15000)
                result_sel = sel
                break
            except Exception:
                continue

        # Dù không tìm thấy selector cụ thể, vẫn chờ thêm để JS render xong
        await asyncio.sleep(3)

        # ── 5. Chụp ảnh và bỏ dòng CO2 ──────────────────────────────────────
        log.info("📸 Chụp ảnh kết quả và xóa dòng CO2...")
        await _screenshot_without_co2(page, output_png)

        await browser.close()
        log.info(f"✅ Screenshot lưu tại: {output_png}")
        return output_png


async def _screenshot_without_co2(page, output_png: str):
    """
    Chụp toàn trang, sau đó dùng Pillow để xóa vùng chứa text CO2.
    Chiến lược:
      1. Ẩn element CO2 bằng JS (nếu tìm được)
      2. Chụp screenshot
      3. Dùng Pillow xóa thêm vùng ảnh chứa chữ "CO2" / "Tonnes"
    """
    from PIL import Image, ImageDraw
    import io

    # Ẩn các element chứa CO2 bằng JS
    await page.evaluate("""
        () => {
            const co2Texts = ['CO2', 'Tonnes', 'carbon', 'CARBON', 'tonne'];
            document.querySelectorAll('*').forEach(el => {
                if (el.children.length === 0) {  // chỉ text node
                    const txt = el.textContent || '';
                    if (co2Texts.some(t => txt.includes(t))) {
                        // Ẩn dòng chứa CO2
                        let row = el;
                        for (let i = 0; i < 4; i++) {
                            if (row && (row.tagName === 'TR' || row.tagName === 'DIV' || row.tagName === 'P')) {
                                row.style.display = 'none';
                                break;
                            }
                            row = row?.parentElement;
                        }
                    }
                }
            });
        }
    """)

    await asyncio.sleep(0.5)

    # Chụp toàn trang
    raw_png = await page.screenshot(full_page=True)

    # Dùng Pillow để scan và xóa thêm (nếu JS miss)
    img = Image.open(io.BytesIO(raw_png)).convert("RGB")
    _pillow_remove_co2_rows(img)

    img.save(output_png, "PNG", optimize=True)


def _pillow_remove_co2_rows(img):
    """
    Scan ảnh tìm các dòng pixel chứa màu gần giống text "Tonnes of CO2".
    Cách đơn giản: dùng pytesseract nếu có, hoặc bỏ qua (JS đã ẩn rồi).
    """
    try:
        import pytesseract
        import numpy as np

        arr = np.array(img)
        h, w = arr.shape[:2]
        draw = ImageDraw.Draw(img)

        # OCR từng vùng ngang ~20px
        strip_h = 20
        for y in range(0, h - strip_h, strip_h // 2):
            region = img.crop((0, y, w, y + strip_h))
            txt = pytesseract.image_to_string(region, config="--psm 7")
            if any(kw in txt for kw in ["CO2", "Tonnes", "tonne", "carbon", "CARBON"]):
                draw.rectangle([0, y, w, y + strip_h], fill=(255, 255, 255))

        del draw
    except Exception:
        # pytesseract không có sẵn → bỏ qua, JS đã lo
        pass


# ══════════════════════════════════════════════════════════════════════════════
# Handlers
# ══════════════════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Xin chào! Tôi là *ICAGO PDF Bot*.\n\n"
        "📎 *Gửi file PDF* lịch trình GDS/Amadeus → nhận PDF chuẩn ICAGO\n\n"
        "✈️ *Gửi mã PNR* (dán thẳng text code) → tôi sẽ:\n"
        "  1. Tự động mở pnrexpert.com\n"
        "  2. Nhấn Quick Convert\n"
        "  3. Gửi lại ảnh kết quả (đã ẩn dòng CO2)\n\n"
        "📌 Lệnh: /start /help",
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
    """Xử lý khi user gửi file PDF."""
    doc = update.message.document

    if not doc.file_name.lower().endswith(".pdf"):
        await update.message.reply_text("❌ Vui lòng gửi file *PDF*.", parse_mode="Markdown")
        return

    if doc.file_size > MAX_MB * 1024 * 1024:
        await update.message.reply_text(f"❌ File quá lớn (tối đa {MAX_MB} MB).")
        return

    status_msg = await update.message.reply_text("⏳ Đang xử lý PDF...")
    user = update.effective_user
    log.info(f"Nhận file từ {user.full_name} (@{user.username}): {doc.file_name}")

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
        log.info(f"✅ Gửi thành công: {output_name} ({size_kb} KB)")

    except Exception as e:
        log.exception("Lỗi khi xử lý PDF")
        await status_msg.edit_text(f"❌ Lỗi xử lý:\n`{e}`", parse_mode="Markdown")


async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    Xử lý tin nhắn text:
    - Nếu trông giống mã PNR → chạy pnrexpert.com flow
    - Ngược lại → hướng dẫn user
    """
    text = update.message.text or ""

    if looks_like_pnr(text):
        await handle_pnr_code(update, ctx, text)
    else:
        await update.message.reply_text(
            "📎 Hãy gửi *file PDF* lịch trình hoặc dán *mã PNR* để tôi xử lý.\n"
            "Gõ /help để xem hướng dẫn.",
            parse_mode="Markdown",
        )


async def handle_pnr_code(update: Update, ctx: ContextTypes.DEFAULT_TYPE, pnr_text: str):
    """Chạy Playwright → pnrexpert.com → chụp ảnh → gửi Telegram."""
    user = update.effective_user
    log.info(f"PNR từ {user.full_name}: {pnr_text[:60]}...")

    status_msg = await update.message.reply_text(
        "✈️ Đang xử lý mã PNR trên pnrexpert.com...\n"
        "_(Bước 1/3: Mở trình duyệt)_",
        parse_mode="Markdown",
    )

    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_png = os.path.join(tmp_dir, "pnr_result.png")

            await status_msg.edit_text(
                "✈️ Đang xử lý mã PNR...\n_(Bước 2/3: Convert trên web)_",
                parse_mode="Markdown",
            )

            await pnr_to_screenshot(pnr_text, output_png)

            await status_msg.edit_text(
                "✈️ Đang xử lý mã PNR...\n_(Bước 3/3: Gửi kết quả)_",
                parse_mode="Markdown",
            )

            size_kb = os.path.getsize(output_png) // 1024

            with open(output_png, "rb") as f:
                await update.message.reply_photo(
                    photo=f,
                    caption="✅ Kết quả từ pnrexpert.com (đã ẩn dòng CO2)",
                )

        await status_msg.delete()
        log.info(f"✅ Đã gửi ảnh PNR ({size_kb} KB)")

    except Exception as e:
        log.exception("Lỗi khi xử lý PNR")
        await status_msg.edit_text(
            f"❌ Lỗi xử lý PNR:\n`{e}`\n\n"
            "💡 Kiểm tra:\n"
            "• Đã cài Playwright chưa? `pip install playwright && playwright install chromium`\n"
            "• Mã PNR có đúng định dạng GDS không?",
            parse_mode="Markdown",
        )


# ══════════════════════════════════════════════════════════════════════════════
# Khởi động bot
# ══════════════════════════════════════════════════════════════════════════════

def run_bot():
    log.info("🤖 Khởi động ICAGO Bot...")
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help",  cmd_help))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    # TEXT handler — phát hiện PNR hoặc hướng dẫn
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    log.info("✅ Bot đang chạy. Nhấn Ctrl+C để dừng.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


# ── Entry point cho Render (webhook mode) ─────────────────────────────────────
RENDER_URL = os.environ.get("RENDER_URL", "")

if RENDER_URL:
    try:
        from flask import Flask, request as flask_request

        flask_app = Flask(__name__)
        _tg_app   = Application.builder().token(BOT_TOKEN).build()

        _tg_app.add_handler(CommandHandler("start", cmd_start))
        _tg_app.add_handler(CommandHandler("help",  cmd_help))
        _tg_app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
        _tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

        @flask_app.route(f"/{BOT_TOKEN}", methods=["POST"])
        def webhook():
            import json
            data = flask_request.get_json(force=True)
            update = Update.de_json(data, _tg_app.bot)
            asyncio.run(_tg_app.process_update(update))
            return "ok", 200

        @flask_app.route("/", methods=["GET"])
        def health():
            return "ICAGO Bot is running 🚀", 200

    except ImportError:
        pass


if __name__ == "__main__":
    run_bot()
