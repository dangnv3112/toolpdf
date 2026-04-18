#!/usr/bin/env python3
"""
pnr_screenshot.py — PNR Expert Auto Screenshot
Chụp đúng phần nội dung (Outbound→Return→Ticket) từ pnrexpert.com.
"""

import asyncio
import os
import tempfile

try:
    from PIL import Image
except ImportError:
    raise SystemExit("❌ pip install pillow")

# ── Config ─────────────────────────────────────────────────────────────────────
PNR_URL           = "https://www.pnrexpert.com/"
VIEWPORT_W        = 1280
VIEWPORT_H        = 3000
BROWSERLESS_TOKEN = os.environ.get("BROWSERLESS_TOKEN", "").strip()

BL_ENDPOINTS = [
    "wss://production-sfo.browserless.io?token={t}",
    "wss://production-sfo.browserless.io?token={t}&launch=%7B%22stealth%22%3Atrue%7D",
    "wss://production-lon.browserless.io?token={t}",
    "wss://chrome.browserless.io?token={t}",
]

# ── JS: tắt animation ──────────────────────────────────────────────────────────
JS_NO_ANIM = r"""() => {
    const s = document.createElement('style');
    s.textContent = '*{animation:none!important;transition:none!important;}';
    document.head.appendChild(s);
}"""

# ── JS: ẩn Co2 + các dòng rác TRƯỚC khi tìm bounding box ─────────────────────
# Ẩn trước để không tính vào chiều cao clip
JS_HIDE_JUNK = r"""() => {
    let n = 0;
    document.querySelectorAll('*').forEach(el => {
        if (el.children.length > 0) return;
        const txt = el.textContent || '';
        // Ẩn dòng Co2
        if (/tonnes?\s+of\s+co2/i.test(txt)) {
            let cur = el;
            for (let i = 0; i < 6 && cur && cur !== document.body; i++) {
                const t = cur.innerText || '';
                if (/tonnes?\s+of\s+co2/i.test(t) &&
                    !/depart|arriv|flight|outbound|return/i.test(t)) {
                    cur.style.display = 'none'; n++; break;
                }
                cur = cur.parentElement;
            }
        }
    });
    return n;
}"""

# ── JS chính: tìm clip box chính xác ──────────────────────────────────────────
# Thuật toán:
#   1. Tìm text node "Outbound" (header đầu tiên của bảng kết quả)
#   2. Tìm text node cuối cùng trong bảng (FLIGHT TICKET hoặc dòng cuối Return)
#   3. Tính bounding box bao trùm từ đầu đến cuối
#   4. Fallback: lấy element cha chứa cả Outbound và Return
JS_FIND_RESULT = r"""() => {
    // Scroll về top trước
    window.scrollTo(0, 0);

    // ── Tìm element chứa "Outbound" (header section đầu tiên) ─────────────────
    function findTextEl(regex) {
        const walker = document.createTreeWalker(
            document.body, NodeFilter.SHOW_TEXT, null, false
        );
        let node;
        while ((node = walker.nextNode())) {
            if (regex.test(node.textContent || '')) {
                return node.parentElement;
            }
        }
        return null;
    }

    const outboundEl = findTextEl(/outbound\s*:/i) || findTextEl(/outbound/i);
    const returnEl   = findTextEl(/return\s*:/i)   || findTextEl(/\breturn\b/i);

    if (!outboundEl) return null;

    // ── Tìm container cha nhỏ nhất bao cả Outbound + Return ───────────────────
    function getContainer(elA, elB) {
        if (!elB) return elA;
        // Leo lên từ elA cho đến khi container đó chứa cả elB
        let cur = elA;
        for (let i = 0; i < 15 && cur && cur !== document.body; i++) {
            if (cur.contains(elB)) return cur;
            cur = cur.parentElement;
        }
        return elA;
    }

    const container = getContainer(outboundEl, returnEl);
    if (!container) return null;

    const r = container.getBoundingClientRect();

    // Kiểm tra hợp lệ
    if (r.width < 200 || r.height < 100) return null;

    // Bỏ overflow để render full nội dung
    let cur = container;
    for (let i = 0; i < 10 && cur && cur !== document.body; i++) {
        cur.style.overflow  = 'visible';
        cur.style.maxHeight = 'none';
        cur.style.height    = 'auto';
        cur = cur.parentElement;
    }

    // Lấy lại r sau khi bỏ overflow
    const r2 = container.getBoundingClientRect();

    return {
        strategy: 'outbound-return-container',
        x: Math.round(r2.left),
        y: Math.round(r2.top),
        w: Math.round(r2.width),
        h: Math.round(r2.height),
        scrollH: container.scrollHeight,
        elTag: container.tagName,
    };
}"""


# ══════════════════════════════════════════════════════════════════════════════
# Core automation flow
# ══════════════════════════════════════════════════════════════════════════════

