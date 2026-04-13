#!/usr/bin/env python3
"""
pnr_screenshot.py — PNR Expert Auto Screenshot
================================================
Nhận mã PNR → dán vào pnrexpert.com → Quick Convert → chụp ảnh kết quả
(tự động ẩn dòng "Tonnes of Co2")

Hỗ trợ 3 mode (tự động chọn theo môi trường):
  1. Browserless.io  — dùng khi có BROWSERLESS_TOKEN (Render deploy)
  2. Playwright local — dùng khi đã cài chromium (chạy local)
  3. Selenium local  — fallback nếu có Chrome/chromedriver

Cài đặt local:
  pip install playwright pillow requests
  python -m playwright install chromium

Deploy Render (không cần cài Chrome):
  - Đăng ký https://browserless.io (free: 1000 sessions/month)
  - Set env: BROWSERLESS_TOKEN=your_token
  - KHÔNG cần cài playwright trên Render

Dùng từ code khác:
  from pnr_screenshot import pnr_to_image
  img_path = pnr_to_image("1.SMITH/JOHN ...", output_path="result.png")
"""

import asyncio
import base64
import json
import os
import re
import tempfile
from pathlib import Path

try:
    from PIL import Image, ImageChops
    import io as _io
except ImportError:
    raise SystemExit("❌ pip install pillow")

# ── Config ─────────────────────────────────────────────────────────────────────
PNR_URL            = "https://www.pnrexpert.com/"
VIEWPORT_W         = 1200
VIEWPORT_H         = 900
BROWSERLESS_TOKEN  = os.environ.get("BROWSERLESS_TOKEN", "")
BROWSERLESS_WS     = "wss://chrome.browserless.io?token={token}&--window-size={w},{h}"

# JS: ẩn dòng Co2 + animation, trả về số element đã ẩn
JS_HIDE_CO2 = r"""
() => {
    let count = 0;
    document.querySelectorAll('*').forEach(el => {
        const txt = (el.innerText || el.textContent || '');
        if (/tonnes?\s+of\s+co2/i.test(txt) && el.children.length === 0) {
            let cur = el;
            while (cur && cur !== document.body) {
                const t = (cur.innerText || '');
                if (/tonnes?\s+of\s+co2/i.test(t) && !/depart|arriv|flight/i.test(t)) {
                    cur.style.cssText += 'display:none!important;visibility:hidden!important;';
                    count++;
                    break;
                }
                cur = cur.parentElement;
            }
        }
    });
    // Tắt animation để chụp ảnh sắc nét
    const style = document.createElement('style');
    style.textContent = '* { animation: none !important; transition: none !important; }';
    document.head.appendChild(style);
    return count;
}
"""

# JS: tìm bounding box vùng kết quả
JS_GET_BBOX = r"""
() => {
    const candidates = [
        '.itinerary-preview', '.itinerary-output', '.result-preview',
        '[class*="preview"]', '[class*="itinerary"]', '[class*="result-area"]',
        '.card', '[class*="output"]',
    ];
    for (const sel of candidates) {
        for (const el of document.querySelectorAll(sel)) {
            const r = el.getBoundingClientRect();
            if (r.width > 400 && r.height > 150) {
                return { x: Math.round(r.x), y: Math.round(r.y),
                         w: Math.round(r.width), h: Math.round(r.height), sel };
            }
        }
    }
    // Fallback: tìm element chứa "Departs"
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
    while (walker.nextNode()) {
        const el = walker.currentNode;
        if (/departs?:/i.test(el.innerText || '') && el.children.length > 2) {
            const r = el.getBoundingClientRect();
            if (r.width > 300 && r.height > 100) {
                return { x: Math.round(r.x), y: Math.round(r.y),
                         w: Math.round(r.width), h: Math.round(r.height), sel: 'departs-fallback' };
            }
        }
    }
    return null;
}
"""


# ══════════════════════════════════════════════════════════════════════════════
# Mode 1: Browserless.io (Render deploy — không cần cài Chrome)
# ══════════════════════════════════════════════════════════════════════════════

