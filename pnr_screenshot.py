#!/usr/bin/env python3
"""
pnr_screenshot.py — PNR Expert Auto Screenshot
Chụp đúng khung kết quả (dark preview panel) — full height, không bị cắt.
"""

import asyncio
import os
import sys
import tempfile

try:
    from PIL import Image
except ImportError:
    raise SystemExit("❌ pip install pillow")

# ── Config ─────────────────────────────────────────────────────────────────────
PNR_URL           = "https://www.pnrexpert.com/"
VIEWPORT_W        = 1280
VIEWPORT_H        = 900
BROWSERLESS_TOKEN = os.environ.get("BROWSERLESS_TOKEN", "").strip()

BL_WSS_ENDPOINTS = [
    "wss://production-sfo.browserless.io?token={token}&launch=%7B%22stealth%22%3Atrue%7D",
    "wss://production-sfo.browserless.io?token={token}",
    "wss://production-lon.browserless.io?token={token}",
    "wss://chrome.browserless.io?token={token}",
]

# ── JS: ẩn Co2 ─────────────────────────────────────────────────────────────────
JS_HIDE_CO2 = r"""
() => {
    let count = 0;
    document.querySelectorAll('*').forEach(el => {
        const txt = el.innerText || el.textContent || '';
        if (/tonnes?\s+of\s+co2/i.test(txt) && el.children.length === 0) {
            let cur = el;
            for (let i = 0; i < 6; i++) {
                if (!cur || cur === document.body) break;
                const t = cur.innerText || '';
                if (/tonnes?\s+of\s+co2/i.test(t) &&
                    !/depart|arriv|flight|outbound|return/i.test(t)) {
                    cur.style.cssText += 'display:none!important;';
                    count++;
                    break;
                }
                cur = cur.parentElement;
            }
        }
    });
    const s = document.createElement('style');
    s.textContent = '*{animation:none!important;transition:none!important;}';
    document.head && document.head.appendChild(s);
    return count;
}
"""

# ── JS: tìm ĐÚNG khung preview kết quả (dark panel) ──────────────────────────
JS_FIND_PREVIEW = r"""
() => {
    // pnrexpert.com: khung kết quả là div scrollable chứa nội dung chuyến bay
    // Thường có class chứa "preview", "output", "result", hoặc có background tối

    const flightKeywords = /departs?|arrives?|outbound|return.*city|flight itinerary/i;

    // Ưu tiên 1: tìm element scrollable (overflow) chứa nội dung flight
    const allEls = Array.from(document.querySelectorAll('div, section, article'));
    
    // Sắp xếp theo diện tích lớn nhất trước
    const candidates = allEls
        .map(el => {
            const r = el.getBoundingClientRect();
            const txt = el.innerText || '';
            const hasContent = flightKeywords.test(txt);
            const isScrollable = el.scrollHeight > el.clientHeight + 10;
            const isLarge = r.width > 300 && r.height > 100;
            return { el, r, txt, hasContent, isScrollable, isLarge,
                     score: (hasContent ? 10 : 0) + (isScrollable ? 5 : 0) + (isLarge ? 3 : 0) };
        })
        .filter(c => c.hasContent && c.isLarge)
        .sort((a, b) => b.score - a.score || b.r.width * b.r.height - a.r.width * a.r.height);

    if (candidates.length === 0) return null;

    const best = candidates[0];
    const el   = best.el;
    const r    = best.r;

    // Lấy scrollHeight thực sự (full height kể cả phần bị cắt)
    return {
        x:            Math.round(r.left),
        y:            Math.round(r.top),
        w:            Math.round(r.width),
        h_visible:    Math.round(r.height),        // chiều cao nhìn thấy
        h_full:       el.scrollHeight,             // chiều cao thực (full)
        scrollable:   el.scrollHeight > el.clientHeight + 10,
        tag:          el.tagName,
        className:    el.className.toString().slice(0, 80),
    };
}
"""

