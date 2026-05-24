#!/usr/bin/env python3
"""
icago_word.py — ICAGO Word (.docx) -> PDF Converter
Layout dong nhat voi icago_itinerary.py:
  - Logo ~42% width, canh giua
  - BOOKING REF (bold) + ten khach (bold)
  - FLIGHT line: label | info bold | date bold right
  - DEPARTURE/ARRIVAL: bold trai, gio bold phai
  - OPERATED BY: normal, indent
  - Separator ---: full width
  - BAGGAGE/SEAT/MEAL/EQUIPMENT/NON STOP: normal
  - FLIGHT TICKET(S): do dam + dashes
  - TICKET:: do dam
  - Khoang cach 16pt giua cac block
"""

import argparse, os, re, sys

try:
    from docx import Document
except ImportError:
    sys.exit("pip install python-docx")

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import (
        SimpleDocTemplate, Spacer, Image as RLImage, Flowable, KeepTogether
    )
    from reportlab.lib import colors
except ImportError:
    sys.exit("pip install reportlab")

try:
    from PIL import Image as PILImage
except ImportError:
    sys.exit("pip install pillow")

# ── Paths ──────────────────────────────────────────────────────────────────────
_DIR         = os.path.dirname(os.path.abspath(__file__))
DEFAULT_LOGO = os.path.join(_DIR, "icago_logo.png")
DEFAULT_LUUY = os.path.join(_DIR, "icago_luuy.png")

# ── Layout (dong nhat voi icago_itinerary) ─────────────────────────────────────
PAGE_W, PAGE_H = letter
ML = 46; MR = 46; MT = 24; MB = 28
CW = PAGE_W - ML - MR        # ~520 pt

FN  = "Courier"
FB  = "Courier-Bold"
FS  = 8.5
CW_ = FS * 0.6               # char width
LH  = 11.5
LHE = 3

BLACK = colors.black
RED   = colors.HexColor("#CC0000")

FLIGHT_LABEL_W = 7  * CW_
FLIGHT_DATE_W  = 17 * CW_
DEPTARR_LBL_W  = 11 * CW_
LOGO_W         = CW * 0.42

# ── Regexes ────────────────────────────────────────────────────────────────────
_FLIGHT_RE    = re.compile(r"^FLIGHT\s+(?!BOOKING|TICKET)", re.I)
_TICKET_HD_RE = re.compile(r"^FLIGHT\s+TICKET\(S\)",        re.I)
_OPERATED_RE  = re.compile(r"^OPERATED\s+BY\s*:",           re.I)
_DEP_RE       = re.compile(r"^DEPARTURE\s*:",               re.I)
_ARR_RE       = re.compile(r"^ARRIVAL\s*:",                 re.I)
_SEPARATOR_RE = re.compile(r"^-{5,}")
_DASHES_RE    = re.compile(r"^[-\s]+$")
_TICKET_LN_RE = re.compile(r"^TICKET\s*:",                  re.I)
_BOOKING_RE   = re.compile(r"FLIGHT\s+BOOKING\s+REF\s*:\s*(\S+)", re.I)

_TRAIL_TIME_RE = re.compile(
    r'\s+(\d{1,2}\s+[A-Z]{3}\s+(?:\d{2}:\d{2}|\d{4}))\s*$', re.I
)
_FLIGHT_PARSE_RE = re.compile(
    r'^FLIGHT\s+(.*?)\s{3,}([A-Z]{3}\s+\d{1,2}\s+\S+\s+\d{4})\s*$', re.I
)

_DELETE_RE = [re.compile(p, re.I) for p in [
    r"Please\s+check\s*[-]\s*in",
    r"Thank\s+you\s+for\s+purchasing",
    r"FLIGHT.S.\s+CALCULATED\s+AVERAGE\s+CO2",
    r"SOURCE:\s*ICAO\s+CARBON\s+EMISSIONS",
    r"https?://www\.icao\.int",
    r"HTTPS?://BAGS\.AMADEUS\.COM",
    r"CHECK\s+YOUR\s+TRIP\s+ONLINE",
    r"CLICK\s+HERE",
    r"Data\s+Protection\s+Notice\s*:",
    r"BAGGAGE\s+POLICY\s*[-]",
    r"IF\s+YOU\s+ARE\s+DENIED\s+BOARDING",
    r"ENTITLED\s+TO\s+CERTAIN\s+STANDARDS",
    r"PASSENGER\s+RIGHTS\s+PLEASE\s+CONTACT",
    r"CANADIAN\s+TRANSPORTATION",
    r"RESERVATION\s+CONFIRMED",
    r"FLIGHT\s+BOOKING\s+REF\s*:",
]]


