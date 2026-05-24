#!/usr/bin/env python3
"""
icago_itinerary.py — ICAGO Itinerary Converter v5
Layout do duoc chinh xac tu file mau KIM THOA:
  - Page A4: 595.5 x 842pt, margin L=59.5 R=49 T=24 B=28 -> CW=487pt
  - Logo: full content width (491pt)
  - FLIGHT: x=0 "FLIGHT", x=66 info bold, x=373 date bold (fixed col)
  - OPERATED BY: x=66 normal
  - DEPARTURE/ARRIVAL: x=0 label bold, x=66 location bold, x=397 time bold
  - TERMINAL continuation: x=66 bold (same row style as DEP/ARR body)
  - BAGGAGE/SEAT/MEAL/EQUIPMENT: x=66 label, x=227 value - normal
  - Separator ---: full width
  - FLIGHT TICKET(S): red bold + dashes
  - TICKET: red bold
  - Block spacing: 16pt
"""

import argparse, os, re, sys

try:
    import pdfplumber
except ImportError:
    sys.exit("pip install pdfplumber")

try:
    from reportlab.lib.pagesizes import A4
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

# ── Page geometry (measured from KIM THOA sample) ─────────────────────────────
PAGE_W, PAGE_H = A4           # 595.3 x 841.9 pt
ML = 59                       # left margin (measured: content starts x=59.5)
MR = 49                       # right margin
MT = 24
MB = 28
CW = PAGE_W - ML - MR        # ~487 pt

# ── Fonts ──────────────────────────────────────────────────────────────────────
FN  = "Courier"
FB  = "Courier-Bold"
FS  = 8.5
LH  = 12.0    # line height (measured: top diff ~12pt)
LHE = 3       # empty line height

BLACK = colors.black
RED   = colors.HexColor("#CC0000")

# ── Column offsets (measured from content left = 0) ───────────────────────────
# FLIGHT line: "FLIGHT" at 0, info at 66, date at 373
COL_INDENT   = 66             # x0=125.6 - x0=59.5 = 66
COL_DATE     = 373            # x0=427.1 - x0=59.5 = 367 -> use 373 for letter
COL_TIME     = 397            # x0=457 - x0=59.5 (time in DEP/ARR)
COL_VALUE    = 160            # x0=286.4 - x0=59.5 (BAGGAGE value col, approx)

LOGO_W = CW                   # full content width

# ── Regexes ────────────────────────────────────────────────────────────────────
_FLIGHT_RE    = re.compile(r"^\s*FLIGHT\s+(?!BOOKING|TICKET)", re.I)
_TICKET_HD_RE = re.compile(r"^\s*FLIGHT\s+TICKET\(S\)",        re.I)
_OPERATED_RE  = re.compile(r"^\s*OPERATED\s+BY\s*:",           re.I)
_DEP_RE       = re.compile(r"^\s*DEPARTURE\s*:",               re.I)
_ARR_RE       = re.compile(r"^\s*ARRIVAL\s*:",                 re.I)
_SEPARATOR_RE = re.compile(r"^\s*-{5,}")
_DASHES_RE    = re.compile(r"^\s*[-\s]+$")
_TICKET_LN_RE = re.compile(r"^\s*TICKET\s*:",                  re.I)

# Trailing time: "DD MON HH:MM" or "DD MON YYYY"
_TRAIL_TIME_RE = re.compile(
    r'\s+(\d{1,2}\s+[A-Z]{3}\s+(?:\d{2}:\d{2}|\d{4}))\s*$', re.I
)
# FLIGHT line date: "DOW DD MON YYYY"
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
    """Single line at fixed x offset."""
    def __init__(self, text, bold=False, red=False, x=0):
        super().__init__()
        self.text = (text or "").rstrip()
        self.bold = bold or red
        self.red  = red
        self.x    = x
        self.width  = CW
        self.height = LHE if not self.text.strip() else LH

    def draw(self):
        if not self.text.strip():
            return
        self.canv.setFont(FB if self.bold else FN, FS)
        self.canv.setFillColor(RED if self.red else BLACK)
        self.canv.drawString(self.x, 2, self.text)
        self.canv.setFillColor(BLACK)


