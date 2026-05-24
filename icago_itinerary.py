#!/usr/bin/env python3
"""
icago_itinerary.py — ICAGO Itinerary Converter v4
Layout theo mau LE_THI_KIM_THOA_X3:
  - Logo ~40% width, canh trai
  - BOOKING REF (bold) + ten khach (bold) sau khoang cach
  - FLIGHT line: "FLIGHT  UA xxx - AIRLINE         DOW DD MON YYYY"
    -> col1="FLIGHT" bold | col2=flight info bold | col3=date bold right-align
  - OPERATED BY: normal, indent
  - Separator "---": full width
  - DEPARTURE / ARRIVAL: label bold + location bold | date+time bold right-align
  - Continuation (TERMINAL 2): normal, indent
  - FLIGHT BOOKING REF: xoa
  - RESERVATION CONFIRMED: xoa
  - BAGGAGE / SEAT / MEAL / EQUIPMENT / NON STOP / AIRCRAFT OWNER / WHEELCHAIR: normal
  - FLIGHT TICKET(S): do dam, inline voi "---" separator
  - TICKET: do dam
  - Luu y: full width
"""

import argparse, os, re, sys

try:
    import pdfplumber
except ImportError:
    sys.exit("pip install pdfplumber")

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib import colors
    from reportlab.platypus import (
        SimpleDocTemplate, Spacer, Image as RLImage, Flowable, KeepTogether
    )
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

# ── Page / font constants ──────────────────────────────────────────────────────
PAGE_W, PAGE_H = letter          # 612 x 792 pt
ML = 46; MR = 46; MT = 24; MB = 28
CW = PAGE_W - ML - MR            # ~520 pt content width

FN   = "Courier"
FB   = "Courier-Bold"
FS   = 8.5                        # font size pt
CW_  = FS * 0.6                   # char width (~5.1 pt)
LH   = 11.5                       # normal line height
LHE  = 3                          # empty line height

BLACK = colors.black
RED   = colors.HexColor("#CC0000")

# Column widths for FLIGHT header row
# "FLIGHT" label = 6 chars
FLIGHT_LABEL_W = 7 * CW_          # ~36 pt
# Date "DOW DD MON YYYY" = ~16 chars
FLIGHT_DATE_W  = 17 * CW_         # ~87 pt
FLIGHT_INFO_W  = CW - FLIGHT_LABEL_W - FLIGHT_DATE_W   # middle

# DEPARTURE/ARRIVAL label width: "DEPARTURE: " = 11 chars, "ARRIVAL:   " = 11
DEPTARR_LBL_W  = 11 * CW_        # ~56 pt
DEPTARR_TIME_W = 13 * CW_        # ~66 pt  "DD MMM HH:MM"
DEPTARR_LOC_W  = CW - DEPTARR_LBL_W - DEPTARR_TIME_W

LOGO_W = CW * 0.38               # ~40% width

# ── Regexes ────────────────────────────────────────────────────────────────────
_FLIGHT_RE    = re.compile(r"^\s*FLIGHT\s+(?!BOOKING|TICKET)", re.I)
_TICKET_HD_RE = re.compile(r"^\s*FLIGHT\s+TICKET\(S\)",        re.I)
_OPERATED_RE  = re.compile(r"^\s*OPERATED\s+BY\s*:",           re.I)
_DEP_RE       = re.compile(r"^\s*DEPARTURE\s*:",               re.I)
_ARR_RE       = re.compile(r"^\s*ARRIVAL\s*:",                 re.I)
_SEPARATOR_RE = re.compile(r"^\s*-{5,}",                       re.I)
_DASHES_RE    = re.compile(r"^\s*[-\s]+$")
_TICKET_LN_RE = re.compile(r"^\s*TICKET\s*:",                  re.I)

# Trailing date+time: "DD MON HH:MM" or "DD MON YYYY" at end of line (1+ spaces before)
_TRAIL_TIME_RE = re.compile(
    r'\s+(\d{1,2}\s+[A-Z]{3}\s+(?:\d{2}:\d{2}|\d{4}))\s*$', re.I
)
# FLIGHT line date (right side): "DOW DD MON YYYY"
_FLIGHT_DATE_RE = re.compile(
    r'\s{3,}([A-Z]{3}\s+\d{1,2}\s+[A-Z]+\s+\d{4})\s*$', re.I
)
# Parse FLIGHT line: "FLIGHT   UA 468 - UNITED AIRLINES      MON 01 JUNE 2026"
_FLIGHT_PARSE_RE = re.compile(
    r'^\s*FLIGHT\s+(.*?)\s{3,}([A-Z]{3}\s+\d{1,2}\s+\S+\s+\d{4})\s*$', re.I
)