# ── JS: scroll element về top và expand để lấy full height ───────────────────
JS_EXPAND_AND_RESET = r"""
() => {
    // Tìm lại element và:
    // 1. Bỏ overflow hidden/scroll để nội dung hiện full
    // 2. Scroll về đầu
    const flightKeywords = /departs?|arrives?|outbound|return.*city|flight itinerary/i;
    const allEls = Array.from(document.querySelectorAll('div, section, article'));
    
    const candidates = allEls
        .filter(el => {
            const txt = el.innerText || '';
            const r = el.getBoundingClientRect();
            return flightKeywords.test(txt) && r.width > 300 && r.height > 100;
        })
        .sort((a, b) => {
            const ra = a.getBoundingClientRect();
            const rb = b.getBoundingClientRect();
            return (rb.width * rb.height) - (ra.width * ra.height);
        });

    if (candidates.length === 0) return 0;

    const el = candidates[0];
    // Bỏ giới hạn height và overflow để nội dung hiện đầy đủ
    el.style.cssText += `
        overflow: visible !important;
        max-height: none !important;
        height: auto !important;
    `;
    el.scrollTop = 0;
    
    // Làm tương tự các con trực tiếp
    Array.from(el.children).forEach(child => {
        child.style.cssText += 'overflow: visible !important; max-height: none !important;';
    });

    return el.scrollHeight;
}
"""


# ══════════════════════════════════════════════════════════════════════════════
# Core flow
# ══════════════════════════════════════════════════════════════════════════════

