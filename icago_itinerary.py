#!/usr/bin/env python3
"""
icago_itinerary.py — ICAGO Itinerary Converter
================================================
✅ Windows / macOS / Linux — KHÔNG cần pdftotext.
✅ Mỗi cụm FLIGHT luôn ở cùng một trang (không bị tách).

Cài đặt:  pip install reportlab pillow pdfplumber

Dùng CLI:
  python icago_itinerary.py Itinerary.pdf
  python icago_itinerary.py Itinerary.pdf output.pdf -v
  python icago_itinerary.py --help

Dùng import (từ bot.py):
  from icago_itinerary import convert
  convert("input.pdf", "output.pdf", logo_path=..., luuy_path=...)
"""

import argparse
import os
import re
import sys

# ── Thư viện ───────────────────────────────────────────────────────────────────
try:
    import pdfplumber
except ImportError:
    sys.exit("❌ pip install pdfplumber")

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import (
        SimpleDocTemplate, Spacer, Image as RLImage, Flowable, KeepTogether
    )
except ImportError:
    sys.exit("❌ pip install reportlab")

try:
    from PIL import Image as PILImage
except ImportError:
    sys.exit("❌ pip install pillow")

# ── Đường dẫn ảnh mặc định ────────────────────────────────────────────────────
_SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
DEFAULT_LOGO = os.path.join(_SCRIPT_DIR, "icago_logo.png")
DEFAULT_LUUY = os.path.join(_SCRIPT_DIR, "icago_luuy.png")

# ── Layout ─────────────────────────────────────────────────────────────────────
PAGE_W, PAGE_H = letter
MARGIN_L  = 50
MARGIN_R  = 50
CONTENT_W = PAGE_W - MARGIN_L - MARGIN_R
FONT_MONO = "Courier"
FONT_BOLD = "Courier-Bold"
FONT_SIZE = 8.5
LINE_H    = 12


# ══════════════════════════════════════════════════════════════════════════════
# 1. Trích xuất văn bản (layout mode — không cần pdftotext)
# ══════════════════════════════════════════════════════════════════════════════

def extract_pages(pdf_path, verbose=False):
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text(layout=True, x_density=7.25, y_density=13) or ""
            pages.append(text)
    if verbose:
        print(f"  [extract] {len(pages)} trang trong '{pdf_path}'")
    return pages


# ══════════════════════════════════════════════════════════════════════════════
# 2. Parse header trang 1
# ══════════════════════════════════════════════════════════════════════════════

def parse_header(header_lines, verbose=False):
    """
    Header có 2 cột nối trên cùng dòng:
      '          TUONG LAI VIET (BSP)              BOOKING REF: E6ZDTU'
    Lấy phần phải của gap đầu tiên sau indent.
    Giữ: BOOKING REF, tên hành khách. Bỏ: DATE, địa chỉ.
    """
    kept = []
    for line in header_lines:
        stripped = line.rstrip()
        if not stripped.strip():
            continue
        m_indent = re.match(r'^\s+', stripped)
        content_start = m_indent.end() if m_indent else 0
        rest = stripped[content_start:]
        m_gap = re.search(r'\s{4,}', rest)
        if not m_gap:
            continue
        right = rest[m_gap.end():].strip()
        if not right:
            continue
        if re.match(r'^DATE\s*:', right, re.IGNORECASE):
            if verbose: print(f"  [header] bỏ DATE   : {repr(right)}")
            continue
        if re.match(r'^BOOKING\s+REF\s*:', right, re.IGNORECASE):
            kept.append(right)
            if verbose: print(f"  [header] giữ       : {repr(right)}")
            continue
        if re.match(r'^[A-Z\-]+/[A-Z]', right):
            kept.append(right)
            if verbose: print(f"  [header] hành khách: {repr(right)}")
    return kept


# ══════════════════════════════════════════════════════════════════════════════
# 3. Xóa dòng không cần
# ══════════════════════════════════════════════════════════════════════════════

_DELETE_RE = [re.compile(p, re.IGNORECASE) for p in [
    r"FLIGHT\(S\)\s+CALCULATED\s+AVERAGE\s+CO2",
    r"SOURCE:\s*ICAO\s+CARBON\s+EMISSIONS",
    r"https?://www\.icao\.int",
    r"HTTPS?://BAGS\.AMADEUS\.COM",
    r"CHECK\s+YOUR\s+TRIP\s+ONLINE",
    r"CLICK\s+HERE",
    r"Data\s+Protection\s+Notice\s*:",
    r"with\s+the\s+applicable\s+carrier",
    r"a\s+reservation\s+system\s+provider",
    r"available\s+at\s+or\s+from\s+the\s+carrier",
    r"documentation,\s+which\s+applies\s+to\s+your\s+booking",
    r"your\s+personal\s+data\s+is\s+collected",
    r"\(applicable\s+for\s+interline\s+carriage\)",
]]