async def _run_flow(page, pnr_text: str, output_path: str, verbose: bool) -> bool:
    try:
        # 1. Mở trang
        if verbose: print("  [1] goto pnrexpert.com...")
        await page.goto(PNR_URL, timeout=25_000, wait_until="domcontentloaded")
        await page.wait_for_selector("textarea", timeout=10_000)

        # 2. Paste PNR
        if verbose: print("  [2] paste PNR...")
        ta = page.locator("textarea").first
        await ta.click()
        await ta.fill(pnr_text.strip())

        # 3. Click Quick Convert
        if verbose: print("  [3] Quick Convert...")
        await page.locator("button:has-text('Quick Convert')").click()

        # 4. Chờ kết quả render
        if verbose: print("  [4] chờ kết quả...")
        try:
            await page.wait_for_function(
                r"() => /outbound/i.test(document.body.innerText) || "
                r"      /departs/i.test(document.body.innerText)",
                timeout=20_000, polling=500,
            )
            if verbose: print("  [4] ✅ kết quả đã hiện")
        except Exception:
            if verbose: print("  [4] ⚠️ timeout — thử tiếp")
        await asyncio.sleep(1)

        # 5. Tắt animation
        await page.evaluate(JS_NO_ANIM)

        # 6. ẨN CO2 + JUNK TRƯỚC — để không tính vào bounding box
        n = await page.evaluate(JS_HIDE_JUNK)
        if verbose: print(f"  [6] ẩn {n} junk element")
        await asyncio.sleep(0.2)  # chờ layout reflow sau khi ẩn

        # 7. Scroll về top
        await page.evaluate("() => window.scrollTo(0, 0)")
        await asyncio.sleep(0.2)

        # 8. Tìm clip box
        info = await page.evaluate(JS_FIND_RESULT)
        if verbose: print(f"  [8] result box: {info}")

        if not info:
            if verbose: print("  [8] ⚠️ không tìm thấy — chụp full page")
            await page.screenshot(path=output_path, full_page=True)
            return os.path.getsize(output_path) > 5000

        # 9. Tính clip — padding nhỏ và đều
        PAD = 12
        x = max(0, info["x"] - PAD)
        y = max(0, info["y"] - PAD)
        w = min(VIEWPORT_W - x, info["w"] + PAD * 2)
        h = max(info["scrollH"], info["h"]) + PAD * 2

        clip = {"x": x, "y": y, "width": w, "height": h}
        if verbose: print(f"  [9] clip: {clip}")

        await page.screenshot(path=output_path, clip=clip, full_page=False)

        size = os.path.getsize(output_path) if os.path.exists(output_path) else 0
        if verbose: print(f"  [9] saved {size//1024} KB")
        return size > 3000

    except Exception as e:
        if verbose: print(f"  [flow] ❌ {type(e).__name__}: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# Browser launchers
# ══════════════════════════════════════════════════════════════════════════════

def _ctx_opts():
    return dict(
        viewport={"width": VIEWPORT_W, "height": VIEWPORT_H},
        device_scale_factor=2,
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    )


async def _via_browserless(pnr_text, output_path, verbose):
    if not BROWSERLESS_TOKEN:
        return False
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return False

    for tmpl in BL_ENDPOINTS:
        ws = tmpl.format(t=BROWSERLESS_TOKEN)
        if verbose: print(f"  [BL] {ws[:55]}...")
        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.connect_over_cdp(ws, timeout=20_000)
                ctx  = await browser.new_context(**_ctx_opts())
                page = await ctx.new_page()
                ok   = await _run_flow(page, pnr_text, output_path, verbose)
                await browser.close()
                if ok:
                    return True
        except Exception as e:
            if verbose: print(f"  [BL] ❌ {e}")
    return False


async def _via_local(pnr_text, output_path, verbose):
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return False
    if verbose: print("  [local] Playwright chromium...")
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--no-sandbox","--disable-setuid-sandbox",
                      "--disable-dev-shm-usage","--disable-gpu"],
            )
            ctx  = await browser.new_context(**_ctx_opts())
            page = await ctx.new_page()
            ok   = await _run_flow(page, pnr_text, output_path, verbose)
            await browser.close()
            return ok
    except Exception as e:
        if verbose: print(f"  [local] ❌ {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# Post-process: crop whitespace thừa ở đáy
# ══════════════════════════════════════════════════════════════════════════════

def _trim_bottom(img_path: str, verbose=False):
    try:
        img = Image.open(img_path).convert("RGB")
        w, h = img.size
        for y in range(h - 1, max(h - 120, 0), -1):
            row = [img.getpixel((x, y)) for x in range(0, w, 8)]
            active = sum(
                1 for r,g,b in row
                if not (r>240 and g>240 and b>240)
                and not (r<15  and g<15  and b<15)
            )
            if active / len(row) > 0.03:
                new_h = min(y + 20, h)
                if new_h < h - 5:
                    img.crop((0, 0, w, new_h)).save(img_path, "PNG")
                    if verbose: print(f"  [trim] {h}→{new_h}px")
                return
    except Exception as e:
        if verbose: print(f"  [trim] skip: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════════

async def _main(pnr_text, output_path, verbose):
    if BROWSERLESS_TOKEN:
        ok = await _via_browserless(pnr_text, output_path, verbose)
        if ok and os.path.isfile(output_path) and os.path.getsize(output_path) > 3000:
            _trim_bottom(output_path, verbose)
            return output_path
        if verbose: print("  Browserless fail → local...")

    ok = await _via_local(pnr_text, output_path, verbose)
    if ok and os.path.isfile(output_path) and os.path.getsize(output_path) > 3000:
        _trim_bottom(output_path, verbose)
        return output_path

    raise RuntimeError(
        "Không thể chụp ảnh PNR.\n"
        "• Render: kiểm tra BROWSERLESS_TOKEN\n"
        "• Local : python -m playwright install chromium"
    )


def pnr_to_image(pnr_text: str, output_path: str = None, verbose: bool = False) -> str:
    if not output_path:
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        output_path = tmp.name; tmp.close()
    asyncio.run(_main(pnr_text, output_path, verbose))
    return output_path


# ── CLI ────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse, sys
    p = argparse.ArgumentParser()
    p.add_argument("pnr", nargs="?")
    p.add_argument("-o", "--output", default="pnr_result.png")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()
    txt = args.pnr or sys.stdin.read()
    print(f"🔄 Xử lý... (Browserless: {'✅' if BROWSERLESS_TOKEN else '❌ local'})")
    out = pnr_to_image(txt.strip(), args.output, args.verbose)
    print(f"✅ {out}  ({os.path.getsize(out)//1024} KB)")
