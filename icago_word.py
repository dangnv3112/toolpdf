# #!/usr/bin/env python3
“””
icago_word.py — ICAGO Word (.docx) → PDF Converter

Chuyển đổi file lịch trình Word (.docx) sang định dạng ICAGO chuẩn (PDF).

Chức năng:
✅ Bỏ header logo cũ, thay bằng icago_logo.png
✅ Trích xuất FLIGHT BOOKING REF: … đầu tiên → hiện lên đầu trái
✅ Bỏ dòng “Passenger(s):” (giữ tên hành khách)
✅ In đậm: FLIGHT, DEPARTURE, ARRIVAL, FLIGHT TICKET(S)
✅ Tô đỏ + in đậm: FLIGHT TICKET(S) và các dòng TICKET bên dưới
✅ Thêm icago_luuy.png cuối trang
✅ Output có thể đặt tên tùy ý

Cài đặt:
pip install python-docx reportlab pillow

Dùng CLI:
python icago_word.py input.docx
python icago_word.py input.docx OUTPUT.pdf
python icago_word.py input.docx -o OUTPUT.pdf –logo logo.png –luuy luuy.png -v

Dùng import:
from icago_word import convert_docx
convert_docx(“input.docx”, “output.pdf”)
“””

import argparse
import os
import re
import sys

# ── Thư viện ───────────────────────────────────────────────────────────────────

try:
from docx import Document
except ImportError:
sys.exit(“❌ pip install python-docx”)

try:
from reportlab.lib.pagesizes import letter
from reportlab.platypus import (
SimpleDocTemplate, Spacer, Image as RLImage, Flowable, KeepTogether
)
from reportlab.lib import colors
except ImportError:
sys.exit(“❌ pip install reportlab”)

try:
from PIL import Image as PILImage
except ImportError:
sys.exit(“❌ pip install pillow”)

# ── Đường dẫn ảnh mặc định ────────────────────────────────────────────────────

_SCRIPT_DIR  = os.path.dirname(os.path.abspath(**file**))
DEFAULT_LOGO = os.path.join(_SCRIPT_DIR, “icago_logo.png”)
DEFAULT_LUUY = os.path.join(_SCRIPT_DIR, “icago_luuy.png”)

# ── Layout ─────────────────────────────────────────────────────────────────────

PAGE_W, PAGE_H = letter
MARGIN_L  = 50
MARGIN_R  = 50
CONTENT_W = PAGE_W - MARGIN_L - MARGIN_R
FONT_MONO = “Courier”
FONT_BOLD = “Courier-Bold”
FONT_SIZE = 8.5
LINE_H    = 10.5   # tight line height — gần hơn so với 12
EMPTY_H   = 4      # dòng trống nhỏ hơn nhiều so với dòng thường

# ── Regex ──────────────────────────────────────────────────────────────────────

_BOLD_RE = [
re.compile(r”^FLIGHT\s+(?!BOOKING)”,   re.IGNORECASE),
re.compile(r”^DEPARTURE\s*:”,          re.IGNORECASE),
re.compile(r”^ARRIVAL\s*:”,            re.IGNORECASE),
re.compile(r”^FLIGHT\s+TICKET(S)”,   re.IGNORECASE),
# Các dòng giữa FLIGHT và ARRIVAL — in đậm
re.compile(r”^STATUS\s*:”,             re.IGNORECASE),
re.compile(r”^CLASS\s*:”,              re.IGNORECASE),
re.compile(r”^AIRCRAFT\s*:”,           re.IGNORECASE),
re.compile(r”^DURATION\s*:”,           re.IGNORECASE),
re.compile(r”^OPERATED\s+BY\s*:”,      re.IGNORECASE),
re.compile(r”^SEAT\s*:”,               re.IGNORECASE),
re.compile(r”^MEAL\s*:”,               re.IGNORECASE),
re.compile(r”^CABIN\s*:”,              re.IGNORECASE),
re.compile(r”^STOP\s*:”,               re.IGNORECASE),
re.compile(r”^EQUIPMENT\s*:”,          re.IGNORECASE),
re.compile(r”^MARKETING\s+CARRIER\s*:”,re.IGNORECASE),
re.compile(r”^OPERATING\s+CARRIER\s*:”,re.IGNORECASE),
re.compile(r”^TERMINAL\s*:”,           re.IGNORECASE),
re.compile(r”^CHECK-?IN\s*:”,          re.IGNORECASE),
re.compile(r”^BAGGAGE\s*:”,            re.IGNORECASE),
re.compile(r”^FARE\s+BASIS\s*:”,       re.IGNORECASE),
re.compile(r”^NOT\s+VALID\s”,          re.IGNORECASE),
re.compile(r”^FREQUENCY\s*:”,          re.IGNORECASE),
]
_RED_RE = [
re.compile(r”^FLIGHT\s+TICKET(S)”, re.IGNORECASE),
re.compile(r”^TICKET\s*:”,           re.IGNORECASE),
]
_FLIGHT_START_RE = re.compile(r”^FLIGHT\s+(?!BOOKING|TICKET)”, re.IGNORECASE)
_TICKET_START_RE = re.compile(r”^FLIGHT\s+TICKET(S)”,        re.IGNORECASE)
_BOOKING_REF_RE  = re.compile(r”FLIGHT\s+BOOKING\s+REF\s*:\s*(\S+)”, re.IGNORECASE)