_DELETE_RE = [re.compile(p, re.I) for p in [
    r"FLIGHT.S.\s+CALCULATED\s+AVERAGE\s+CO2",
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
    r"applicable\s+for\s+interline\s+carriage",
    r"BAGGAGE\s+POLICY\s*[-]",
    r"IF\s+YOU\s+ARE\s+DENIED\s+BOARDING",
    r"ENTITLED\s+TO\s+CERTAIN\s+STANDARDS",
    r"PASSENGER\s+PROTECTION\s+REGULATIONS",
    r"RIGHTS\s+PLEASE\s+CONTACT\s+YOUR\s+AIR\s+CARRIER",
    r"AGENCY\s+WEBSITE",
    r"CANADIAN\s+TRANSPORTATION",
    r"RESERVATION\s+CONFIRMED",
    r"FLIGHT\s+BOOKING\s+REF\s*:",
]]


def _del(line):
    return any(rx.search(line.strip()) for rx in _DELETE_RE)


# ══════════════════════════════════════════════════════════════════════════════
# Flowables
# ══════════════════════════════════════════════════════════════════════════════

class MonoLine(Flowable):
    """Plain single line. bold/red optional."""
    def __init__(self, text, bold=False, red=False, indent=0):
        super().__init__()
        self.text   = text.rstrip() if text else ""
        self.bold   = bold or red
        self.red    = red
        self.indent = indent
        self.width  = CW
        self.height = LHE if not (text or "").strip() else LH

    def draw(self):
        if not self.text.strip():
            return
        self.canv.setFont(FB if self.bold else FN, FS)
        self.canv.setFillColor(RED if self.red else BLACK)
        self.canv.drawString(self.indent, 2, self.text)
        self.canv.setFillColor(BLACK)


class FlightLine(Flowable):
    """
    FLIGHT  |  UA 468 - UNITED AIRLINES  |  MON 01 JUNE 2026
    All bold, date right-aligned.
    """
    def __init__(self, info, date):
        super().__init__()
        self.info  = info.strip()
        self.date  = date.strip()
        self.width = CW
        self.height = LH

    def draw(self):
        self.canv.setFont(FB, FS)
        self.canv.setFillColor(BLACK)
        # "FLIGHT" label
        self.canv.drawString(0, 2, "FLIGHT")
        # flight info starting after label col
        self.canv.drawString(FLIGHT_LABEL_W + 4, 2, self.info)
        # date flush right
        dw = self.canv.stringWidth(self.date, FB, FS)
        self.canv.drawString(CW - dw, 2, self.date)


class DepArrLine(Flowable):
    """
    DEPARTURE: / ARRIVAL: with location bold left, time bold right.
    Handles continuation lines (e.g. TERMINAL 2) as plain indented.
    """
    def __init__(self, label, location, time_part):
        """
        label    : "DEPARTURE:" or "ARRIVAL:"
        location : rest of the location string
        time_part: "DD MON HH:MM" or None
        """
        super().__init__()
        self.label     = label.strip()
        self.location  = location.strip()
        self.time_part = (time_part or "").strip()
        self.width  = CW
        self.height = LH

    def draw(self):
        self.canv.setFont(FB, FS)
        self.canv.setFillColor(BLACK)
        # label
        lw = self.canv.stringWidth(self.label + " ", FB, FS)
        self.canv.drawString(0, 2, self.label)
        # location
        self.canv.drawString(lw, 2, self.location)
        # time right-aligned
        if self.time_part:
            tw = self.canv.stringWidth(self.time_part, FB, FS)
            self.canv.drawString(CW - tw, 2, self.time_part)


class SepLine(Flowable):
    """Full-width solid separator line (replaces --- chars)."""
    def __init__(self, dashes=True):
        super().__init__()
        self.dashes = dashes
        self.width  = CW
        self.height = LH

    def draw(self):
        # Draw actual dash characters to match the original style
        self.canv.setFont(FN, FS)
        self.canv.setFillColor(BLACK)
        n_dashes = int(CW / (FS * 0.6))
        self.canv.drawString(0, 2, "-" * n_dashes)


class TicketHeaderLine(Flowable):
    """FLIGHT TICKET(S) ---- red bold, with dashes to fill the line."""
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
        # fill rest with dashes
        dash_w = self.canv.stringWidth("-", FN, FS)
        n = max(0, int((CW - lw) / dash_w))
        self.canv.setFont(FN, FS)
        self.canv.drawString(lw, 2, "-" * n)
        self.canv.setFillColor(BLACK)


# ══════════════════════════════════════════════════════════════════════════════
# Extract
# ══════════════════════════════════════════════════════════════════════════════

