#!/usr/bin/env python3
"""
icago_itinerary.py — ICAGO Itinerary Converter v3
- Logo full content width
- Bold chi: FLIGHT line, DEPARTURE line, ARRIVAL line (moi cum), BOOKING REF + ten khach
- DEPARTURE/ARRIVAL: canh trai thong tin chuyen bay, thoi gian canh PHAI
- Cac dong con lai: normal weight
- FLIGHT TICKET(S) + TICKET: do dam
"""

import argparse
import os
import re
import sys

try:
    import pdfplumber
except ImportError:
    sys.exit("pip install pdfplumber")

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        SimpleDocTemplate, Spacer, Image as RLImage,
        Flowable, KeepTogether, Table, TableStyle
    )
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
except ImportError:
    sys.exit("pip install reportlab")

try:
    from PIL import Image as PILImage
except ImportError:
    sys.exit("pip install pillow")

# ── Paths ──────────────────────────────────────────────────────────────────────
_SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
DEFAULT_LOGO = os.path.join(_SCRIPT_DIR, "icago_logo.png")
DEFAULT_LUUY = os.path.join(_SCRIPT_DIR, "icago_luuy.png")

# ── Layout ─────────────────────────────────────────────────────────────────────
PAGE_W, PAGE_H = letter
MARGIN_L  = 46
MARGIN_R  = 46
MARGIN_T  = 28
MARGIN_B  = 28
CONTENT_W = PAGE_W - MARGIN_L - MARGIN_R   # ~523 pt

FONT_NORM = "Courier"
FONT_BOLD = "Courier-Bold"
FS        = 8.5          # font size
LH        = 11.5         # line height
LH_EMPTY  = 4            # height of blank separator lines
COLOR_BLACK = colors.black
COLOR_RED   = colors.HexColor("#CC0000")

# Approx char width for Courier at FS (= FS * 0.6)
CHAR_W = FS * 0.6        # ~5.1 pt per char

# TIME column width — fits "DD MMM HH:MM" = 12 chars + small pad
TIME_COL_W = 13 * CHAR_W   # ~66 pt

# ── Regexes ────────────────────────────────────────────────────────────────────
_FLIGHT_START_RE  = re.compile(r"^\s*FLIGHT\s+(?!BOOKING|TICKET)", re.I)
_TICKET_START_RE  = re.compile(r"^\s*FLIGHT\s+TICKET\(S\)",        re.I)
_DEPARTURE_RE     = re.compile(r"^\s*DEPARTURE\s*:", re.I)
_ARRIVAL_RE       = re.compile(r"^\s*ARRIVAL\s*:",   re.I)
_BOOKING_REF_RE   = re.compile(r"FLIGHT\s+BOOKING\s+REF\s*:\s*(\S+)", re.I)

# Lines to BOLD (only these + FLIGHT line + DEPARTURE + ARRIVAL)
_BOLD_LABELS = re.compile(
    r"^\s*(FLIGHT\s+(?!BOOKING)|DEPARTURE\s*:|ARRIVAL\s*:)",
    re.I
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
    r"RESERVATION\s+CONFIRMED",      # redundant — status shown on ticket
    r"FLIGHT\s+BOOKING\s+REF\s*:",   # shown in header already
]]


def _should_delete(line):
    return any(rx.search(line.strip()) for rx in _DELETE_RE)


# ══════════════════════════════════════════════════════════════════════════════
# 1. Extract text from PDF
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
# 2. Parse header (page 1 top section)
# ══════════════════════════════════════════════════════════════════════════════

def parse_header(header_lines, verbose=False):
    """
    Extract BOOKING REF and passenger names.
    Handles two formats:
      A) "    BOOKING REF: EMN2LW" — direct (no right-column gap)
      B) "   left col    BOOKING REF: xxx" — two-column with 4+ space gap
    """
    kept = []
    for line in header_lines:
        stripped = line.rstrip()
        s = stripped.strip()
        if not s:
            continue

        # Format A: line IS the booking ref or passenger name
        if re.match(r'^BOOKING\s+REF\s*:', s, re.I):
            val = re.sub(r'(BOOKING\s+REF\s*:\s*)[A-Za-z]{2}/', r'\1', s, flags=re.I)
            kept.append(val)
            if verbose:
                print(f"  [header] ref(A): {repr(val)}")
            continue
        if re.match(r'^[A-Z][A-Z-]+/[A-Z]', s) and '  ' not in s[:30]:
            kept.append(s)
            if verbose:
                print(f"  [header] pax(A): {repr(s)}")
            continue

        # Format B: two-column layout — find content after 4+ space gap
        m_indent = re.match(r'^\s+', stripped)
        content_start = m_indent.end() if m_indent else 0
        rest = stripped[content_start:]
        m_gap = re.search(r'\s{4,}', rest)
        if not m_gap:
            continue
        right = rest[m_gap.end():].strip()
        if not right:
            continue
        if re.match(r'^DATE\s*:', right, re.I):
            continue
        if re.match(r'^BOOKING\s+REF\s*:', right, re.I):
            right = re.sub(r'(BOOKING\s+REF\s*:\s*)[A-Za-z]{2}/', r'\1', right, flags=re.I)
            kept.append(right)
            if verbose:
                print(f"  [header] ref(B): {repr(right)}")
            continue
        if re.match(r'^[A-Z-]+/[A-Z]', right):
            kept.append(right)
            if verbose:
                print(f"  [header] pax(B): {repr(right)}")
    return kept


