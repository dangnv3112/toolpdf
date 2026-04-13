# ICAGO Telegram Bot — Hướng dẫn cài đặt & Deploy

## Cấu trúc thư mục

```
icago_tool/
├── icago_itinerary.py   ← Core PDF converter
├── pnr_screenshot.py    ← PNR → pnrexpert.com → screenshot
├── bot.py               ← Telegram Bot (gộp cả 2 tính năng)
├── icago_logo.png
├── icago_luuy.png
├── requirements.txt
├── README.md
└── README_BOT.md
```

---

## Tính năng bot

| Người dùng gửi | Bot trả về |
|----------------|-----------|
| 📎 File PDF lịch trình | PDF đã format chuẩn ICAGO |
| ✈️ Mã PNR (text) | Ảnh lịch trình từ pnrexpert.com (bỏ dòng CO₂) |

---

## Bước 1 — Tạo bot Telegram

1. Mở Telegram → tìm **@BotFather** → `/newbot`
2. Đặt tên & username (phải kết thúc bằng `bot`)
3. Copy **token** (dạng `123456:ABCdef...`)

---

## Bước 2 — Cài đặt

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

---

## Bước 3 — Chạy local (test)

```bash
# Windows
set BOT_TOKEN=123456:ABCdef...
python bot.py

# macOS / Linux
export BOT_TOKEN=123456:ABCdef...
python bot.py
```

Vào Telegram → tìm bot → thử:
- Gửi `/start`
- Gửi file PDF → nhận PDF ICAGO
- Gửi mã PNR → nhận ảnh lịch trình

---

## Bước 4 — Deploy lên Render (24/7 miễn phí)

### 4.1 Push lên GitHub
```bash
git init && git add . && git commit -m "ICAGO Bot"
git remote add origin https://github.com/YOUR_USER/icago-bot.git
git push -u origin main
```

### 4.2 Tạo Web Service trên render.com

| Mục | Giá trị |
|-----|---------|
| Runtime | Python 3 |
| Build Command | `pip install -r requirements.txt && python -m playwright install chromium && python -m playwright install-deps chromium` |
| Start Command | `gunicorn bot:flask_app` |
| Instance Type | Free |

### 4.3 Environment Variables

| Key | Value |
|-----|-------|
| `BOT_TOKEN` | Token từ BotFather |
| `RENDER_URL` | `https://icago-bot.onrender.com` |

### 4.4 Đăng ký Webhook

Sau khi deploy xong, truy cập URL sau (thay TOKEN và YOUR_APP):
```
https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://<YOUR_APP>.onrender.com/<TOKEN>
```
Thấy `{"ok":true}` → thành công! 🎉

---

## Ví dụ PNR hợp lệ

```
1.NGUYEN/VAN A
3 VN 363 H 10JUL 4*SGNHAN HK1 0700 0900 10JUL E VN/ABC123
4 VN 364 H 20JUL 2*HANSGNHK1 1000 1200 20JUL E VN/ABC123
```

Bot tự nhận dạng PNR nếu text có:
- Dòng `1.HO/TEN`
- Mã chuyến bay kiểu `BA 284`, `UA 7941`
- Ngày kiểu GDS: `10JUL`, `07APR`

---

## Biến môi trường

| Biến | Mặc định | Mô tả |
|------|----------|-------|
| `BOT_TOKEN` | *(bắt buộc)* | Token Telegram |
| `RENDER_URL` | *(trống)* | URL Render — bật webhook |
| `LOGO_PATH` | `icago_logo.png` | Logo ICAGO |
| `LUUY_PATH` | `icago_luuy.png` | Ảnh Lưu ý |
| `MAX_MB` | `20` | Giới hạn upload PDF |