def _should_delete(line):
    return any(rx.search(line.strip()) for rx in _DELETE_RE)

def clean_body(lines, verbose=False):
    result, skip_rest = [], False
    for line in lines:
        s = line.strip()
        if re.match(r"Data\s+Protection\s+Notice\s*:", s, re.IGNORECASE):
            skip_rest = True
        if skip_rest:
            continue
        if _should_delete(line):
            if verbose: print(f"  [body] xóa: {repr(s[:80])}")
            continue
        result.append(line)
    while result and not result[-1].strip():
        result.pop()
    return result


# ══════════════════════════════════════════════════════════════════════════════
# 4. Xử lý trang
# ══════════════════════════════════════════════════════════════════════════════

def process_pages(pages, verbose=False):
    header_lines, body_lines = [], []
    for idx, page_text in enumerate(pages):
        lines = page_text.split("\n")
        if idx == 0:
            flight_idx = next(
                (i for i, l in enumerate(lines)
                 if re.search(r'FLIGHT\s+', l)
                 and not re.search(r'FLIGHT\s+BOOKING', l)),
                len(lines),
            )
            header_lines = parse_header(lines[:flight_idx], verbose)
            body_lines   = clean_body(lines[flight_idx:], verbose)
            if verbose:
                print(f"  [page 1] header={len(header_lines)}, body={len(body_lines)} dòng")
        else:
            if verbose:
                print(f"  [page {idx+1}] bỏ qua")
    return header_lines, body_lines


# ══════════════════════════════════════════════════════════════════════════════
# 5. Style dòng
# ══════════════════════════════════════════════════════════════════════════════

_BOLD_RE = [
    re.compile(r"^\s*FLIGHT\s+(?!BOOKING)", re.IGNORECASE),
    re.compile(r"^\s*DEPARTURE\s*:",        re.IGNORECASE),
    re.compile(r"^\s*ARRIVAL\s*:",          re.IGNORECASE),
    re.compile(r"^\s*FLIGHT\s+TICKET\(S\)", re.IGNORECASE),
]
_FLIGHT_START_RE = re.compile(r"^\s*FLIGHT\s+(?!BOOKING|TICKET)", re.IGNORECASE)
_TICKET_START_RE = re.compile(r"^\s*FLIGHT\s+TICKET\(S\)",        re.IGNORECASE)

def is_bold(line):
    return any(rx.match(line) for rx in _BOLD_RE)

def is_flight_start(line):
    """True nếu dòng bắt đầu một cụm FLIGHT mới."""
    return bool(_FLIGHT_START_RE.match(line))

def is_ticket_start(line):
    """True nếu dòng bắt đầu phần FLIGHT TICKET(S)."""
    return bool(_TICKET_START_RE.match(line))


# ══════════════════════════════════════════════════════════════════════════════
# 6. Nhóm body thành các "flight block" — dùng KeepTogether
# ══════════════════════════════════════════════════════════════════════════════

def group_into_blocks(body_lines):
    """
    Chia body_lines thành các cụm:
      - Mỗi cụm bắt đầu khi gặp dòng FLIGHT ... (không phải FLIGHT BOOKING / TICKET)
      - Cụm FLIGHT TICKET(S) và sau đó là phần cuối
    Trả về list of list[str].
    """
    blocks  = []
    current = []

    for line in body_lines:
        if is_flight_start(line) or is_ticket_start(line):
            if current:
                blocks.append(current)
            current = [line]
        else:
            current.append(line)

    if current:
        blocks.append(current)

    return blocks


# ══════════════════════════════════════════════════════════════════════════════
# 7. ReportLab Flowable
# ══════════════════════════════════════════════════════════════════════════════

class MonoLine(Flowable):
    def __init__(self, text, bold=False):
        super().__init__()
        self.text   = text
        self.bold   = bold
        self.width  = CONTENT_W
        self.height = LINE_H

    def draw(self):
        self.canv.setFont(FONT_BOLD if self.bold else FONT_MONO, FONT_SIZE)
        self.canv.drawString(0, 2, self.text)


# ══════════════════════════════════════════════════════════════════════════════
# 8. Build PDF
# ══════════════════════════════════════════════════════════════════════════════

def _scaled_image(path, max_w):
    img = PILImage.open(path)
    w, h = img.size
    return RLImage(path, width=max_w, height=max_w * h / w)