# ══════════════════════════════════════════════════════════════════════════════
# 3. Clean body lines
# ══════════════════════════════════════════════════════════════════════════════

def clean_body(lines, verbose=False):
    result, skip_rest = [], False
    for line in lines:
        s = line.strip()
        if re.match(r"Data\s+Protection\s+Notice\s*:", s, re.I):
            skip_rest = True
        if skip_rest:
            continue
        if _should_delete(line):
            if verbose:
                print(f"  [body] del: {repr(s[:70])}")
            continue
        result.append(line)
    while result and not result[-1].strip():
        result.pop()
    return result


# ══════════════════════════════════════════════════════════════════════════════
# 4. Process all pages
# ══════════════════════════════════════════════════════════════════════════════

def process_pages(pages, verbose=False):
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
        else:
            page_body = clean_body(lines, verbose)

        all_body_lines.extend(page_body)

    return header_lines, all_body_lines


# ══════════════════════════════════════════════════════════════════════════════
# 5. Line classification
# ══════════════════════════════════════════════════════════════════════════════

def is_flight_start(line):
    return bool(_FLIGHT_START_RE.match(line))

def is_ticket_start(line):
    return bool(_TICKET_START_RE.match(line))

def is_departure(line):
    return bool(_DEPARTURE_RE.match(line.strip()))

def is_arrival(line):
    return bool(_ARRIVAL_RE.match(line.strip()))

def is_ticket_line(line):
    s = line.strip()
    return (re.match(r"FLIGHT\s+TICKET\(S\)", s, re.I) or
            re.match(r"TICKET\s*:", s, re.I) or
            re.match(r"[A-Z]{2}/ETKT\s", s, re.I))


# ══════════════════════════════════════════════════════════════════════════════
# 6. Group into flight blocks
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
# 7. Flowables
# ══════════════════════════════════════════════════════════════════════════════

class MonoLine(Flowable):
    """Single line, monospace, with optional bold/red."""
    def __init__(self, text, bold=False, red=False):
        super().__init__()
        self.text   = text.rstrip()
        self.bold   = bold or red
        self.red    = red
        self.width  = CONTENT_W
        self.height = LH_EMPTY if not self.text.strip() else LH

    def draw(self):
        if not self.text.strip():
            return
        font  = FONT_BOLD if self.bold else FONT_NORM
        color = COLOR_RED if self.red else COLOR_BLACK
        self.canv.setFont(font, FS)
        self.canv.setFillColor(color)
        self.canv.drawString(0, 2, self.text)
        self.canv.setFillColor(COLOR_BLACK)


class DepArrLine(Flowable):
    """
    DEPARTURE / ARRIVAL line: label+location flush left, date+time flush right.
    Bold, black.

    Raw line looks like (spaces preserved from pdfplumber layout=True):
       " DEPARTURE: NEWARK, NJ (NEWARK LIBERTY INTL), TERMINAL C 03 JUN 10:30"
       " ARRIVAL:   TOKYO, JP (TOKYO INTL HANEDA), TERMINAL 3     04 JUN 13:35"

    We split on the trailing datetime pattern: "DD MMM HH:MM" or "DD MON YYYY"
    """
    _TIME_RE = re.compile(
        r'(\s+(\d{1,2}\s+[A-Z]{3}\s+\d{2}:\d{2}|\d{1,2}\s+[A-Z]{3}\s+\d{4}))\s*$',
        re.I
    )

    def __init__(self, text, continuation=False):
        """
        continuation=True: this is an indented continuation line (e.g. TERMINAL 2),
        rendered as normal (non-bold) plain line indented to match body.
        """
        super().__init__()
        self.raw          = text.rstrip()
        self.continuation = continuation
        self.width        = CONTENT_W
        self.height       = LH

    def _split_time(self, s):
        """Return (left_part, time_part) or (s, None)."""
        m = self._TIME_RE.search(s)
        if m:
            return s[:m.start()].strip(), m.group(2) if m.group(2) else m.group(1).strip()
        return s.strip(), None

    def draw(self):
        s = self.raw.strip()
        if self.continuation:
            # Indented continuation — normal weight, small indent
            self.canv.setFont(FONT_NORM, FS)
            self.canv.setFillColor(COLOR_BLACK)
            self.canv.drawString(12, 2, s)
            return

        left, time_part = self._split_time(s)
        self.canv.setFont(FONT_BOLD, FS)
        self.canv.setFillColor(COLOR_BLACK)
        self.canv.drawString(0, 2, left)
        if time_part:
            tw = self.canv.stringWidth(time_part, FONT_BOLD, FS)
            self.canv.drawString(CONTENT_W - tw, 2, time_part)