def _del(s):
    return any(rx.search(s) for rx in _DELETE_RE)


# ══════════════════════════════════════════════════════════════════════════════
# Flowables — giong het icago_itinerary
# ══════════════════════════════════════════════════════════════════════════════

class MonoLine(Flowable):
    def __init__(self, text, bold=False, red=False, indent=0):
        super().__init__()
        self.text   = (text or "").rstrip()
        self.bold   = bold or red
        self.red    = red
        self.indent = indent
        self.width  = CW
        self.height = LHE if not self.text.strip() else LH

    def draw(self):
        if not self.text.strip():
            return
        self.canv.setFont(FB if self.bold else FN, FS)
        self.canv.setFillColor(RED if self.red else BLACK)
        self.canv.drawString(self.indent, 2, self.text)
        self.canv.setFillColor(BLACK)


class FlightLine(Flowable):
    def __init__(self, info, date):
        super().__init__()
        self.info   = info.strip()
        self.date   = date.strip()
        self.width  = CW
        self.height = LH

    def draw(self):
        self.canv.setFont(FB, FS)
        self.canv.setFillColor(BLACK)
        self.canv.drawString(0, 2, "FLIGHT")
        self.canv.drawString(FLIGHT_LABEL_W + 4, 2, self.info)
        dw = self.canv.stringWidth(self.date, FB, FS)
        self.canv.drawString(CW - dw, 2, self.date)


class DepArrLine(Flowable):
    def __init__(self, label, location, time_part):
        super().__init__()
        self.label     = (label or "").strip()
        self.location  = (location or "").strip()
        self.time_part = (time_part or "").strip()
        self.width  = CW
        self.height = LH

    def draw(self):
        self.canv.setFont(FB, FS)
        self.canv.setFillColor(BLACK)
        lw = self.canv.stringWidth(self.label + " ", FB, FS)
        self.canv.drawString(0, 2, self.label)
        self.canv.drawString(lw, 2, self.location)
        if self.time_part:
            tw = self.canv.stringWidth(self.time_part, FB, FS)
            self.canv.drawString(CW - tw, 2, self.time_part)


class SepLine(Flowable):
    def __init__(self):
        super().__init__()
        self.width  = CW
        self.height = LH

    def draw(self):
        self.canv.setFont(FN, FS)
        self.canv.setFillColor(BLACK)
        n = int(CW / (FS * 0.6))
        self.canv.drawString(0, 2, "-" * n)


class TicketHeaderLine(Flowable):
    def __init__(self):
        super().__init__()
        self.width  = CW
        self.height = LH

    def draw(self):
        label = "FLIGHT TICKET(S) "
        self.canv.setFont(FB, FS)
        self.canv.setFillColor(RED)
        self.canv.drawString(0, 2, label)
        lw = self.canv.stringWidth(label, FB, FS)
        dw = self.canv.stringWidth("-", FN, FS)
        n  = max(0, int((CW - lw) / dw))
        self.canv.setFont(FN, FS)
        self.canv.drawString(lw, 2, "-" * n)
        self.canv.setFillColor(BLACK)


# ══════════════════════════════════════════════════════════════════════════════
# Extract paragraphs from DOCX
# ══════════════════════════════════════════════════════════════════════════════

def extract_docx_lines(docx_path, verbose=False):
    doc = Document(docx_path)
    lines = []
    skipped = 0
    for para in doc.paragraphs:
        has_img = any(
            run._element.find('.//{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}anchor') is not None or
            run._element.find('.//{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}inline') is not None
            for run in para.runs
        )
        if has_img:
            skipped += 1
            continue
        lines.append(para.text)
    if verbose:
        print(f"  [extract] {len(lines)} lines, skipped {skipped} image paragraphs")
    return lines


# ══════════════════════════════════════════════════════════════════════════════
# Process lines -> header + body
# ══════════════════════════════════════════════════════════════════════════════