def extract_pages(pdf_path, verbose=False):
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text(layout=True, x_density=7.25, y_density=13) or ""
            pages.append(text)
    if verbose:
        print(f"  [extract] {len(pages)} pages")
    return pages


# ══════════════════════════════════════════════════════════════════════════════
# Parse header (BOOKING REF + passengers)
# ══════════════════════════════════════════════════════════════════════════════

def parse_header(lines, verbose=False):
    kept = []
    for line in lines:
        s = line.strip()
        if not s:
            continue
        # Direct format
        if re.match(r'^BOOKING\s+REF\s*:', s, re.I):
            val = re.sub(r'(BOOKING\s+REF\s*:\s*)[A-Za-z]{2}/', r'\1', s, flags=re.I)
            kept.append(val); continue
        # Passenger name: LASTNAME/FIRSTNAME or with suffix like (Child)
        if re.match(r'^[A-Z][A-Z-]+/[A-Z]', s):
            kept.append(s); continue
        # Two-column layout
        m = re.match(r'^\s+', line)
        if not m:
            continue
        rest = line[m.end():]
        mg = re.search(r'\s{4,}', rest)
        if not mg:
            continue
        right = rest[mg.end():].strip()
        if not right:
            continue
        if re.match(r'^DATE\s*:', right, re.I):
            continue
        if re.match(r'^BOOKING\s+REF\s*:', right, re.I):
            right = re.sub(r'(BOOKING\s+REF\s*:\s*)[A-Za-z]{2}/', r'\1', right, flags=re.I)
            kept.append(right); continue
        if re.match(r'^[A-Z-]+/[A-Z]', right):
            kept.append(right)
    if verbose:
        print(f"  [header] {kept}")
    return kept


# ══════════════════════════════════════════════════════════════════════════════
# Clean
# ══════════════════════════════════════════════════════════════════════════════

def clean_body(lines, verbose=False):
    result, skip = [], False
    for line in lines:
        s = line.strip()
        if re.match(r"Data\s+Protection\s+Notice\s*:", s, re.I):
            skip = True
        if skip:
            continue
        if _del(line):
            if verbose: print(f"  [del] {repr(s[:70])}")
            continue
        result.append(line)
    while result and not result[-1].strip():
        result.pop()
    return result


# ══════════════════════════════════════════════════════════════════════════════
# Process pages
# ══════════════════════════════════════════════════════════════════════════════

def process_pages(pages, verbose=False):
    header_lines = []
    all_body     = []
    for idx, text in enumerate(pages):
        lines = text.split("\n")
        if idx == 0:
            fi = next(
                (i for i, l in enumerate(lines)
                 if re.search(r'FLIGHT\s+', l) and not re.search(r'FLIGHT\s+BOOKING', l)),
                len(lines)
            )
            header_lines = parse_header(lines[:fi], verbose)
            body = clean_body(lines[fi:], verbose)
        else:
            body = clean_body(lines, verbose)
        all_body.extend(body)
    return header_lines, all_body


# ══════════════════════════════════════════════════════════════════════════════
# Group into blocks
# ══════════════════════════════════════════════════════════════════════════════

def group_blocks(body_lines):
    blocks, cur = [], []
    for line in body_lines:
        s = line.strip()
        if _FLIGHT_RE.match(line) or _TICKET_HD_RE.match(s):
            if cur: blocks.append(cur)
            cur = [line]
        else:
            cur.append(line)
    if cur: blocks.append(cur)
    return blocks


# ══════════════════════════════════════════════════════════════════════════════
# Parse DEPARTURE / ARRIVAL line
# ══════════════════════════════════════════════════════════════════════════════

def _parse_dep_arr(raw):
    """
    Returns (label, location, time_str)
    e.g. "DEPARTURE: ORLANDO, FL (ORLANDO INTL), TERMINAL B   01 JUN 07:00"
    -> ("DEPARTURE:", "ORLANDO, FL (ORLANDO INTL), TERMINAL B", "01 JUN 07:00")
    """
    s = raw.strip()
    # Split label
    m = re.match(r'^(DEPARTURE:|ARRIVAL:)\s*', s, re.I)
    if not m:
        return None, s, None
    label = m.group(1).upper()
    rest  = s[m.end():]
    # Extract trailing time
    mt = _TRAIL_TIME_RE.search(rest)
    if mt:
        time_str = mt.group(1)
        location = rest[:mt.start()].strip()
    else:
        time_str = None
        location = rest.strip()
    return label, location, time_str


# ══════════════════════════════════════════════════════════════════════════════
# Build one block -> list of Flowables
# ══════════════════════════════════════════════════════════════════════════════