# ══════════════════════════════════════════════════════════════════════════════
# 8. Build one block into Flowable list
# ══════════════════════════════════════════════════════════════════════════════

def _build_block_elements(block):
    """
    Render rules:
    - FLIGHT line: BOLD
    - DEPARTURE line: DepArrLine (bold, time right-aligned)
    - ARRIVAL line: DepArrLine (bold, time right-aligned)
    - Lines immediately after DEPARTURE/ARRIVAL that have no label (continuation): DepArrLine(continuation=True)
    - OPERATED BY: normal
    - dashes (---) : normal
    - RESERVATION CONFIRMED, BAGGAGE, MEAL, EQUIPMENT, NON STOP, etc.: normal
    - FLIGHT BOOKING REF inside block: normal (drop it — redundant)
    - FLIGHT TICKET(S), TICKET:: red bold
    - Everything else: normal
    """
    elements = []
    after_dep_arr = False   # True for 1 line after DEPARTURE/ARRIVAL (catch continuation)

    for raw in block:
        s = raw.strip()

        # Blank / separator line
        if not s or re.match(r'^[-\s]+$', s):
            elements.append(MonoLine(raw, bold=False))
            after_dep_arr = False
            continue

        # FLIGHT TICKET(S) or TICKET: — red
        if is_ticket_line(s):
            elements.append(MonoLine(s, red=True))
            after_dep_arr = False
            continue

        # DEPARTURE / ARRIVAL — bold, time right-aligned
        if is_departure(s) or is_arrival(s):
            elements.append(DepArrLine(raw, continuation=False))
            after_dep_arr = True
            continue

        # Continuation line after DEPARTURE/ARRIVAL (e.g. "TERMINAL 2")
        if after_dep_arr and not re.match(r'[A-Z ]+\s*:', s, re.I) and not is_flight_start(raw):
            elements.append(DepArrLine(raw, continuation=True))
            # Keep after_dep_arr True in case of multi-line continuation
            continue
        else:
            after_dep_arr = False

        # FLIGHT line (not BOOKING, not TICKET) — bold
        if is_flight_start(raw):
            elements.append(MonoLine(s, bold=True))
            continue

        # Everything else — normal
        elements.append(MonoLine(s, bold=False))

    return elements


# ══════════════════════════════════════════════════════════════════════════════
# 9. Build full PDF
# ══════════════════════════════════════════════════════════════════════════════

def _scaled_image(path, width):
    img = PILImage.open(path)
    w, h = img.size
    return RLImage(path, width=width, height=width * h / w)


def build_pdf(header_lines, body_lines, output_path, logo_path, luuy_path, verbose=False):
    story = []

    # ── Logo: full content width ──────────────────────────────────────────────
    if logo_path and os.path.isfile(logo_path):
        story.append(_scaled_image(logo_path, CONTENT_W))
        story.append(Spacer(1, 8))
    else:
        print(f"  WARNING: logo not found: {logo_path}")

    # ── Header: BOOKING REF (bold) + passenger names (bold) ──────────────────
    for line in header_lines:
        story.append(MonoLine(line, bold=True))
    story.append(Spacer(1, 7))

    # ── Body blocks ───────────────────────────────────────────────────────────
    blocks = group_into_blocks(body_lines)
    if verbose:
        print(f"  [build] {len(blocks)} block(s)")

    for block_idx, block in enumerate(blocks):
        elements = _build_block_elements(block)

        # KeepTogether to avoid splitting a flight block across pages
        if len(elements) <= 45:
            story.append(KeepTogether(elements))
        else:
            mid = len(elements) // 2
            story.append(KeepTogether(elements[:mid]))
            story.append(KeepTogether(elements[mid:]))

        if verbose:
            print(f"  block {block_idx+1}: {len(block)} lines")

    # ── Luu y image ───────────────────────────────────────────────────────────
    story.append(Spacer(1, 12))
    if luuy_path and os.path.isfile(luuy_path):
        story.append(_scaled_image(luuy_path, CONTENT_W))
    else:
        print(f"  WARNING: luuy image not found: {luuy_path}")

    SimpleDocTemplate(
        output_path,
        pagesize=letter,
        leftMargin=MARGIN_L,
        rightMargin=MARGIN_R,
        topMargin=MARGIN_T,
        bottomMargin=MARGIN_B,
    ).build(story)


# ══════════════════════════════════════════════════════════════════════════════
# 10. Public API
# ══════════════════════════════════════════════════════════════════════════════

def convert(input_path, output_path, logo_path=DEFAULT_LOGO, luuy_path=DEFAULT_LUUY, verbose=False):
    if not os.path.isfile(input_path):
        raise FileNotFoundError(f"Not found: {input_path}")
    pages = extract_pages(input_path, verbose)
    header, body = process_pages(pages, verbose)
    build_pdf(header, body, output_path, logo_path, luuy_path, verbose)
    return output_path


# ══════════════════════════════════════════════════════════════════════════════
# 11. CLI
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
    print(f"Done: {output} ({os.path.getsize(output)//1024} KB)")


if __name__ == "__main__":
    main()
