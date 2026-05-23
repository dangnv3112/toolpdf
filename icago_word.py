#!/usr/bin/env python3
"""
icago_word.py — ICAGO Word (.docx) -> PDF Converter

Chức năng:
  - Bo header logo cu, thay bang icago_logo.png
  - Trich xuat FLIGHT BOOKING REF: ... dau tien -> hien len dau trai
  - Bo dong "Passenger(s):" (giu ten hanh khach)
  - In dam: FLIGHT, DEPARTURE, ARRIVAL, FLIGHT TICKET(S)
  - To do + in dam: FLIGHT TICKET(S) va cac dong TICKET ben duoi
  - Them icago_luuy.png cuoi trang
  - Output co the dat ten tuy y

Cai dat:
    pip install python-docx reportlab pillow

Dung CLI:
    python icago_word.py input.docx
    python icago_word.py input.docx OUTPUT.pdf
    python icago_word.py input.docx -o OUTPUT.pdf --logo logo.png --luuy luuy.png -v

Dung import:
    from icago_word import convert_docx
    convert_docx("input.docx", "output.pdf")
"""

import argparse
import os
import re
import sys

# ── Thu vien ───────────────────────────────────────────────────────────────────

try:
    from docx import Document
except ImportError:
    sys.exit("❌ pip install python-docx")

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import (
        SimpleDocTemplate, Spacer, Image as RLImage, Flowable, KeepTogether
    )
    from reportlab.lib import colors
except ImportError:
    sys.exit("❌ pip install reportlab")

try:
    from PIL import Image as PILImage
except ImportError:
    sys.exit("❌ pip install pillow")

# ── Duong dan anh mac dinh ────────────────────────────────────────────────────

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
LINE_H    = 10.5   # tight line height
EMPTY_H   = 4      # dong trong nho hon

# ── Regex ──────────────────────────────────────────────────────────────────────

_BOLD_RE = [
    re.compile(r"^FLIGHT\s+(?!BOOKING)",   re.IGNORECASE),
    re.compile(r"^DEPARTURE\s*:",          re.IGNORECASE),
    re.compile(r"^ARRIVAL\s*:",            re.IGNORECASE),
    re.compile(r"^FLIGHT\s+TICKET\(S\)",   re.IGNORECASE),
    re.compile(r"^STATUS\s*:",             re.IGNORECASE),
    re.compile(r"^CLASS\s*:",              re.IGNORECASE),
    re.compile(r"^AIRCRAFT\s*:",           re.IGNORECASE),
    re.compile(r"^DURATION\s*:",           re.IGNORECASE),
    re.compile(r"^OPERATED\s+BY\s*:",      re.IGNORECASE),
    re.compile(r"^SEAT\s*:",               re.IGNORECASE),
    re.compile(r"^MEAL\s*:",               re.IGNORECASE),
    re.compile(r"^CABIN\s*:",              re.IGNORECASE),
    re.compile(r"^STOP\s*:",               re.IGNORECASE),
    re.compile(r"^EQUIPMENT\s*:",          re.IGNORECASE),
    re.compile(r"^MARKETING\s+CARRIER\s*:",re.IGNORECASE),
    re.compile(r"^OPERATING\s+CARRIER\s*:",re.IGNORECASE),
    re.compile(r"^TERMINAL\s*:",           re.IGNORECASE),
    re.compile(r"^CHECK-?IN\s*:",          re.IGNORECASE),
    re.compile(r"^BAGGAGE\s*:",            re.IGNORECASE),
    re.compile(r"^FARE\s+BASIS\s*:",       re.IGNORECASE),
    re.compile(r"^NOT\s+VALID\s",          re.IGNORECASE),
    re.compile(r"^FREQUENCY\s*:",          re.IGNORECASE),
]
_RED_RE = [
    re.compile(r"^FLIGHT\s+TICKET\(S\)", re.IGNORECASE),
    re.compile(r"^TICKET\s*:",           re.IGNORECASE),
]
_FLIGHT_START_RE = re.compile(r"^FLIGHT\s+(?!BOOKING|TICKET)", re.IGNORECASE)
_TICKET_START_RE = re.compile(r"^FLIGHT\s+TICKET\(S\)",        re.IGNORECASE)
_BOOKING_REF_RE  = re.compile(r"FLIGHT\s+BOOKING\s+REF\s*:\s*(\S+)", re.IGNORECASE)

