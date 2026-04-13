# ICAGO Telegram Bot — Hướng dẫn cài đặt & Deploy

## Cấu trúc thư mục

```
icago_tool/
├── icago_itinerary.py   ← Core PDF converter
├── pnr_screenshot.py    ← PNR → pnrexpert.com → screenshot
├── bot.py               ← Telegram Bot
├── icago_logo.png
├── icago_luuy.png
├── requirements.txt
└── README_BOT.md
```

---

## Tính năng bot

| Người dùng gửi | Bot trả về |
|----------------|-----------|
| 📎 File PDF lịch trình | PDF chuẩn ICAGO |
| ✈️ Mã PNR (text) | Ảnh lịch trình từ pnrexpert.com (bỏ dòng CO₂) |

---

## Cài đặt & Chạy LOCAL

```bash
pip install -r requirements.txt
python -m playwright install chromium   ← chỉ cần khi chạy local

set BOT_TOKEN=123456:ABCdef...          ← Windows
export BOT_TOKEN=123456:ABCdef...       ← macOS/Linux

python bot.py
```

---

## Deploy lên Render (24/7 miễn phí)

### Bước 1 — Lấy token Telegram
1. Telegram → @BotFather → `/newbot`
2. Copy token `123456:ABCdef...`

### Bước 2 — Đăng ký Browserless.io (FREE)

> ⚠️ Render free tier không cài được Chrome.
> Browserless.io cung cấp Chrome-as-a-Service miễn phí.

1. Vào **https://browserless.io** → Sign up (free)
2. Free plan: **1,000 sessions/tháng** (đủ dùng)
3. Vào Dashboard → copy **API Token**

### Bước 3 — Push code lên GitHub

```bash
git init
git add .
git commit -m "ICAGO Bot"
git remote add origin https://github.com/YOUR_USER/icago-bot.git
git push -u origin main
```

### Bước 4 — Tạo Web Service trên Render

| Mục | Giá trị |
|-----|---------|
| Runtime | Python 3 |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `gunicorn bot:flask_app` |
| Instance Type | Free |

> ✅ KHÔNG cần `playwright install chromium` trên Render!

### Bước 5 — Set Environment Variables

Vào tab **Environment** trên Render, thêm:

| Key | Value | Ghi chú |
|-----|-------|---------|
| `BOT_TOKEN` | `123456:ABCdef...` | Token từ BotFather |
| `RENDER_URL` | `https://icago-bot.onrender.com` | URL Render cấp |
| `BROWSERLESS_TOKEN` | `your_browserless_token` | Token từ browserless.io |

### Bước 6 — Đăng ký Webhook

Sau khi deploy xong, mở URL sau trên trình duyệt:

```
https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://<YOUR_APP>.onrender.com/<TOKEN>
```

Thấy `{"ok":true}` → thành công! 🎉

---

## Biến môi trường đầy đủ

| Biến | Bắt buộc | Mặc định | Mô tả |
|------|----------|----------|-------|
| `BOT_TOKEN` | ✅ | — | Token Telegram |
| `BROWSERLESS_TOKEN` | ✅ (Render) | — | Token browserless.io |
| `RENDER_URL` | Render only | — | URL app trên Render |
| `LOGO_PATH` | | `icago_logo.png` | Đường dẫn logo |
| `LUUY_PATH` | | `icago_luuy.png` | Đường dẫn ảnh Lưu ý |
| `MAX_MB` | | `20` | Giới hạn upload PDF |

---

## So sánh Local vs Render

| | Local | Render + Browserless |
|--|--|--|
| Cài Chrome | Cần (`playwright install chromium`) | Không cần |
| Chi phí | Miễn phí | Miễn phí |
| Uptime 24/7 | Phụ thuộc máy | ✅ |
| PNR sessions | Không giới hạn | 1,000/tháng (free) |
| Setup | Đơn giản | Cần GitHub + Render + Browserless |

---

## Kiến trúc xử lý PNR

```
User gửi PNR text
       │
       ▼
  bot.py nhận → looks_like_pnr() → True
       │
       ▼
  pnr_screenshot.py
       │
       ├── BROWSERLESS_TOKEN có? ──→ Browserless.io (Render)
       │                              Playwright kết nối qua WebSocket
       │
       └── Không? ─────────────→ Playwright local (Chrome máy tính)
       │
       ▼
  pnrexpert.com
  ├── paste PNR vào textarea
  ├── click Quick Convert
  ├── chờ kết quả
  ├── JS: ẩn dòng "Tonnes of Co2"
  ├── screenshot vùng kết quả
  └── PIL: crop Co2 còn sót (double-check)
       │
       ▼
  Bot gửi ảnh lại cho user
```