# Logo tối đa 35% content width — không quá rộng so với nội dung bên dưới

LOGO_MAX_W = CONTENT_W * 0.35

# ── Junk lines to delete ───────────────────────────────────────────────────────

_DELETE_RE = [re.compile(p, re.IGNORECASE) for p in [
r”Please\s+check\s*[–-]\s*in”,
r”Vui\s+l[oò]ng\s+c[oó]\s+m[aặ]t”,
r”Thank\s+you\s+for\s+purchasing”,
r”FLIGHT(S)\s+CALCULATED\s+AVERAGE\s+CO2”,
r”SOURCE:\s*ICAO\s+CARBON\s+EMISSIONS”,
r”https?://www.icao.int”,
r”HTTPS?://BAGS.AMADEUS.COM”,
r”CHECK\s+YOUR\s+TRIP\s+ONLINE”,
r”CLICK\s+HERE”,
r”Data\s+Protection\s+Notice\s*:”,
r”BAGGAGE\s+POLICY\s*-\s*FOR\s+TRAVEL”,
r”IF\s+YOU\s+ARE\s+DENIED\s+BOARDING”,
r”ENTITLED\s+TO\s+CERTAIN\s+STANDARDS”,
r”PASSENGER\s+RIGHTS\s+PLEASE\s+CONTACT”,
r”CANADIAN\s+TRANSPORTATION”,
]]

def _should_delete(line):
s = line.strip()
return any(rx.search(s) for rx in _DELETE_RE)

# ══════════════════════════════════════════════════════════════════════════════

# 1. Trích xuất văn bản từ DOCX

# ══════════════════════════════════════════════════════════════════════════════

def extract_docx_lines(docx_path, verbose=False):
“””
Đọc toàn bộ paragraphs từ .docx, trả về list[str].
Bỏ qua paragraphs chứa drawing/image (logo cũ).
“””
doc = Document(docx_path)
lines = []
skipped_imgs = 0

```
for para in doc.paragraphs:
    # Kiểm tra nếu paragraph chứa drawing/image → bỏ qua (logo cũ)
    has_drawing = any(
        run._element.find('.//{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}anchor') is not None or
        run._element.find('.//{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}inline') is not None
        for run in para.runs
    )
    if has_drawing:
        skipped_imgs += 1
        if verbose:
            print(f"  [extract] bỏ image paragraph #{skipped_imgs}")
        continue

    text = para.text  # full text of paragraph (all runs merged)
    lines.append(text)

if verbose:
    print(f"  [extract] {len(lines)} dòng, bỏ {skipped_imgs} image paragraph")
return lines
```

# ══════════════════════════════════════════════════════════════════════════════

# 2. Xử lý lines

# ══════════════════════════════════════════════════════════════════════════════

def process_lines(raw_lines, verbose=False):
“””
Trả về (booking_ref: str|None, passenger_lines: list[str], body_lines: list[str])
“””
# Tìm BOOKING REF đầu tiên trong toàn bộ nội dung
booking_ref = None
for line in raw_lines:
m = _BOOKING_REF_RE.search(line)
if m and not booking_ref:
ref = m.group(1)
# Bỏ “xx/” prefix nếu có (vd: “xx/N60HHP” → “N60HHP”)
ref = re.sub(r’^[A-Za-z]{2}/’, ‘’, ref)
booking_ref = ref
if verbose:
print(f”  [process] BOOKING REF: {booking_ref}”)