def build_pdf(header_lines, body_lines, output_path, logo_path, luuy_path, verbose=False):
    story = []

    # Logo
    if logo_path and os.path.isfile(logo_path):
        story.append(_scaled_image(logo_path, CONTENT_W))
        story.append(Spacer(1, 8))
    else:
        print(f"  ⚠️  Không tìm thấy logo: {logo_path}")

    # Header
    for line in header_lines:
        story.append(MonoLine(line, bold=False))
    story.append(Spacer(1, 6))

    # Body — nhóm thành các flight block, mỗi block dùng KeepTogether
    blocks = group_into_blocks(body_lines)
    if verbose:
        print(f"  [build] {len(blocks)} flight block(s)")

    for block_idx, block in enumerate(blocks):
        elements = []
        for line in block:
            elements.append(MonoLine(line, bold=is_bold(line)))

        # KeepTogether giữ toàn bộ block trên cùng một trang
        # Nếu block quá dài (> 40 dòng), chia đôi để tránh lỗi overflow
        if len(elements) <= 40:
            story.append(KeepTogether(elements))
        else:
            mid = len(elements) // 2
            story.append(KeepTogether(elements[:mid]))
            story.append(KeepTogether(elements[mid:]))

        if verbose:
            print(f"    block {block_idx+1}: {len(block)} dòng")

    # Lưu ý
    story.append(Spacer(1, 14))
    if luuy_path and os.path.isfile(luuy_path):
        story.append(_scaled_image(luuy_path, CONTENT_W))
    else:
        print(f"  ⚠️  Không tìm thấy ảnh Lưu ý: {luuy_path}")

    SimpleDocTemplate(
        output_path,
        pagesize=letter,
        leftMargin=MARGIN_L,
        rightMargin=MARGIN_R,
        topMargin=30,
        bottomMargin=30,
    ).build(story)


# ══════════════════════════════════════════════════════════════════════════════
# 9. Public API — dùng khi import từ bot.py
# ══════════════════════════════════════════════════════════════════════════════

def convert(input_path, output_path, logo_path=DEFAULT_LOGO, luuy_path=DEFAULT_LUUY, verbose=False):
    """
    Hàm chính để gọi từ code khác (Telegram bot, Flask, v.v.)

    Ví dụ:
        from icago_itinerary import convert
        convert("Itinerary.pdf", "output.pdf")
    """
    if not os.path.isfile(input_path):
        raise FileNotFoundError(f"Không tìm thấy: {input_path}")
    pages = extract_pages(input_path, verbose)
    header, body = process_pages(pages, verbose)
    build_pdf(header, body, output_path, logo_path, luuy_path, verbose)
    return output_path


# ══════════════════════════════════════════════════════════════════════════════
# 10. CLI
# ══════════════════════════════════════════════════════════════════════════════

def parse_args():
    p = argparse.ArgumentParser(
        prog="icago_itinerary",
        description="Chuyển đổi lịch trình GDS/Amadeus sang định dạng ICAGO PDF.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ví dụ:
  python icago_itinerary.py Itinerary.pdf
  python icago_itinerary.py Itinerary.pdf KhachHang.pdf
  python icago_itinerary.py Itinerary.pdf -o KhachHang.pdf --logo logo.png --luuy luuy.png
  python icago_itinerary.py Itinerary.pdf -v
        """,
    )
    p.add_argument("input",  help="File PDF đầu vào")
    p.add_argument("output", nargs="?", default=None,
                   help="File PDF đầu ra (mặc định: <input>_ICAGO.pdf)")
    p.add_argument("-o", "--output-file", dest="output_file", default=None, metavar="PATH")
    p.add_argument("--logo", default=DEFAULT_LOGO, metavar="FILE",
                   help="Ảnh logo ICAGO  (mặc định: icago_logo.png)")
    p.add_argument("--luuy", default=DEFAULT_LUUY, metavar="FILE",
                   help="Ảnh Lưu ý       (mặc định: icago_luuy.png)")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def main():
    args = parse_args()
    output = args.output_file or args.output or \
             os.path.splitext(args.input)[0] + "_ICAGO.pdf"

    print(f"📄 Input  : {args.input}")
    print(f"📝 Output : {output}")
    print(f"🖼️  Logo   : {args.logo}")
    print(f"🖼️  Lưu ý : {args.luuy}")
    if args.verbose:
        print("🔍 Verbose ON\n")

    convert(args.input, output, args.logo, args.luuy, args.verbose)
    print(f"✅ Hoàn thành! → {output}  ({os.path.getsize(output)//1024} KB)")


if __name__ == "__main__":
    main()