LOGO_MAX_W = CONTENT_W * 0.35

# ── Junk lines to delete ───────────────────────────────────────────────────────

_DELETE_RE = [re.compile(p, re.IGNORECASE) for p in [
    r"Please\s+check\s*[-]\s*in",
    r"Vui\s+l[o]ng\s+c[o]\s+m[a]t",
    r"Thank\s+you\s+for\s+purchasing",
    r"FLIGHT(S)\s+CALCULATED\s+AVERAGE\s+CO2",
    r"SOURCE:\s*ICAO\s+CARBON\s+EMISSIONS",
    r"https?://www.icao.int",
    r"HTTPS?://BAGS.AMADEUS.COM",
    r"CHECK\s+YOUR\s+TRIP\s+ONLINE",
    r"CLICK\s+HERE",
    r"Data\s+Protection\s+Notice\s*:",
    r"BAGGAGE\s+POLICY\s*-\s*FOR\s+TRAVEL",
    r"IF\s+YOU\s+ARE\s+DENIED\s+BOARDING",
    r"ENTITLED\s+TO\s+CERTAIN\s+STANDARDS",
    r"PASSENGER\s+RIGHTS\s+PLEASE\s+CONTACT",
    r"CANADIAN\s+TRANSPORTATION",
]]


def _should_delete(line):
    s = line.strip()
    return any(rx.search(s) for rx in _DELETE_RE)


# ══════════════════════════════════════════════════════════════════════════════
# 1. Trich xuat van ban tu DOCX
# ══════════════════════════════════════════════════════════════════════════════

def extract_docx_lines(docx_path, verbose=False):
    """Doc toan bo paragraphs tu .docx, tra ve list[str]. Bo qua paragraphs chua image (logo cu)."""
    doc = Document(docx_path)
    lines = []
    skipped_imgs = 0

    for para in doc.paragraphs:
        has_drawing = any(
            run._element.find('.//{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}anchor') is not None or
            run._element.find('.//{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}inline') is not None
            for run in para.runs
        )
        if has_drawing:
            skipped_imgs += 1
            if verbose:
                print(f"  [extract] bo image paragraph #{skipped_imgs}")
            continue

        text = para.text
        lines.append(text)

    if verbose:
        print(f"  [extract] {len(lines)} dong, bo {skipped_imgs} image paragraph")
    return lines


# ══════════════════════════════════════════════════════════════════════════════
# 2. Xu ly lines
# ══════════════════════════════════════════════════════════════════════════════

def process_lines(raw_lines, verbose=False):
    """Tra ve (booking_ref: str|None, passenger_lines: list[str], body_lines: list[str])"""
    booking_ref = None
    for line in raw_lines:
        m = _BOOKING_REF_RE.search(line)
        if m and not booking_ref:
            ref = m.group(1)
            ref = re.sub(r'^[A-Za-z]{2}/', '', ref)
            booking_ref = ref
            if verbose:
                print(f"  [process] BOOKING REF: {booking_ref}")

    passengers = []
    body_lines = []
    found_flight = False
    skip_rest = False

    for line in raw_lines:
        stripped = line.strip()

        if skip_rest:
            continue

        if re.match(r"Data\s+Protection\s+Notice", stripped, re.IGNORECASE):
            skip_rest = True
            continue

        if _should_delete(line):
            if verbose:
                print(f"  [process] xoa: {repr(stripped[:80])}")
            continue

        if re.match(r"^FLIGHT\s+", stripped, re.IGNORECASE) and not re.match(r"^FLIGHT\s+BOOKING", stripped, re.IGNORECASE):
            found_flight = True

        if not found_flight:
            if re.match(r"^Passenger\(s\)\s*:", stripped, re.IGNORECASE):
                if verbose:
                    print(f"  [process] bo Passenger(s): label")
                continue
            if re.match(r"^\s*FLIGHT\s+BOOKING\s+REF", stripped, re.IGNORECASE):
                continue
            if stripped:
                if re.search(r'www\.|@|TELEPHONE\s*:|EMAIL\s*:|BSP\b', stripped, re.IGNORECASE):
                    continue
                passengers.append(stripped)
            continue

        body_lines.append(stripped)

    while body_lines and not body_lines[-1]:
        body_lines.pop()

    # Collapse consecutive empty lines
    collapsed = []
    prev_empty = False
    for line in body_lines:
        if not line.strip():
            if not prev_empty:
                collapsed.append(line)
            prev_empty = True
        else:
            collapsed.append(line)
            prev_empty = False
    body_lines = collapsed

    if verbose:
        print(f"  [process] passengers: {passengers}")
        print(f"  [process] body lines: {len(body_lines)}")

    return booking_ref, passengers, body_lines


