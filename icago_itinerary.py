#!/usr/bin/env python3
"""
icago_itinerary.py — ICAGO Itinerary Converter
✅ Windows / macOS / Linux — KHÔNG cần pdftotext.
✅ Mỗi cụm FLIGHT luôn ở cùng một trang (không bị tách).
✅ Phần FLIGHT TICKET(S) in đậm màu đỏ.

Cài đặt:
    pip install reportlab pillow pdfplumber
"""

import argparse
import os
import re
import sys

try:
    import pdfplumber
except ImportError:
    sys.exit("❌ pip install pdfplumber")

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib import colors
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
MARGIN_L       = 50
MARGIN_R       = 50
CONTENT_W      = PAGE_W - MARGIN_L - MARGIN_R
FONT_MONO      = "Courier"
FONT_BOLD      = "Courier-Bold"
FONT_SIZE      = 8.5
LINE_H         = 12
COLOR_BLACK    = colors.black
COLOR_RED_BOLD = colors.HexColor("#CC0000")   # đỏ đậm — dễ đọc trên giấy in

# Logo tối đa 35% content width
LOGO_MAX_W = CONTENT_W * 0.35


# ══════════════════════════════════════════════════════════════════════════════
# 1. Trích xuất văn bản
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
            if verbose:
                print(f"  [header] bỏ DATE : {repr(right)}")
            continue
        if re.match(r'^BOOKING\s+REF\s*:', right, re.IGNORECASE):
            # Bỏ "xx/" prefix trong mã ref nếu có (vd: "xx/N60HHP" → "N60HHP")
            right = re.sub(r'(BOOKING\s+REF\s*:\s*)[A-Za-z]{2}/', r'\1', right, flags=re.IGNORECASE)
            kept.append(right)
            if verbose:
                print(f"  [header] giữ : {repr(right)}")
            continue
        if re.match(r'^[A-Z-]+/[A-Z]', right):
            kept.append(right)
            if verbose:
                print(f"  [header] hành khách: {repr(right)}")
    return kept


# ══════════════════════════════════════════════════════════════════════════════
# 3. Xóa dòng không cần
# ══════════════════════════════════════════════════════════════════════════════