async def _run_flow(page, pnr_text: str, output_path: str, verbose: bool) -> bool:
    """
    Paste PNR → Quick Convert → chờ kết quả → ẩn Co2
    → chụp ĐÚNG khung kết quả full height.
    """
    try:
        # 1. Mở trang
        if verbose: print("  [flow] mở pnrexpert.com ...")
        await page.goto(PNR_URL, timeout=30_000, wait_until="domcontentloaded")
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass

        # 2. Paste PNR
        if verbose: print("  [flow] paste PNR...")
        ta = await page.wait_for_selector("textarea", timeout=10_000)
        await ta.click()
        await ta.fill(pnr_text.strip())
        await asyncio.sleep(0.5)

        # 3. Quick Convert
        if verbose: print("  [flow] click Quick Convert...")
        btn = await page.wait_for_selector(
            "button:has-text('Quick Convert')", timeout=10_000
        )
        await btn.click()

        # 4. Chờ nội dung flight xuất hiện
        if verbose: print("  [flow] chờ kết quả...")
        try:
            await page.wait_for_function(
                r"() => /departs?:/i.test(document.body.innerText)",
                timeout=25_000,
            )
        except Exception:
            if verbose: print("  [flow] ⚠️ timeout — tiếp tục")
        await asyncio.sleep(2.5)  # buffer để render xong

        # 5. Ẩn Co2
        n = await page.evaluate(JS_HIDE_CO2)
        if verbose: print(f"  [flow] ẩn {n} phần tử Co2")

        # 6. Expand overflow + scroll top để lấy full height
        full_h = await page.evaluate(JS_EXPAND_AND_RESET)
        if verbose: print(f"  [flow] scrollHeight sau expand = {full_h}px")
        await asyncio.sleep(0.5)

        # 7. Tìm bounding box khung kết quả
        info = await page.evaluate(JS_FIND_PREVIEW)
        if verbose: print(f"  [flow] preview info = {info}")

        if info and info.get("w", 0) > 200:
            x        = max(0, info["x"] - 4)
            y        = max(0, info["y"] - 4)
            width    = min(VIEWPORT_W - x, info["w"] + 8)
            # Dùng scrollHeight (chiều cao thực) thay vì clientHeight (bị cắt)
            height   = info["h_full"] + 8

            # Phóng viewport cao hơn để chứa đủ nội dung
            needed_h = int(y + height + 50)
            if needed_h > VIEWPORT_H:
                await page.set_viewport_size({"width": VIEWPORT_W, "height": needed_h})
                await asyncio.sleep(0.3)
                # Lấy lại bounding box sau khi resize
                info2 = await page.evaluate(JS_FIND_PREVIEW)
                if info2:
                    x     = max(0, info2["x"] - 4)
                    y     = max(0, info2["y"] - 4)
                    width = min(VIEWPORT_W - x, info2["w"] + 8)

            clip = {"x": x, "y": y, "width": width, "height": height}
            if verbose: print(f"  [flow] clip = {clip}")
            await page.screenshot(path=output_path, clip=clip, full_page=False)

        else:
            # Fallback: chụp full page
            if verbose: print("  [flow] fallback: full_page screenshot")
            await page.screenshot(path=output_path, full_page=True)

        size = os.path.getsize(output_path) if os.path.exists(output_path) else 0
        if verbose: print(f"  [flow] ✅ saved {output_path} ({size//1024} KB)")
        return size > 2000

    except Exception as e:
        if verbose: print(f"  [flow] ❌ {type(e).__name__}: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# Mode 1: Browserless.io
# ══════════════════════════════════════════════════════════════════════════════

async def _via_browserless(pnr_text: str, output_path: str, verbose: bool) -> bool:
    if not BROWSERLESS_TOKEN:
        return False
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return False

    for tmpl in BL_WSS_ENDPOINTS:
        ws = tmpl.format(token=BROWSERLESS_TOKEN)
        if verbose: print(f"  [browserless] thử: {ws[:60]}...")
        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.connect_over_cdp(ws, timeout=25_000)
                ctx = await browser.new_context(
                    viewport={"width": VIEWPORT_W, "height": VIEWPORT_H},
                    device_scale_factor=2,
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                )
                page = await ctx.new_page()
                ok   = await _run_flow(page, pnr_text, output_path, verbose)
                await browser.close()
                if ok:
                    if verbose: print(f"  [browserless] ✅ OK")
                    return True
        except Exception as e:
            if verbose: print(f"  [browserless] ❌ {ws[:45]}: {e}")
            continue

    return False


# ══════════════════════════════════════════════════════════════════════════════
# Mode 2: Playwright local
# ══════════════════════════════════════════════════════════════════════════════

async def _via_playwright_local(pnr_text: str, output_path: str, verbose: bool) -> bool:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return False
    if verbose: print("  [mode] Playwright local...")
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--no-sandbox","--disable-setuid-sandbox",
                      "--disable-dev-shm-usage","--disable-gpu"],
            )
            ctx  = await browser.new_context(
                viewport={"width": VIEWPORT_W, "height": VIEWPORT_H},
                device_scale_factor=2,
            )
            page = await ctx.new_page()
            ok   = await _run_flow(page, pnr_text, output_path, verbose)
            await browser.close()
            return ok
    except Exception as e:
        if verbose: print(f"  [local] ❌ {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# Post-process PIL: crop Co2 còn sót + whitespace thừa ở đáy
# ══════════════════════════════════════════════════════════════════════════════

def _postprocess(img_path: str, verbose: bool = False):
    """Tự động crop vùng trắng thừa và dòng Co2 còn sót ở đáy."""
    try:
        img = Image.open(img_path).convert("RGB")
        w, h = img.size

        # Tìm y cuối cùng có nội dung thực (pixel không trắng/xám quá nhạt)
        last_y = h
        for y in range(h - 1, max(h - 100, 0), -1):
            row = [img.getpixel((x, y)) for x in range(0, w, 6)]
            non_bg = sum(
                1 for r, g, b in row
                if not (r > 235 and g > 235 and b > 235)   # không trắng
                and not (r < 20  and g < 20  and b < 20)    # không đen thuần
            )
            if non_bg / len(row) > 0.04:
                last_y = y + 4
                break

        if last_y < h - 2:
            img.crop((0, 0, w, last_y)).save(img_path, "PNG")
            if verbose: print(f"  [PIL] crop bottom: {h}→{last_y}px")

    except Exception as e:
        if verbose: print(f"  [PIL] skip: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════════

async def _async_main(pnr_text: str, output_path: str, verbose: bool) -> str:
    ok = False
    if BROWSERLESS_TOKEN:
        ok = await _via_browserless(pnr_text, output_path, verbose)
    if not ok:
        ok = await _via_playwright_local(pnr_text, output_path, verbose)
    if not ok:
        raise RuntimeError(
            "Không thể chụp ảnh.\n"
            "• Local : python -m playwright install chromium\n"
            "• Render: kiểm tra BROWSERLESS_TOKEN"
        )
    _postprocess(output_path, verbose)
    return output_path


def pnr_to_image(pnr_text: str, output_path: str = None, verbose: bool = False) -> str:
    if output_path is None:
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        output_path = tmp.name
        tmp.close()
    asyncio.run(_async_main(pnr_text, output_path, verbose))
    return output_path


# ── CLI ────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse, sys as _sys
    p = argparse.ArgumentParser(description="PNR → pnrexpert.com → PNG (full content)")
    p.add_argument("pnr", nargs="?")
    p.add_argument("-o", "--output", default="pnr_result.png")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()
    pnr = args.pnr or _sys.stdin.read()
    print(f"🔄 Xử lý PNR (browserless={'có' if BROWSERLESS_TOKEN else 'không'})...")
    out = pnr_to_image(pnr.strip(), args.output, args.verbose)
    print(f"✅ Xong: {out}  ({os.path.getsize(out)//1024} KB)")