# ══════════════════════════════════════════════════════════════════════════════
# 3. Style helpers
# ══════════════════════════════════════════════════════════════════════════════

def is_bold(line):
    return any(rx.match(line.strip()) for rx in _BOLD_RE)


def is_red(line):
    return any(rx.match(line.strip()) for rx in _RED_RE)


def is_flight_start(line):
    return bool(_FLIGHT_START_RE.match(line.strip()))


def is_ticket_start(line):
    return bool(_TICKET_START_RE.match(line.strip()))


# ══════════════════════════════════════════════════════════════════════════════
# 4. Nhom body thanh flight blocks
# ══════════════════════════════════════════════════════════════════════════════

def group_into_blocks(body_lines):
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
# 5. ReportLab Flowables
# ══════════════════════════════════════════════════════════════════════════════

class MonoLine(Flowable):
    def __init__(self, text, bold=False, red=False, color=None):
        super().__init__()
        self.text   = text
        self.bold   = bold or red
        self.color  = colors.red if red else (color or colors.black)
        self.width  = CONTENT_W
        self.height = EMPTY_H if not text.strip() else LINE_H

    def draw(self):
        if not self.text.strip():
            return
        self.canv.setFont(FONT_BOLD if self.bold else FONT_MONO, FONT_SIZE)
        self.canv.setFillColor(self.color)
        self.canv.drawString(0, 2, self.text)
        self.canv.setFillColor(colors.black)


# ══════════════════════════════════════════════════════════════════════════════
# 6. Build PDF
# ══════════════════════════════════════════════════════════════════════════════

def _scaled_image(path, max_w):
    img = PILImage.open(path)
    w, h = img.size
    return RLImage(path, width=max_w, height=max_w * h / w)


def build_pdf(booking_ref, passengers, body_lines, output_path,
              logo_path, luuy_path, verbose=False):
    story = []

    # Logo ICAGO
    if logo_path and os.path.isfile(logo_path):
        story.append(_scaled_image(logo_path, LOGO_MAX_W))
        story.append(Spacer(1, 6))
    else:
        print(f"  ⚠️ Khong tim thay logo: {logo_path}")

    # BOOKING REF + Passengers
    if booking_ref:
        story.append(MonoLine(f"BOOKING REF: {booking_ref}", bold=True))
    for p in passengers:
        if re.match(r'^BOOKING\s+REF\s*:', p, re.IGNORECASE) and booking_ref:
            continue
        story.append(MonoLine(p, bold=True))
    story.append(Spacer(1, 5))

    # Body: nhom thanh flight blocks + KeepTogether
    blocks = group_into_blocks(body_lines)
    if verbose:
        print(f"  [build] {len(blocks)} flight block(s)")

    for block_idx, block in enumerate(blocks):
        elements = []
        in_ticket_section = False
        in_flight_section = False

        for line in block:
            s = line.strip()
            if is_ticket_start(s):
                in_ticket_section = True

            if is_flight_start(s):
                in_flight_section = True

            line_red  = in_ticket_section or is_red(s)
            line_bold = is_bold(s) or line_red or in_flight_section

            elements.append(MonoLine(line, bold=line_bold, red=line_red))

            if re.match(r'^ARRIVAL\s*:', s, re.IGNORECASE):
                in_flight_section = False

        if len(elements) <= 40:
            story.append(KeepTogether(elements))
        else:
            mid = len(elements) // 2
            story.append(KeepTogether(elements[:mid]))
            story.append(KeepTogether(elements[mid:]))

        if verbose:
            print(f"    block {block_idx+1}: {len(block)} dong")

    # Luu y
    story.append(Spacer(1, 8))
    if luuy_path and os.path.isfile(luuy_path):
        story.append(_scaled_image(luuy_path, CONTENT_W))
    else:
        print(f"  ⚠️ Khong tim thay anh Luu y: {luuy_path}")

    SimpleDocTemplate(
        output_path,
        pagesize=letter,
        leftMargin=MARGIN_L,
        rightMargin=MARGIN_R,
        topMargin=30,
        bottomMargin=30,
    ).build(story)