class FlightLine(Flowable):
    """
    FLIGHT  <info bold>                    <date bold>
    "FLIGHT" at x=0, info at x=COL_INDENT, date at x=COL_DATE (fixed).
    """
    def __init__(self, info, date):
        super().__init__()
        self.info = info.strip()
        self.date = date.strip()
        self.width  = CW
        self.height = LH

    def draw(self):
        self.canv.setFont(FB, FS)
        self.canv.setFillColor(BLACK)
        self.canv.drawString(0, 2, "FLIGHT")
        self.canv.drawString(COL_INDENT, 2, self.info)
        self.canv.drawString(COL_DATE, 2, self.date)


class DepArrLine(Flowable):
    """
    DEPARTURE: <location bold>             <time bold>
    label at x=0, location at x=COL_INDENT, time at x=COL_TIME (fixed col).
    """
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
        self.canv.drawString(0, 2, self.label)
        self.canv.drawString(COL_INDENT, 2, self.location)
        if self.time_part:
            self.canv.drawString(COL_TIME, 2, self.time_part)


class SepLine(Flowable):
    """Full-width dashes separator."""
    def __init__(self):
        super().__init__()
        self.width  = CW
        self.height = LH

    def draw(self):
        self.canv.setFont(FN, FS)
        self.canv.setFillColor(BLACK)
        n = int(CW / (FS * 0.6))
        self.canv.drawString(0, 2, "-" * n)


class DottedSep(Flowable):
    """Dotted separator - - - - -"""
    def __init__(self, text):
        super().__init__()
        self.text   = text.strip()
        self.width  = CW
        self.height = LH

    def draw(self):
        self.canv.setFont(FN, FS)
        self.canv.setFillColor(BLACK)
        self.canv.drawString(0, 2, self.text)


class TicketHeaderLine(Flowable):
    """FLIGHT TICKET(S) ------- red bold."""
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
# Extract & process
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


def parse_header(lines, verbose=False):
    kept = []
    for line in lines:
        s = line.strip()
        if not s:
            continue
        if re.match(r'^BOOKING\s+REF\s*:', s, re.I):
            val = re.sub(r'(BOOKING\s+REF\s*:\s*)[A-Za-z]{2}/', r'\1', s, flags=re.I)
            kept.append(val)
            continue
        if re.match(r'^[A-Z][A-Z-]+/[A-Z]', s):
            kept.append(s)
            continue
        # Two-column layout
        m = re.match(r'^\s+', line)
        if not m:
            continue
        rest = line[m.end():]
        mg = re.search(r'\s{4,}', rest)
        if not mg:
            continue
        right = rest[mg.end():].strip()
        if not right or re.match(r'^DATE\s*:', right, re.I):
            continue
        if re.match(r'^BOOKING\s+REF\s*:', right, re.I):
            right = re.sub(r'(BOOKING\s+REF\s*:\s*)[A-Za-z]{2}/', r'\1', right, flags=re.I)
            kept.append(right)
        elif re.match(r'^[A-Z-]+/[A-Z]', right):
            kept.append(right)
    if verbose:
        print(f"  [header] {kept}")
    return kept


def clean_body(lines, verbose=False):
    result, skip = [], False
    for line in lines:
        s = line.strip()
        if re.match(r"Data\s+Protection\s+Notice\s*:", s, re.I):
            skip = True
        if skip:
            continue
        if _del(line):
            if verbose:
                print(f"  [del] {repr(s[:70])}")
            continue
        result.append(line)
    while result and not result[-1].strip():
        result.pop()
    return result


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


def group_blocks(body_lines):
    blocks, cur = [], []
    for line in body_lines:
        s = line.strip()
        if _FLIGHT_RE.match(line) or _TICKET_HD_RE.match(s):
            if cur:
                blocks.append(cur)
            cur = [line]
        else:
            cur.append(line)
    if cur:
        blocks.append(cur)
    return blocks