def process_lines(raw_lines, verbose=False):
    """Returns (booking_ref, passenger_lines, body_lines)"""
    # Find booking ref
    booking_ref = None
    for line in raw_lines:
        m = _BOOKING_RE.search(line)
        if m and not booking_ref:
            ref = re.sub(r'^[A-Za-z]{2}/', '', m.group(1))
            booking_ref = ref
            if verbose:
                print(f"  [process] BOOKING REF: {booking_ref}")

    passengers   = []
    body_lines   = []
    found_flight = False
    skip_rest    = False

    for line in raw_lines:
        s = line.strip()
        if skip_rest:
            continue
        if re.match(r"Data\s+Protection\s+Notice", s, re.I):
            skip_rest = True
            continue
        if _del(s):
            if verbose:
                print(f"  [del] {repr(s[:70])}")
            continue

        # Detect start of flight content
        if re.match(r"^FLIGHT\s+", s, re.I) and not re.match(r"^FLIGHT\s+BOOKING", s, re.I):
            found_flight = True

        if not found_flight:
            if re.match(r"^Passenger\(s\)\s*:", s, re.I):
                continue
            if re.match(r"^FLIGHT\s+BOOKING\s+REF", s, re.I):
                continue
            if s and not re.search(r'www\.|@|TELEPHONE\s*:|EMAIL\s*:|BSP\b', s, re.I):
                passengers.append(s)
            continue

        body_lines.append(s)

    # Trim trailing blanks
    while body_lines and not body_lines[-1]:
        body_lines.pop()

    # Collapse consecutive blanks
    collapsed, prev_empty = [], False
    for line in body_lines:
        if not line:
            if not prev_empty:
                collapsed.append(line)
            prev_empty = True
        else:
            collapsed.append(line)
            prev_empty = False

    if verbose:
        print(f"  [process] pax={passengers}, body={len(collapsed)} lines")
    return booking_ref, passengers, collapsed


# ══════════════════════════════════════════════════════════════════════════════
# Group into flight blocks
# ══════════════════════════════════════════════════════════════════════════════

def group_blocks(body_lines):
    blocks, cur = [], []
    for line in body_lines:
        if _FLIGHT_RE.match(line) or _TICKET_HD_RE.match(line):
            if cur:
                blocks.append(cur)
            cur = [line]
        else:
            cur.append(line)
    if cur:
        blocks.append(cur)
    return blocks


# ══════════════════════════════════════════════════════════════════════════════
# Parse DEPARTURE / ARRIVAL
# ══════════════════════════════════════════════════════════════════════════════

def _parse_dep_arr(s):
    m = re.match(r'^(DEPARTURE:|ARRIVAL:)\s*', s, re.I)
    if not m:
        return None, s, None
    label = m.group(1).upper()
    rest  = s[m.end():]
    mt    = _TRAIL_TIME_RE.search(rest)
    if mt:
        return label, rest[:mt.start()].strip(), mt.group(1)
    return label, rest.strip(), None


# ══════════════════════════════════════════════════════════════════════════════
# Build block -> Flowable list
# ══════════════════════════════════════════════════════════════════════════════

def _block_to_elements(block):
    elements      = []
    after_dep_arr = False

    for raw in block:
        s = raw.strip()

        if not s:
            elements.append(MonoLine(""))
            after_dep_arr = False
            continue

        # FLIGHT TICKET(S)
        if _TICKET_HD_RE.match(s):
            elements.append(TicketHeaderLine())
            after_dep_arr = False
            continue

        # TICKET:
        if _TICKET_LN_RE.match(s):
            elements.append(MonoLine(s, red=True))
            after_dep_arr = False
            continue

        # Solid separator ----
        if _SEPARATOR_RE.match(s) and not _DASHES_RE.match(s):
            elements.append(SepLine())
            after_dep_arr = False
            continue

        # Dotted separator - - -
        if _DASHES_RE.match(s):
            elements.append(MonoLine(s))
            after_dep_arr = False
            continue

        # FLIGHT line
        if _FLIGHT_RE.match(s):
            m = _FLIGHT_PARSE_RE.match(s)
            if m:
                elements.append(FlightLine(m.group(1), m.group(2)))
            else:
                elements.append(MonoLine(s, bold=True))
            after_dep_arr = False
            continue

        # OPERATED BY
        if _OPERATED_RE.match(s):
            elements.append(MonoLine(s, indent=FLIGHT_LABEL_W + 4))
            after_dep_arr = False
            continue

        # DEPARTURE / ARRIVAL
        if _DEP_RE.match(s) or _ARR_RE.match(s):
            label, location, time_str = _parse_dep_arr(s)
            elements.append(DepArrLine(label, location, time_str))
            after_dep_arr = True
            continue

        # Continuation after DEP/ARR (e.g. TERMINAL 2)
        if after_dep_arr and not re.match(r'[A-Z ]+\s*:', s) and not _FLIGHT_RE.match(s):
            elements.append(MonoLine(s, indent=DEPTARR_LBL_W))
            continue
        else:
            after_dep_arr = False

        # Everything else: normal
        elements.append(MonoLine(s))

    return elements