```
passengers = []
body_lines = []
found_flight = False
skip_rest = False

for line in raw_lines:
    stripped = line.strip()

    # Skip empty after processing
    if skip_rest:
        continue

    # Detect Data Protection → skip rest
    if re.match(r"Data\s+Protection\s+Notice", stripped, re.IGNORECASE):
        skip_rest = True
        continue

    # Detect junk
    if _should_delete(line):
        if verbose:
            print(f"  [process] xóa: {repr(stripped[:80])}")
        continue

    # Detect start of FLIGHT section
    if re.match(r"^FLIGHT\s+", stripped, re.IGNORECASE) and not re.match(r"^FLIGHT\s+BOOKING", stripped, re.IGNORECASE):
        found_flight = True

    # Before first FLIGHT: collect header/passenger info
    if not found_flight:
        # Skip "Passenger(s):" label
        if re.match(r"^Passenger\(s\)\s*:", stripped, re.IGNORECASE):
            if verbose:
                print(f"  [process] bỏ Passenger(s): label")
            continue
        # Skip lines that are purely FLIGHT BOOKING REF (already captured)
        if re.match(r"^\s*FLIGHT\s+BOOKING\s+REF", stripped, re.IGNORECASE):
            continue
        # Collect passenger names (non-empty lines before FLIGHT)
        if stripped:
            # Skip lines that look like agency header (has www., EMAIL:, TELEPHONE:, etc.)
            if re.search(r'www\.|@|TELEPHONE\s*:|EMAIL\s*:|BSP\b', stripped, re.IGNORECASE):
                continue
            if re.match(r'^BOOKING\s+REF\s*:', stripped, re.IGNORECASE):
                # This would be "BOOKING REF: N60HHP" line (not FLIGHT BOOKING REF)
                # In Word docs this is often already a passenger-section line
                passengers.append(stripped)
                continue
            passengers.append(stripped)
        continue

    # Body: collect flight content
    body_lines.append(stripped)

# Remove trailing empty body lines
while body_lines and not body_lines[-1]:
    body_lines.pop()

# Collapse consecutive empty lines → tối đa 1 dòng trống liên tiếp
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
```

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

# 4. Nhóm body thành flight blocks

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
def **init**(self, text, bold=False, red=False, color=None):
super().**init**()
self.text   = text
self.bold   = bold or red
self.color  = colors.red if red else (color or colors.black)
self.width  = CONTENT_W
# Empty lines get smaller height to reduce gaps
self.height = EMPTY_H if not text.strip() else LINE_H

```
def draw(self):
    if not self.text.strip():
        return  # empty line — just takes up vertical space
    self.canv.setFont(FONT_BOLD if self.bold else FONT_MONO, FONT_SIZE)
    self.canv.setFillColor(self.color)
    self.canv.drawString(0, 2, self.text)
    self.canv.setFillColor(colors.black)  # reset
```

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

```
# ── Logo ICAGO ─────────────────────────────────────────────────────────────
if logo_path and os.path.isfile(logo_path):
    story.append(_scaled_image(logo_path, LOGO_MAX_W))  # thu nhỏ logo
    story.append(Spacer(1, 6))
else:
    print(f"  ⚠️  Không tìm thấy logo: {logo_path}")

# ── BOOKING REF + Passengers (header trái) ─────────────────────────────────
# Hiện BOOKING REF nếu tìm được — in đậm
if booking_ref:
    story.append(MonoLine(f"BOOKING REF: {booking_ref}", bold=True))
# Hiện tên hành khách — in đậm
for p in passengers:
    # Bỏ "BOOKING REF:" nếu đã in ở trên
    if re.match(r'^BOOKING\s+REF\s*:', p, re.IGNORECASE) and booking_ref:
        continue
    story.append(MonoLine(p, bold=True))  # in đậm tên khách hàng
story.append(Spacer(1, 5))

# ── Body: nhóm thành flight blocks + KeepTogether ──────────────────────────
blocks = group_into_blocks(body_lines)
if verbose:
    print(f"  [build] {len(blocks)} flight block(s)")

for block_idx, block in enumerate(blocks):
    elements = []
    in_ticket_section = False
    in_flight_section = False   # True từ dòng FLIGHT ... đến hết dòng ARRIVAL:
    past_arrival = False

    for line in block:
        s = line.strip()
        if is_ticket_start(s):
            in_ticket_section = True

        # Khi gặp dòng FLIGHT (không phải TICKET/BOOKING): bắt đầu vùng in đậm
        if is_flight_start(s):
            in_flight_section = True
            past_arrival = False

        line_red  = in_ticket_section or is_red(s)
        # In đậm: ticket section, các label đã định nghĩa, HOẶC mọi dòng
        # trong vùng FLIGHT → ARRIVAL (bao gồm cả dòng ARRIVAL)
        line_bold = (
            is_bold(s) or line_red or in_flight_section
        )

        elements.append(MonoLine(line, bold=line_bold, red=line_red))

        # Sau khi in dòng ARRIVAL: kết thúc vùng in đậm tự động
        if re.match(r'^ARRIVAL\s*:', s, re.IGNORECASE):
            in_flight_section = False
            past_arrival = True

    # KeepTogether — nếu block quá dài thì chia đôi
    if len(elements) <= 40:
        story.append(KeepTogether(elements))
    else:
        mid = len(elements) // 2
        story.append(KeepTogether(elements[:mid]))
        story.append(KeepTogether(elements[mid:]))

    if verbose:
        print(f"    block {block_idx+1}: {len(block)} dòng")

# ── Lưu ý ──────────────────────────────────────────────────────────────────
story.append(Spacer(1, 8))
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
```