def _parse_dep_arr(s):
    m = re.match(r'^(DEPARTURE:|ARRIVAL:)\s*', s, re.I)
    if not m:
        return None, s, None
    label = m.group(1).upper()
    rest  = s[m.end():]
    mt    = _TRAIL_TIME_RE.search(rest)
    if mt:
        return label, rest[:mt.start()].strip(), mt.group(1).strip()
    return label, rest.strip(), None


# ══════════════════════════════════════════════════════════════════════════════
# Build block -> Flowables
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

        # TICKET: line
        if _TICKET_LN_RE.match(s):
            elements.append(MonoLine(s, red=True))
            after_dep_arr = False
            continue

        # Solid separator
        if _SEPARATOR_RE.match(raw) and not _DASHES_RE.match(raw):
            elements.append(SepLine())
            after_dep_arr = False
            continue

        # Dotted separator - - -
        if _DASHES_RE.match(s):
            elements.append(DottedSep(s))
            after_dep_arr = False
            continue

        # FLIGHT line
        if _FLIGHT_RE.match(raw):
            m = _FLIGHT_PARSE_RE.match(raw)
            if m:
                elements.append(FlightLine(m.group(1), m.group(2)))
            else:
                elements.append(MonoLine(s, bold=True))
            after_dep_arr = False
            continue

        # OPERATED BY — indented, normal weight
        if _OPERATED_RE.match(s):
            elements.append(MonoLine(s, x=COL_INDENT))
            after_dep_arr = False
            continue

        # DEPARTURE / ARRIVAL
        if _DEP_RE.match(s) or _ARR_RE.match(s):
            label, location, time_str = _parse_dep_arr(s)
            elements.append(DepArrLine(label, location, time_str))
            after_dep_arr = True
            continue

        # Continuation after DEP/ARR (TERMINAL 2, TOM BRADLEY etc) — bold, indented
        if after_dep_arr and not re.match(r'[A-Z]+\s*:', s) and not _FLIGHT_RE.match(raw):
            elements.append(MonoLine(s, bold=True, x=COL_INDENT))
            continue
        else:
            after_dep_arr = False

        # Everything else — normal, indented
        elements.append(MonoLine(s, x=COL_INDENT))

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


def build_pdf(header_lines, body_lines, out_path, logo_path, luuy_path, verbose=False):
    story = []

    # Logo — full content width
    if logo_path and os.path.isfile(logo_path):
        story.append(_img(logo_path, LOGO_W, hAlign='CENTER'))
        story.append(Spacer(1, 12))
    else:
        print(f"  WARN: logo not found: {logo_path}")

    # BOOKING REF bold, blank, passengers bold
    booking = [l for l in header_lines if re.match(r'^BOOKING\s+REF', l, re.I)]
    pax     = [l for l in header_lines if not re.match(r'^BOOKING\s+REF', l, re.I)]
    for l in booking:
        story.append(MonoLine(l, bold=True))
    if pax:
        story.append(Spacer(1, 4))
        for l in pax:
            story.append(MonoLine(l, bold=True))
    story.append(Spacer(1, 10))

    # Blocks
    blocks = group_blocks(body_lines)
    if verbose:
        print(f"  [build] {len(blocks)} blocks")

    for idx, block in enumerate(blocks):
        elems = _block_to_elements(block)
        if idx > 0:
            story.append(Spacer(1, 16))
        if len(elems) <= 45:
            story.append(KeepTogether(elems))
        else:
            mid = len(elems) // 2
            story.append(KeepTogether(elems[:mid]))
            story.append(KeepTogether(elems[mid:]))
        if verbose:
            print(f"  block {idx+1}: {len(block)} lines -> {len(elems)} elems")

    # Luu y
    story.append(Spacer(1, 14))
    if luuy_path and os.path.isfile(luuy_path):
        story.append(_img(luuy_path, CW))
    else:
        print(f"  WARN: luuy not found: {luuy_path}")

    SimpleDocTemplate(
        out_path, pagesize=A4,
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
    args   = parse_args()
    output = args.output_file or args.output or \
             os.path.splitext(args.input)[0] + "_ICAGO.pdf"
    convert(args.input, output, args.logo, args.luuy, args.verbose)
    print(f"Done -> {output} ({os.path.getsize(output)//1024} KB)")


if __name__ == "__main__":
    main()