# ══════════════════════════════════════════════════════════════════════════════
# Build PDF
# ══════════════════════════════════════════════════════════════════════════════

def _img(path, w, hAlign='LEFT'):
    img = PILImage.open(path)
    iw, ih = img.size
    ri = RLImage(path, width=w, height=w * ih / iw)
    ri.hAlign = hAlign
    return ri


def build_pdf(booking_ref, passengers, body_lines, output_path,
              logo_path, luuy_path, verbose=False):
    story = []

    # Logo ~42% width, centered
    if logo_path and os.path.isfile(logo_path):
        story.append(_img(logo_path, LOGO_W, hAlign='CENTER'))
        story.append(Spacer(1, 10))
    else:
        print(f"  WARN: logo not found: {logo_path}")

    # BOOKING REF (bold)
    if booking_ref:
        story.append(MonoLine(f"BOOKING REF: {booking_ref}", bold=True))
    story.append(Spacer(1, 4))

    # Passengers (bold)
    for p in passengers:
        if re.match(r'^BOOKING\s+REF\s*:', p, re.I) and booking_ref:
            continue
        story.append(MonoLine(p, bold=True))
    story.append(Spacer(1, 8))

    # Flight blocks
    blocks = group_blocks(body_lines)
    if verbose:
        print(f"  [build] {len(blocks)} blocks")

    for idx, block in enumerate(blocks):
        elems = _block_to_elements(block)
        # 16pt spacing between blocks (same as icago_itinerary)
        if idx > 0:
            story.append(Spacer(1, 16))
        if len(elems) <= 45:
            story.append(KeepTogether(elems))
        else:
            mid = len(elems) // 2
            story.append(KeepTogether(elems[:mid]))
            story.append(KeepTogether(elems[mid:]))
        if verbose:
            print(f"  block {idx+1}: {len(block)} lines -> {len(elems)} elements")

    # Luu y image
    story.append(Spacer(1, 14))
    if luuy_path and os.path.isfile(luuy_path):
        story.append(_img(luuy_path, CW))
    else:
        print(f"  WARN: luuy not found: {luuy_path}")

    SimpleDocTemplate(
        output_path,
        pagesize=letter,
        leftMargin=ML, rightMargin=MR,
        topMargin=MT, bottomMargin=MB,
    ).build(story)


# ══════════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════════

def convert_docx(input_path, output_path=None,
                 logo_path=DEFAULT_LOGO, luuy_path=DEFAULT_LUUY,
                 verbose=False):
    if not os.path.isfile(input_path):
        raise FileNotFoundError(f"Not found: {input_path}")
    if output_path is None:
        output_path = os.path.splitext(input_path)[0] + "_ICAGO.pdf"
    raw   = extract_docx_lines(input_path, verbose)
    ref, pax, body = process_lines(raw, verbose)
    build_pdf(ref, pax, body, output_path, logo_path, luuy_path, verbose)
    return output_path


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def parse_args():
    p = argparse.ArgumentParser(prog="icago_word")
    p.add_argument("input")
    p.add_argument("output", nargs="?", default=None)
    p.add_argument("-o", "--output-file", dest="output_file", default=None)
    p.add_argument("--logo", default=DEFAULT_LOGO)
    p.add_argument("--luuy", default=DEFAULT_LUUY)
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def main():
    args   = parse_args()
    output = args.output_file or args.output or \
             os.path.splitext(args.input)[0] + "_ICAGO.pdf"
    result = convert_docx(args.input, output, args.logo, args.luuy, args.verbose)
    print(f"Done -> {result} ({os.path.getsize(result)//1024} KB)")


if __name__ == "__main__":
    main()