# ══════════════════════════════════════════════════════════════════════════════

# 7. Public API

# ══════════════════════════════════════════════════════════════════════════════

def convert_docx(input_path, output_path=None,
logo_path=DEFAULT_LOGO, luuy_path=DEFAULT_LUUY,
verbose=False):
“””
Chuyển đổi file .docx → PDF định dạng ICAGO.

```
Args:
    input_path  : Đường dẫn file .docx đầu vào
    output_path : Đường dẫn file .pdf đầu ra (mặc định: <input>_ICAGO.pdf)
    logo_path   : Logo ICAGO (mặc định: icago_logo.png cạnh script)
    luuy_path   : Ảnh Lưu ý (mặc định: icago_luuy.png cạnh script)
    verbose     : In thông tin debug

Returns:
    str: Đường dẫn file PDF đã tạo
"""
if not os.path.isfile(input_path):
    raise FileNotFoundError(f"Không tìm thấy: {input_path}")

if output_path is None:
    base = os.path.splitext(input_path)[0]
    output_path = base + "_ICAGO.pdf"

raw_lines = extract_docx_lines(input_path, verbose)
booking_ref, passengers, body_lines = process_lines(raw_lines, verbose)
build_pdf(booking_ref, passengers, body_lines, output_path,
          logo_path, luuy_path, verbose)
return output_path
```

# ══════════════════════════════════════════════════════════════════════════════

# 8. CLI

# ══════════════════════════════════════════════════════════════════════════════

def parse_args():
p = argparse.ArgumentParser(
prog=“icago_word”,
description=“Chuyển đổi lịch trình Word (.docx) sang PDF định dạng ICAGO.”,
formatter_class=argparse.RawDescriptionHelpFormatter,
epilog=”””
Ví dụ:
python icago_word.py input.docx
python icago_word.py input.docx LUONG_PHUNG_GIA.pdf
python icago_word.py input.docx -o LUONG_PHUNG_GIA.pdf –logo logo.png –luuy luuy.png
python icago_word.py input.docx -v
“””,
)
p.add_argument(“input”,  help=“File .docx đầu vào”)
p.add_argument(“output”, nargs=”?”, default=None,
help=“File PDF đầu ra (mặc định: <input>_ICAGO.pdf)”)
p.add_argument(”-o”, “–output-file”, dest=“output_file”, default=None, metavar=“PATH”,
help=“File PDF đầu ra (thay thế cho positional ‘output’)”)
p.add_argument(”–logo”, default=DEFAULT_LOGO, metavar=“FILE”,
help=“Ảnh logo ICAGO  (mặc định: icago_logo.png)”)
p.add_argument(”–luuy”, default=DEFAULT_LUUY, metavar=“FILE”,
help=“Ảnh Lưu ý       (mặc định: icago_luuy.png)”)
p.add_argument(”-v”, “–verbose”, action=“store_true”)
return p.parse_args()

def main():
args = parse_args()
output = args.output_file or args.output or   
os.path.splitext(args.input)[0] + “_ICAGO.pdf”

```
print(f"📄 Input  : {args.input}")
print(f"📝 Output : {output}")
print(f"🖼️  Logo   : {args.logo}")
print(f"🖼️  Lưu ý : {args.luuy}")
if args.verbose:
    print("🔍 Verbose ON\n")

result = convert_docx(args.input, output, args.logo, args.luuy, args.verbose)
size_kb = os.path.getsize(result) // 1024
print(f"✅ Hoàn thành! → {result}  ({size_kb} KB)")
```

if **name** == “**main**”:
main()