async def _via_browserless(pnr_text: str, output_path: str, verbose: bool) -> bool:
    """
    Dùng Playwright kết nối tới Browserless.io qua WebSocket.
    Không cần Chrome cài local.
    Trả về True nếu thành công.
    """
    try:
        from playwright.async_api import async_playwright, TimeoutError as PWTimeout
    except ImportError:
        if verbose: print("  [browserless] playwright không có — bỏ qua mode này")
        return False

    if not BROWSERLESS_TOKEN:
        return False

    ws_url = BROWSERLESS_WS.format(
        token=BROWSERLESS_TOKEN, w=VIEWPORT_W, h=VIEWPORT_H
    )
    if verbose: print(f"  [browserless] kết nối tới browserless.io...")

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.connect_over_cdp(ws_url)
            context = await browser.new_context(
                viewport={"width": VIEWPORT_W, "height": VIEWPORT_H},
                device_scale_factor=2,
            )
            page = await context.new_page()
            result = await _run_pnr_flow(page, pnr_text, output_path, verbose)
            await browser.close()
            return result
    except Exception as e:
        if verbose: print(f"  [browserless] lỗi: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# Mode 2: Playwright local (chạy local sau khi cài chromium)
# ══════════════════════════════════════════════════════════════════════════════

async def _via_playwright_local(pnr_text: str, output_path: str, verbose: bool) -> bool:
    try:
        from playwright.async_api import async_playwright, TimeoutError as PWTimeout
    except ImportError:
        if verbose: print("  [playwright] không cài — bỏ qua")
        return False

    if verbose: print("  [playwright] khởi động Chrome local...")
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox",
                      "--disable-dev-shm-usage", "--disable-gpu"],
            )
            context = await browser.new_context(
                viewport={"width": VIEWPORT_W, "height": VIEWPORT_H},
                device_scale_factor=2,
            )
            page = await context.new_page()
            result = await _run_pnr_flow(page, pnr_text, output_path, verbose)
            await browser.close()
            return result
    except Exception as e:
        if verbose: print(f"  [playwright] lỗi: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# Flow chung: paste PNR → Quick Convert → screenshot
# ══════════════════════════════════════════════════════════════════════════════

async def _run_pnr_flow(page, pnr_text: str, output_path: str, verbose: bool) -> bool:
    try:
        from playwright.async_api import TimeoutError as PWTimeout
    except ImportError:
        return False

    PAD = 24  # pixel padding quanh vùng kết quả

    try:
        # 1. Mở trang
        if verbose: print("  [flow] mở pnrexpert.com...")
        await page.goto(PNR_URL, timeout=30_000, wait_until="domcontentloaded")
        await page.wait_for_load_state("networkidle", timeout=20_000)

        # 2. Paste PNR
        if verbose: print("  [flow] paste PNR...")
        ta = await page.wait_for_selector("textarea", timeout=10_000)
        await ta.click()
        await ta.fill(pnr_text.strip())
        await asyncio.sleep(0.4)

        # 3. Click Quick Convert
        if verbose: print("  [flow] Quick Convert...")
        btn = await page.wait_for_selector(
            "button:has-text('Quick Convert')", timeout=10_000
        )
        await btn.click()

        # 4. Chờ kết quả
        if verbose: print("  [flow] chờ kết quả...")
        try:
            await page.wait_for_function(
                "() => /departs?:/i.test(document.body.innerText)",
                timeout=20_000,
            )
        except PWTimeout:
            if verbose: print("  [flow] ⚠️ timeout — tiếp tục")
        await asyncio.sleep(1.5)

        # 5. Ẩn Co2
        n = await page.evaluate(JS_HIDE_CO2)
        if verbose: print(f"  [flow] ẩn {n} phần tử Co2")
        await asyncio.sleep(0.3)

        # 6. Chụp ảnh
        bbox = await page.evaluate(JS_GET_BBOX)
        if verbose: print(f"  [flow] bbox: {bbox}")

        if bbox:
            clip = {
                "x":      max(0, bbox["x"] - PAD),
                "y":      max(0, bbox["y"] - PAD),
                "width":  min(VIEWPORT_W, bbox["w"] + PAD * 2),
                "height": bbox["h"] + PAD * 2,
            }
            await page.screenshot(path=output_path, clip=clip, full_page=False)
        else:
            await page.screenshot(path=output_path, full_page=True)

        if verbose: print(f"  [flow] ✅ screenshot: {output_path}")
        return True

    except Exception as e:
        if verbose: print(f"  [flow] lỗi: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# Post-process: PIL crop dòng Co2 còn sót
# ══════════════════════════════════════════════════════════════════════════════

def _crop_co2(img_path: str, verbose: bool = False):
    """
    Crop bỏ dòng Co2 còn sót ở cuối ảnh bằng cách phân tích pixel.
    Tìm dòng cuối cùng có màu sắc (text) và cắt ngay sau đó.
    """
    try:
        img = Image.open(img_path).convert("RGB")
        w, h = img.size

        # Scan từ dưới lên để tìm text Co2
        # Co2 line thường là text nhỏ màu xanh lá / xám ở cuối
        crop_at = h
        for y in range(h - 10, max(h - 80, 0), -1):
            row_pixels = [img.getpixel((x, y)) for x in range(0, w, 5)]
            # Pixel màu xanh lá (Co2 icon) hoặc xám nhạt
            colored = sum(
                1 for r, g, b in row_pixels
                if not (r > 220 and g > 220 and b > 220)  # không phải trắng
                and not (r < 30 and g < 30 and b < 30)    # không phải đen
            )
            ratio = colored / len(row_pixels)
            if ratio > 0.05:
                crop_at = y + 5
                break

        if crop_at < h - 5:
            img.crop((0, 0, w, crop_at)).save(img_path, "PNG")
            if verbose: print(f"  [crop] cắt tại y={crop_at} (bỏ {h-crop_at}px cuối)")
    except Exception as e:
        if verbose: print(f"  [crop] skip: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════════

async def _async_pnr_to_image(pnr_text: str, output_path: str, verbose: bool) -> str:
    """Thử lần lượt các mode cho đến khi thành công."""
    # Mode 1: Browserless.io (ưu tiên khi deploy Render)
    if BROWSERLESS_TOKEN:
        if verbose: print("  [mode] Browserless.io")
        ok = await _via_browserless(pnr_text, output_path, verbose)
        if ok and os.path.isfile(output_path):
            _crop_co2(output_path, verbose)
            return output_path

    # Mode 2: Playwright local
    if verbose: print("  [mode] Playwright local")
    ok = await _via_playwright_local(pnr_text, output_path, verbose)
    if ok and os.path.isfile(output_path):
        _crop_co2(output_path, verbose)
        return output_path

    raise RuntimeError(
        "Không thể chạy browser.\n"
        "Local: python -m playwright install chromium\n"
        "Render: set BROWSERLESS_TOKEN (đăng ký tại browserless.io)"
    )


def pnr_to_image(pnr_text: str, output_path: str = None, verbose: bool = False) -> str:
    """
    Public API — gọi từ bot.py hoặc CLI.

    Args:
        pnr_text:    Nội dung PNR raw từ GDS
        output_path: File ảnh output (.png). Mặc định: tạo temp file.
        verbose:     In log chi tiết

    Returns:
        Đường dẫn file ảnh đã tạo
    """
    if output_path is None:
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        output_path = tmp.name
        tmp.close()

    asyncio.run(_async_pnr_to_image(pnr_text, output_path, verbose))
    return output_path


# ── CLI ────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse, sys

    p = argparse.ArgumentParser(
        description="PNR → pnrexpert.com → screenshot (bỏ Co2)"
    )
    p.add_argument("pnr", nargs="?", help="PNR text (hoặc đọc từ stdin)")
    p.add_argument("-o", "--output", default="pnr_result.png")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    pnr_text = args.pnr or sys.stdin.read()
    print("🔄 Đang xử lý PNR...")
    out = pnr_to_image(pnr_text.strip(), args.output, args.verbose)
    print(f"✅ Kết quả: {out}  ({os.path.getsize(out)//1024} KB)")