_DELETE_RE = [re.compile(p, re.IGNORECASE) for p in [
    r"FLIGHT(S)\s+CALCULATED\s+AVERAGE\s+CO2",
    r"SOURCE:\s*ICAO\s+CARBON\s+EMISSIONS",
    r"https?://www.icao.int",
    r"HTTPS?://BAGS.AMADEUS.COM",
    r"CHECK\s+YOUR\s+TRIP\s+ONLINE",
    r"CLICK\s+HERE",
    r"Data\s+Protection\s+Notice\s*:",
    r"with\s+the\s+applicable\s+carrier",
    r"a\s+reservation\s+system\s+provider",
    r"available\s+at\s+or\s+from\s+the\s+carrier",
    r"documentation,\s+which\s+applies\s+to\s+your\s+booking",
    r"your\s+personal\s+data\s+is\s+collected",
    r"(applicable\s+for\s+interline\s+carriage)",
    r"BAGGAGE\s+POLICY\s*[-]",
    r"IF\s+YOU\s+ARE\s+DENIED\s+BOARDING",
    r"ENTITLED\s+TO\s+CERTAIN\s+STANDARDS",
    r"PASSENGER\s+PROTECTION\s+REGULATIONS",
    r"RIGHTS\s+PLEASE\s+CONTACT\s+YOUR\s+AIR\s+CARRIER",
    r"AGENCY\s+WEBSITE",
    r"CANADIAN\s+TRANSPORTATION",
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
            if verbose:
                print(f"  [body] xóa: {repr(s[:80])}")
            continue
        result.append(line)
    while result and not result[-1].strip():
        result.pop()
    return result


# ══════════════════════════════════════════════════════════════════════════════
# 4. Xử lý trang
# ══════════════════════════════════════════════════════════════════════════════

def process_pages(pages, verbose=False):
    """
    Xử lý TẤT CẢ trang:
    - Trang 1: tách header (địa chỉ đại lý) + body (từ dòng FLIGHT đầu tiên)
    - Trang 2+: toàn bộ là body tiếp theo (không bỏ qua)
    """
    header_lines   = []
    all_body_lines = []

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
            page_body    = clean_body(lines[flight_idx:], verbose)
            if verbose:
                print(f"  [page 1] header={len(header_lines)}, body={len(page_body)} dòng")
        else:
            page_body = clean_body(lines, verbose)
            if verbose:
                print(f"  [page {idx+1}] body={len(page_body)} dòng (xử lý tiếp)")

        all_body_lines.extend(page_body)

    return header_lines, all_body_lines


# ══════════════════════════════════════════════════════════════════════════════
# 5. Phân loại style từng dòng
# ══════════════════════════════════════════════════════════════════════════════

_BOLD_RE = [
    re.compile(r"^\s*FLIGHT\s+(?!BOOKING)",    re.IGNORECASE),
    re.compile(r"^\s*DEPARTURE\s*:",           re.IGNORECASE),
    re.compile(r"^\s*ARRIVAL\s*:",             re.IGNORECASE),
    re.compile(r"^\s*FLIGHT\s+TICKET\(S\)",    re.IGNORECASE),
    re.compile(r"^\s*STATUS\s*:",              re.IGNORECASE),
    re.compile(r"^\s*CLASS\s*:",               re.IGNORECASE),
    re.compile(r"^\s*AIRCRAFT\s*:",            re.IGNORECASE),
    re.compile(r"^\s*DURATION\s*:",            re.IGNORECASE),
    re.compile(r"^\s*OPERATED\s+BY\s*:",       re.IGNORECASE),
    re.compile(r"^\s*SEAT\s*:",                re.IGNORECASE),
    re.compile(r"^\s*MEAL\s*:",                re.IGNORECASE),
    re.compile(r"^\s*CABIN\s*:",               re.IGNORECASE),
    re.compile(r"^\s*STOP\s*:",                re.IGNORECASE),
    re.compile(r"^\s*EQUIPMENT\s*:",           re.IGNORECASE),
    re.compile(r"^\s*MARKETING\s+CARRIER\s*:", re.IGNORECASE),
    re.compile(r"^\s*OPERATING\s+CARRIER\s*:", re.IGNORECASE),
    re.compile(r"^\s*TERMINAL\s*:",            re.IGNORECASE),
    re.compile(r"^\s*CHECK-?IN\s*:",           re.IGNORECASE),
    re.compile(r"^\s*BAGGAGE\s*:",             re.IGNORECASE),
    re.compile(r"^\s*FARE\s+BASIS\s*:",        re.IGNORECASE),
    re.compile(r"^\s*NOT\s+VALID\s",           re.IGNORECASE),
    re.compile(r"^\s*FREQUENCY\s*:",           re.IGNORECASE),
]
_FLIGHT_START_RE = re.compile(r"^\s*FLIGHT\s+(?!BOOKING|TICKET)", re.IGNORECASE)
_TICKET_START_RE = re.compile(r"^\s*FLIGHT\s+TICKET\(S\)",        re.IGNORECASE)


def is_bold(line):
    return any(rx.match(line) for rx in _BOLD_RE)


def is_flight_start(line):
    return bool(_FLIGHT_START_RE.match(line))


def is_ticket_start(line):
    return bool(_TICKET_START_RE.match(line))


# ══════════════════════════════════════════════════════════════════════════════
# 6. Nhóm thành flight blocks (KeepTogether)
# ══════════════════════════════════════════════════════════════════════════════

def group_into_blocks(body_lines):
    blocks, current = [], []
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
# 7. ReportLab Flowable — MonoLine
# ══════════════════════════════════════════════════════════════════════════════

class MonoLine(Flowable):
    """
    Một dòng monospace.
    - normal : Courier, đen
    - bold   : Courier-Bold, đen (FLIGHT / DEPARTURE / ARRIVAL)
    - red    : Courier-Bold, đỏ (FLIGHT TICKET(S) + dòng TICKET:)
    """
    def __init__(self, text, bold=False, red=False):
        super().__init__()
        self.text   = text
        self.bold   = bold
        self.red    = red
        self.width  = CONTENT_W
        self.height = LINE_H

    def draw(self):
        font  = FONT_BOLD if (self.bold or self.red) else FONT_MONO
        color = COLOR_RED_BOLD if self.red else COLOR_BLACK
        self.canv.setFont(font, FONT_SIZE)
        self.canv.setFillColor(color)
        self.canv.drawString(0, 2, self.text)
        self.canv.setFillColor(COLOR_BLACK)   # reset về đen


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
        story.append(_scaled_image(logo_path, LOGO_MAX_W))
        story.append(Spacer(1, 8))
    else:
        print(f"  ⚠️ Không tìm thấy logo: {logo_path}")

    # Header (BOOKING REF, tên hành khách) — in đậm
    for line in header_lines:
        story.append(MonoLine(line, bold=True, red=False))
    story.append(Spacer(1, 6))

    # Body — chia block, KeepTogether
    blocks = group_into_blocks(body_lines)
    if verbose:
        print(f"  [build] {len(blocks)} block(s)")

    for block_idx, block in enumerate(blocks):
        elements = []
        in_flight_section = False  # True từ dòng FLIGHT ... đến hết dòng ARRIVAL

        for line in block:
            s = line.strip()

            if is_flight_start(line):
                in_flight_section = True

            if re.match(r"FLIGHT\s+TICKET\(S\)", s, re.IGNORECASE) or \
               re.match(r"TICKET\s*:", s, re.IGNORECASE) or \
               re.match(r"[A-Z]{2}/ETKT\s", s, re.IGNORECASE):
                elements.append(MonoLine(line, bold=False, red=True))
                in_flight_section = False
            elif in_flight_section or is_bold(line):
                elements.append(MonoLine(line, bold=True, red=False))
            else:
                elements.append(MonoLine(line, bold=False, red=False))

            if re.match(r'^\s*ARRIVAL\s*:', s, re.IGNORECASE):
                in_flight_section = False

        if len(elements) <= 40:
            story.append(KeepTogether(elements))
        else:
            mid = len(elements) // 2
            story.append(KeepTogether(elements[:mid]))
            story.append(KeepTogether(elements[mid:]))

        if verbose:
            has_ticket = any(re.match(r"FLIGHT\s+TICKET\(S\)", l.strip(), re.IGNORECASE) for l in block)
            print(f"  block {block_idx+1}: {len(block)} dòng{' 🔴' if has_ticket else ''}")

    # Ảnh Lưu ý
    story.append(Spacer(1, 14))
    if luuy_path and os.path.isfile(luuy_path):
        story.append(_scaled_image(luuy_path, CONTENT_W))
    else:
        print(f"  ⚠️ Không tìm thấy ảnh Lưu ý: {luuy_path}")

    SimpleDocTemplate(
        output_path,
        pagesize=letter,
        leftMargin=MARGIN_L,
        rightMargin=MARGIN_R,
        topMargin=30,
        bottomMargin=30,
    ).build(story)


# ══════════════════════════════════════════════════════════════════════════════
# 9. Public API
# ══════════════════════════════════════════════════════════════════════════════

def convert(input_path, output_path, logo_path=DEFAULT_LOGO, luuy_path=DEFAULT_LUUY, verbose=False):
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
    p.add_argument("--logo", default=DEFAULT_LOGO, metavar="FILE")
    p.add_argument("--luuy", default=DEFAULT_LUUY, metavar="FILE")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def main():
    args = parse_args()
    output = args.output_file or args.output or \
             os.path.splitext(args.input)[0] + "_ICAGO.pdf"

    print(f"📄 Input  : {args.input}")
    print(f"📝 Output : {output}")
    if args.verbose:
        print("🔍 Verbose ON\n")

    convert(args.input, output, args.logo, args.luuy, args.verbose)
    print(f"✅ Hoàn thành! → {output} ({os.path.getsize(output)//1024} KB)")


if __name__ == "__main__":
    main()