# ══════════════════════════════════════════════════════════════════════════════
# 7. Public API
# ══════════════════════════════════════════════════════════════════════════════

def convert_docx(input_path, output_path=None,
                 logo_path=DEFAULT_LOGO, luuy_path=DEFAULT_LUUY,
                 verbose=False):
    """
    Chuyen doi file .docx -> PDF dinh dang ICAGO.

    Args:
        input_path  : Duong dan file .docx dau vao
        output_path : Duong dan file .pdf dau ra (mac dinh: <input>_ICAGO.pdf)
        logo_path   : Logo ICAGO
        luuy_path   : Anh Luu y
        verbose     : In thong tin debug

    Returns:
        str: Duong dan file PDF da tao
    """
    if not os.path.isfile(input_path):
        raise FileNotFoundError(f"Khong tim thay: {input_path}")

    if output_path is None:
        base = os.path.splitext(input_path)[0]
        output_path = base + "_ICAGO.pdf"

    raw_lines = extract_docx_lines(input_path, verbose)
    booking_ref, passengers, body_lines = process_lines(raw_lines, verbose)
    build_pdf(booking_ref, passengers, body_lines, output_path,
              logo_path, luuy_path, verbose)
    return output_path


# ══════════════════════════════════════════════════════════════════════════════
# 8. CLI
# ══════════════════════════════════════════════════════════════════════════════

def parse_args():
    p = argparse.ArgumentParser(
        prog="icago_word",
        description="Chuyen doi lich trinh Word (.docx) sang PDF dinh dang ICAGO.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Vi du:
  python icago_word.py input.docx
  python icago_word.py input.docx LUONG_PHUNG_GIA.pdf
  python icago_word.py input.docx -o LUONG_PHUNG_GIA.pdf --logo logo.png --luuy luuy.png
  python icago_word.py input.docx -v
""",
    )
    p.add_argument("input",  help="File .docx dau vao")
    p.add_argument("output", nargs="?", default=None,
                   help="File PDF dau ra (mac dinh: <input>_ICAGO.pdf)")
    p.add_argument("-o", "--output-file", dest="output_file", default=None, metavar="PATH",
                   help="File PDF dau ra (thay the cho positional 'output')")
    p.add_argument("--logo", default=DEFAULT_LOGO, metavar="FILE",
                   help="Anh logo ICAGO  (mac dinh: icago_logo.png)")
    p.add_argument("--luuy", default=DEFAULT_LUUY, metavar="FILE",
                   help="Anh Luu y       (mac dinh: icago_luuy.png)")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def main():
    args = parse_args()
    output = args.output_file or args.output or \
             os.path.splitext(args.input)[0] + "_ICAGO.pdf"

    print(f"📄 Input  : {args.input}")
    print(f"📝 Output : {output}")
    print(f"🖼️  Logo   : {args.logo}")
    print(f"🖼️  Luu y : {args.luuy}")
    if args.verbose:
        print("🔍 Verbose ON\n")

    result = convert_docx(args.input, output, args.logo, args.luuy, args.verbose)
    size_kb = os.path.getsize(result) // 1024
    print(f"✅ Hoan thanh! -> {result}  ({size_kb} KB)")


if __name__ == "__main__":
    main()