def _block_to_elements(block):
    elements = []
    after_dep_arr = False

    for raw in block:
        s = raw.strip()
        if not s:
            elements.append(MonoLine(""))
            after_dep_arr = False
            continue

        # ── FLIGHT TICKET(S) header ──────────────────────────────────────────
        if _TICKET_HD_RE.match(s):
            elements.append(TicketHeaderLine())
            after_dep_arr = False
            continue

        # ── TICKET: line ─────────────────────────────────────────────────────
        if _TICKET_LN_RE.match(s):
            elements.append(MonoLine(s, red=True))
            after_dep_arr = False
            continue

        # ── Solid separator ------ ────────────────────────────────────────────
        if _SEPARATOR_RE.match(raw) and not _DASHES_RE.match(raw):
            elements.append(SepLine())
            after_dep_arr = False
            continue

        # ── Dotted separator - - - ────────────────────────────────────────────
        if _DASHES_RE.match(s):
            elements.append(MonoLine(s))
            after_dep_arr = False
            continue

        # ── FLIGHT line ───────────────────────────────────────────────────────
        if _FLIGHT_RE.match(raw):
            m = _FLIGHT_PARSE_RE.match(raw)
            if m:
                elements.append(FlightLine(m.group(1), m.group(2)))
            else:
                elements.append(MonoLine(s, bold=True))
            after_dep_arr = False
            continue

        # ── OPERATED BY ───────────────────────────────────────────────────────
        if _OPERATED_RE.match(s):
            elements.append(MonoLine(s, indent=FLIGHT_LABEL_W + 4))
            after_dep_arr = False
            continue

        # ── DEPARTURE / ARRIVAL ───────────────────────────────────────────────
        if _DEP_RE.match(s) or _ARR_RE.match(s):
            label, location, time_str = _parse_dep_arr(s)
            elements.append(DepArrLine(label, location, time_str))
            after_dep_arr = True
            continue

        # ── Continuation after DEP/ARR (e.g. TERMINAL 2) ─────────────────────
        if after_dep_arr and not re.match(r'[A-Z ]+\s*:', s) and not _FLIGHT_RE.match(raw):
            elements.append(MonoLine(s, indent=DEPTARR_LBL_W))
            continue
        else:
            after_dep_arr = False

        # ── Everything else: normal ───────────────────────────────────────────
        elements.append(MonoLine(s))

    return elements


# ══════════════════════════════════════════════════════════════════════════════
# Build PDF
# ══════════════════════════════════════════════════════════════════════════════

def _img(path, w):
    img = PILImage.open(path)
    iw, ih = img.size
    return RLImage(path, width=w, height=w * ih / iw)


def build_pdf(header_lines, body_lines, out_path, logo_path, luuy_path, verbose=False):
    story = []

    # Logo ~40% width
    if logo_path and os.path.isfile(logo_path):
        story.append(_img(logo_path, LOGO_W))
        story.append(Spacer(1, 10))
    else:
        print(f"  WARN: logo not found: {logo_path}")

    # BOOKING REF (bold) + blank line + passengers (bold)
    booking_lines = [l for l in header_lines if re.match(r'^BOOKING\s+REF', l, re.I)]
    pax_lines     = [l for l in header_lines if not re.match(r'^BOOKING\s+REF', l, re.I)]
    for l in booking_lines:
        story.append(MonoLine(l, bold=True))
    if pax_lines:
        story.append(Spacer(1, 4))
        for l in pax_lines:
            story.append(MonoLine(l, bold=True))
    story.append(Spacer(1, 8))

    # Flight blocks
    blocks = group_blocks(body_lines)
    if verbose:
        print(f"  [build] {len(blocks)} blocks")

    for idx, block in enumerate(blocks):
        elems = _block_to_elements(block)
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
        out_path, pagesize=letter,
        leftMargin=ML, rightMargin=MR,
        topMargin=MT, bottomMargin=MB,
    ).build(story)


# ══════════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════════

def convert(input_path, output_path, logo_path=DEFAULT_LOGO, luuy_path=DEFAULT_LUUY, verbose=False):
    if not os.path.isfile(input_path):
        raise FileNotFoundError(f"Not found: {input_path}")
    pages = extract_pages(input_path, verbose)
    header, body = process_pages(pages, verbose)
    build_pdf(header, body, output_path, logo_path, luuy_path, verbose)
    return output_path


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def parse_args():
    p = argparse.ArgumentParser(prog="icago_itinerary")
    p.add_argument("input")
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
    convert(args.input, output, args.logo, args.luuy, args.verbose)
    print(f"Done -> {output} ({os.path.getsize(output)//1024} KB)")


if __name__ == "__main__":
    main()
