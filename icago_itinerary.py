# #!/usr/bin/env python3
“””
icago_itinerary.py — ICAGO Itinerary Converter

✅ Windows / macOS / Linux — KHÔNG cần pdftotext.
✅ Mỗi cụm FLIGHT luôn ở cùng một trang (không bị tách).

Cài đặt:  pip install reportlab pillow pdfplumber

Dùng CLI:
python icago_itinerary.py Itinerary.pdf
python icago_itinerary.py Itinerary.pdf output.pdf -v
python icago_itinerary.py –help

Dùng import (từ bot.py):
from icago_itinerary import convert
convert(“input.pdf”, “output.pdf”, logo_path=…, luuy_path=…)
“””

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

# ── Đường dẫn ────────────────────────────────────────────────────────────────
_SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
DEFAULT_LOGO = os.path.join(_SCRIPT_DIR, "icago_logo.png")
DEFAULT_LUUY = os.path.join(_SCRIPT_DIR, "icago_luuy.png")

# ── Layout ───────────────────────────────────────────────────────────────────
PAGE_W, PAGE_H = letter
MARGIN_L  = 50
MARGIN_R  = 50
CONTENT_W = PAGE_W - MARGIN_L - MARGIN_R
FONT_MONO = "Courier"
FONT_BOLD = "Courier-Bold"
FONT_SIZE = 8.5
LINE_H    = 12
COLOR_BLACK    = colors.black
COLOR_RED_BOLD = colors.HexColor("#CC0000")

# Logo và Lưu ý: chiều rộng tối đa = 60% content width (không quá rộng)
IMG_MAX_W = CONTENT_W * 0.60


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
        print(f"  [extract] {len(pages)} trang")
    return pages


# ══════════════════════════════════════════════════════════════════════════════
# 2. Parse header — chỉ giữ BOOKING REF + tên hành khách
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

        # Bỏ dòng DATE
        if re.match(r'^DATE\s*:', right, re.IGNORECASE):
            if verbose: print(f"  [header] bỏ DATE")
            continue

        # Giữ BOOKING REF — bỏ mã hãng "XX/" phía trước nếu có
        if re.match(r'^BOOKING\s+REF\s*:', right, re.IGNORECASE):
            # Xóa pattern "XX/" (2 chữ cái + dấu /)
            cleaned = re.sub(r'\b[A-Z]{2}/(?=[A-Z0-9])', '', right)
            kept.append(cleaned)
            if verbose: print(f"  [header] BOOKING REF: {cleaned}")
            continue

        # Giữ tên hành khách (HO/TEN)
        if re.match(r'^[A-Z\-]+/[A-Z]', right):
            kept.append(right)
            if verbose: print(f"  [header] hành khách: {right}")

    return kept


# ══════════════════════════════════════════════════════════════════════════════
# 3. Xóa các dòng không cần
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
    r"BAGGAGE\s+POLICY\s*[-–]",
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
            if verbose: print(f"  [body] xóa: {s[:80]}")
            continue
        result.append(line)
    while result and not result[-1].strip():
        result.pop()
    return result


# ══════════════════════════════════════════════════════════════════════════════
# 4. Xử lý TẤT CẢ trang (không bỏ qua trang 2+)
# ══════════════════════════════════════════════════════════════════════════════

def process_pages(pages, verbose=False):
    header_lines = []
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
                print(f"  [page 1] header={len(header_lines)}, body={len(page_body)}")
        else:
            page_body = clean_body(lines, verbose)
            if verbose:
                print(f"  [page {idx+1}] body={len(page_body)}")

        all_body_lines.extend(page_body)

    return header_lines, all_body_lines


# ══════════════════════════════════════════════════════════════════════════════
# 5. Phân loại dòng
# ══════════════════════════════════════════════════════════════════════════════

# Các dòng giữa FLIGHT và ARRIVAL → in đậm
_FLIGHT_TO_ARRIVAL_RE = [
    re.compile(r"^\s*FLIGHT\s+(?!BOOKING|TICKET)", re.IGNORECASE),
    re.compile(r"^\s*OPERATED\s+BY:",                re.IGNORECASE),
    re.compile(r"^\s*-{5,}"),   # dòng gạch ngang
    re.compile(r"^\s*DEPARTURE\s*:",                 re.IGNORECASE),
    re.compile(r"^\s*ARRIVAL\s*:",                   re.IGNORECASE),
]

def is_flight_to_arrival(line):
    """Dòng từ FLIGHT đến ARRIVAL → in đậm."""
    return any(rx.match(line) for rx in _FLIGHT_TO_ARRIVAL_RE)

