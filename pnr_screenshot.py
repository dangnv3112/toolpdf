#!/usr/bin/env python3
"""
pnr_screenshot.py — PNR Expert Auto Screenshot
Chụp đúng khung kết quả từ pnrexpert.com — nhanh, không timeout.
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
VIEWPORT_H        = 3000   # cao ngay từ đầu → không cần resize sau
BROWSERLESS_TOKEN = os.environ.get("BROWSERLESS_TOKEN", "").strip()

BL_ENDPOINTS = [
    "wss://production-sfo.browserless.io?token={t}",
    "wss://production-sfo.browserless.io?token={t}&launch=%7B%22stealth%22%3Atrue%7D",
    "wss://production-lon.browserless.io?token={t}",
    "wss://chrome.browserless.io?token={t}",
]

# ── JS: ẩn Co2 ─────────────────────────────────────────────────────────────────
JS_HIDE_CO2 = r"""() => {
    let n = 0;
    document.querySelectorAll('*').forEach(el => {
        if (el.children.length === 0 &&
            /tonnes?\s+of\s+co2/i.test(el.textContent || '')) {
            let cur = el;
            for (let i = 0; i < 6 && cur && cur !== document.body; i++) {
                if (/tonnes?\s+of\s+co2/i.test(cur.innerText || '') &&
                    !/depart|arriv|flight|outbound|return/i.test(cur.innerText || '')) {
                    cur.style.display = 'none';
                    n++; break;
                }
                cur = cur.parentElement;
            }
        }
    });
    return n;
}"""

# ── JS: tắt animation ──────────────────────────────────────────────────────────
JS_NO_ANIM = r"""() => {
    const s = document.createElement('style');
    s.textContent = '*{animation:none!important;transition:none!important;}';
    document.head.appendChild(s);
}"""

# ── JS chính: tìm ĐÚNG phần nội dung bảng chuyến bay (bên dưới toolbar) ──────
# pnrexpert.com có cấu trúc: [toolbar buttons] → [bảng nội dung trắng]
# Phải lấy phần nội dung (bảng trắng), KHÔNG lấy wrapper bao cả toolbar.
JS_FIND_RESULT = r"""() => {
    // ── Chiến lược 1: Tìm phần tử chứa nội dung bảng bên dưới toolbar ─────────
    // pnrexpert render nội dung vào một div có bg trắng, chứa table với
    // thông tin DEPARTURE/ARRIVAL/Outbound/Return
    const flightKeywords = /departs?:|arrives?:|outbound|return.*(?:city|airport)|duration:/i;

    // Tìm tất cả div/section có chứa nội dung chuyến bay thực sự
    const candidates = Array.from(document.querySelectorAll('div, section, table'))
        .filter(el => {
            const txt = el.innerText || '';
            if (!flightKeywords.test(txt)) return false;
            const r = el.getBoundingClientRect();
            // Phải có kích thước hợp lý và nằm trong viewport
            if (r.width < 300 || r.height < 100) return false;
            // Loại bỏ body và các container cực lớn bao cả trang
            if (r.width > window.innerWidth * 0.98 && r.height > window.innerHeight * 1.5) return false;
            return true;
        })
        .map(el => {
            const r = el.getBoundingClientRect();
            const txt = el.innerText || '';
            // Ưu tiên element nhỏ nhất vừa đủ chứa nội dung (tránh lấy wrapper to)
            // Tính điểm: nhiều keyword = tốt, diện tích nhỏ = tốt
            const keyCount = (txt.match(/departs?:|arrives?:|duration:|outbound|return/gi) || []).length;
            const area = r.width * r.height;
            return { el, r, keyCount, area, scrollH: el.scrollHeight };
        })
        .filter(c => c.keyCount >= 2)
        .sort((a, b) => {
            // Ưu tiên: nhiều keyword hơn, diện tích nhỏ hơn
            if (b.keyCount !== a.keyCount) return b.keyCount - a.keyCount;
            return a.area - b.area;
        });

    if (candidates.length > 0) {
        const best = candidates[0];
        const r = best.r;
        return {
            strategy: 'flight-content-box',
            x: Math.round(r.left), y: Math.round(r.top),
            w: Math.round(r.width), h: Math.round(r.height),
            scrollH: best.scrollH,
        };
    }

    // ── Chiến lược 2: Tìm toolbar "Copy to Clipboard" → lấy SIBLING sau nó ────
    // Toolbar và nội dung thường là anh em (siblings), không phải cha-con
    const copyBtn = Array.from(document.querySelectorAll('button, span, div'))
        .find(el => /copy to clipboard/i.test(el.innerText || el.textContent || ''));

    if (copyBtn) {
        // Tìm toolbar wrapper (cha gần nhất của nút Copy)
        let toolbar = copyBtn;
        for (let i = 0; i < 5; i++) {
            const p = toolbar.parentElement;
            if (!p || p === document.body) break;
            const pr = p.getBoundingClientRect();
            // Toolbar thường hẹp (height < 100px)
            if (pr.height < 120) { toolbar = p; continue; }
            break;
        }

        // Tìm sibling TIẾP THEO của toolbar — đó là bảng nội dung
        let sib = toolbar.nextElementSibling;
        for (let i = 0; i < 6 && sib; i++) {
            const r = sib.getBoundingClientRect();
            const txt = sib.innerText || '';
            if (r.width > 300 && r.height > 150 && flightKeywords.test(txt)) {
                return {
                    strategy: 'toolbar-next-sibling-' + i,
                    x: Math.round(r.left), y: Math.round(r.top),
                    w: Math.round(r.width), h: Math.round(r.height),
                    scrollH: sib.scrollHeight,
                };
            }
            sib = sib.nextElementSibling;
        }

        // Nếu không tìm được sibling, leo lên 1 cấp rồi thử lại
        const toolbarParent = toolbar.parentElement;
        if (toolbarParent) {
            sib = toolbarParent.nextElementSibling;
            for (let i = 0; i < 4 && sib; i++) {
                const r = sib.getBoundingClientRect();
                const txt = sib.innerText || '';
                if (r.width > 300 && r.height > 150 && flightKeywords.test(txt)) {
                    return {
                        strategy: 'toolbar-parent-next-' + i,
                        x: Math.round(r.left), y: Math.round(r.top),
                        w: Math.round(r.width), h: Math.round(r.height),
                        scrollH: sib.scrollHeight,
                    };
                }
                sib = sib.nextElementSibling;
            }
        }
    }

    // ── Chiến lược 3: bg trắng + có chứa table chuyến bay ────────────────────
    const whiteBoxes = Array.from(document.querySelectorAll('div'))
        .filter(el => {
            const style = window.getComputedStyle(el);
            const bg = style.backgroundColor;
            const isWhite = bg === 'rgb(255, 255, 255)' || bg === 'rgba(0, 0, 0, 0)';
            if (!isWhite) return false;
            const r = el.getBoundingClientRect();
            if (r.width < 400 || r.height < 200) return false;
            const txt = el.innerText || '';
            return flightKeywords.test(txt);
        })
        .map(el => {
            const r = el.getBoundingClientRect();
            return { el, r, scrollH: el.scrollHeight, area: r.width * r.height };
        })
        .sort((a, b) => a.area - b.area);

    if (whiteBoxes.length > 0) {
        const best = whiteBoxes[0];
        const r = best.r;
        return {
            strategy: 'white-box',
            x: Math.round(r.left), y: Math.round(r.top),
            w: Math.round(r.width), h: Math.round(r.height),
            scrollH: best.scrollH,
        };
    }

    return null;
}"""

# ── JS: bỏ overflow để hiện full nội dung ─────────────────────────────────────
JS_REMOVE_OVERFLOW = r"""(selector_info) => {
    // Tìm lại element theo tọa độ và bỏ overflow
    if (!selector_info) return 0;
    const { x, y, w } = selector_info;
    const el = document.elementFromPoint(x + w/2, y + 10);
    if (!el) return 0;
    
    let cur = el;
    for (let i = 0; i < 10; i++) {
        if (!cur || cur === document.body) break;
        const r = cur.getBoundingClientRect();
        if (r.width > 300 && cur.scrollHeight > 200) {
            const style = window.getComputedStyle(cur);
            if (style.overflow !== 'visible' || style.overflowY !== 'visible') {
                cur.style.overflow = 'visible';
                cur.style.maxHeight = 'none';
                cur.style.height = 'auto';
            }
        }
        cur = cur.parentElement;
    }
    return 1;
}"""


# ══════════════════════════════════════════════════════════════════════════════
# Core automation flow
# ══════════════════════════════════════════════════════════════════════════════

async def _run_flow(page, pnr_text: str, output_path: str, verbose: bool) -> bool:
    try:
        # 1. Mở trang — chỉ chờ DOMContentLoaded (nhanh hơn networkidle)
        if verbose: print("  [1] goto pnrexpert.com...")
        await page.goto(PNR_URL, timeout=25_000, wait_until="domcontentloaded")
        # Chờ textarea xuất hiện thay vì networkidle
        await page.wait_for_selector("textarea", timeout=10_000)

        # 2. Paste PNR
        if verbose: print("  [2] paste PNR...")
        ta = page.locator("textarea").first
        await ta.click()
        await ta.fill(pnr_text.strip())

        # 3. Click Quick Convert
        if verbose: print("  [3] Quick Convert...")
        await page.locator("button:has-text('Quick Convert')").click()

        # 4. Chờ kết quả — dùng wait_for_selector thay vì wait_for_function
        if verbose: print("  [4] chờ kết quả...")
        try:
            # Chờ element có text "Departs" hoặc "Outbound" → kết quả đã render
            await page.wait_for_function(
                r"() => document.body.innerText.includes('Departs') || "
                r"      document.body.innerText.includes('Outbound')",
                timeout=20_000,
                polling=500,   # check mỗi 500ms thay vì default 100ms
            )
            if verbose: print("  [4] ✅ kết quả đã hiện")
        except Exception:
            if verbose: print("  [4] ⚠️ timeout — thử chụp")
        await asyncio.sleep(1)   # buffer nhỏ để render xong

        # 5. Tắt animation + ẩn Co2
        await page.evaluate(JS_NO_ANIM)
        n = await page.evaluate(JS_HIDE_CO2)
        if verbose: print(f"  [5] ẩn {n} Co2 element")

        # 6. Tìm khung kết quả
        info = await page.evaluate(JS_FIND_RESULT)
        if verbose: print(f"  [6] result box: {info}")

        if not info:
            if verbose: print("  [6] ⚠️ không tìm thấy khung — chụp full page")
            await page.screenshot(path=output_path, full_page=True)
            return os.path.getsize(output_path) > 5000

        # 7. Bỏ overflow để nội dung không bị cắt
        await page.evaluate(JS_REMOVE_OVERFLOW, info)
        await asyncio.sleep(0.3)

        # 7b. Scroll về top để y-coordinate khớp với viewport
        await page.evaluate("() => window.scrollTo(0, 0)")
        await asyncio.sleep(0.2)

        # 7c. Lấy lại tọa độ sau scroll (getBoundingClientRect thay đổi theo scroll)
        info = await page.evaluate(JS_FIND_RESULT)
        if not info:
            if verbose: print("  [7c] ⚠️ mất info sau scroll — chụp full page")
            await page.screenshot(path=output_path, full_page=True)
            return os.path.getsize(output_path) > 5000
        if verbose: print(f"  [7c] info sau scroll: {info}")

        # 8. Tính clip — PAD_TOP lớn hơn để không cắt phần đầu "Outbound / Return"
        PAD_X   = 10
        PAD_TOP = 30   # đủ rộng để giữ header Outbound/Return
        PAD_BOT = 20

        x = max(0, info["x"] - PAD_X)
        y = max(0, info["y"] - PAD_TOP)
        w = min(VIEWPORT_W - x, info["w"] + PAD_X * 2)
        # Dùng max(scrollH, h) phòng trường hợp scrollH < chiều cao thực
        h = max(info["scrollH"], info["h"]) + PAD_TOP + PAD_BOT

        clip = {"x": x, "y": y, "width": w, "height": h}
        if verbose: print(f"  [8] clip: {clip}")

        await page.screenshot(path=output_path, clip=clip, full_page=False)

        size = os.path.getsize(output_path) if os.path.exists(output_path) else 0
        if verbose: print(f"  [8] saved {size//1024} KB")
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
        # Scan từ đáy lên, tìm dòng cuối có nội dung thực
        for y in range(h - 1, max(h - 120, 0), -1):
            row = [img.getpixel((x, y)) for x in range(0, w, 8)]
            # Pixel không phải trắng thuần và không phải đen thuần
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