# FLIGHT TICKET(S) và TICKET: → đỏ đậm
def is_ticket_red(line):
    s = line.strip()
    return bool(
        re.match(r"FLIGHT\s+TICKET\(S\)", s, re.IGNORECASE) or
        re.match(r"TICKET\s*:", s, re.IGNORECASE) or
        re.match(r"[A-Z]{2}/ETKT\s", s, re.IGNORECASE)
    )

_FLIGHT_START_RE = re.compile(r"^\s*FLIGHT\s+(?!BOOKING|TICKET)", re.IGNORECASE)
_TICKET_START_RE = re.compile(r"^\s*FLIGHT\s+TICKET\(S\)", re.IGNORECASE)

def is_flight_start(line):
    return bool(_FLIGHT_START_RE.match(line))

def is_ticket_start(line):
    return bool(_TICKET_START_RE.match(line))


# ══════════════════════════════════════════════════════════════════════════════
# 6. Nhóm thành flight blocks
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
# 7. ReportLab Flowable
# ══════════════════════════════════════════════════════════════════════════════

class MonoLine(Flowable):
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
        self.canv.setFillColor(COLOR_BLACK)


# ══════════════════════════════════════════════════════════════════════════════
# 8. Build PDF
# ══════════════════════════════════════════════════════════════════════════════

def _scaled_image(path, max_w):
    """Scale ảnh theo chiều rộng max_w, giữ tỷ lệ."""
    img = PILImage.open(path)
    w, h = img.size
    scale = min(max_w / w, 1.0)  # không phóng to nếu ảnh nhỏ hơn max_w
    new_w = w * scale
    new_h = h * scale
    return RLImage(path, width=new_w, height=new_h)


def build_pdf(header_lines, body_lines, output_path, logo_path, luuy_path, verbose=False):
    story = []

    # ── Logo ICAGO (thu nhỏ) ──────────────────────────────────────────────────
    if logo_path and os.path.isfile(logo_path):
        story.append(_scaled_image(logo_path, IMG_MAX_W))
        story.append(Spacer(1, 8))
    else:
        print(f"  ⚠️  Không tìm thấy logo: {logo_path}")

    # ── Header: BOOKING REF + tên khách — IN ĐẬM ──────────────────────────────
    for line in header_lines:
        story.append(MonoLine(line, bold=True, red=False))
    story.append(Spacer(1, 6))

    # ── Body blocks ────────────────────────────────────────────────────────────
    blocks = group_into_blocks(body_lines)
    if verbose:
        print(f"  [build] {len(blocks)} block(s)")

    for block_idx, block in enumerate(blocks):
        elements = []
        for line in block:
            # Ưu tiên 1: Ticket đỏ
            if is_ticket_red(line):
                elements.append(MonoLine(line, bold=False, red=True))
            # Ưu tiên 2: FLIGHT đến ARRIVAL → đậm đen
            elif is_flight_to_arrival(line):
                elements.append(MonoLine(line, bold=True, red=False))
            # Ưu tiên 3: Bình thường
            else:
                elements.append(MonoLine(line, bold=False, red=False))

        if len(elements) <= 40:
            story.append(KeepTogether(elements))
        else:
            mid = len(elements) // 2
            story.append(KeepTogether(elements[:mid]))
            story.append(KeepTogether(elements[mid:]))

        if verbose:
            has_ticket = any(is_ticket_red(l) for l in block)
            print(f"    block {block_idx+1}: {len(block)} dòng{'  🔴' if has_ticket else ''}")

    # ── Lưu ý (thu nhỏ) ────────────────────────────────────────────────────────
    story.append(Spacer(1, 14))
    if luuy_path and os.path.isfile(luuy_path):
        story.append(_scaled_image(luuy_path, IMG_MAX_W))
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
        epilog="python icago_itinerary.py Itinerary.pdf -v",
    )
    p.add_argument("input",  help="File PDF đầu vào")
    p.add_argument("output", nargs="?", default=None)
    p.add_argument("-o", "--output-file", dest="output_file", default=None)
    p.add_argument("--logo", default=DEFAULT_LOGO)
    p.add_argument("--luuy", default=DEFAULT_LUUY)
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def main():
    args = parse_args()
    output = args.output_file or args.output or \
             os.path.splitext(args.input)[0] + "_ICAGO.pdf"

    print(f"📄 Input  : {args.input}")
    print(f"📝 Output : {output}")

    convert(args.input, output, args.logo, args.luuy, args.verbose)
    print(f"✅ Hoàn thành! ({os.path.getsize(output)//1024} KB)")


if __name__ == "__main__":
    main()
