# ICAGO Telegram Bot — Hướng dẫn cài đặt & Deploy

## Cấu trúc thư mục

```
icago_tool/
├── icago_itinerary.py   ← Core converter
├── bot.py               ← Telegram Bot
├── icago_logo.png       ← Logo ICAGO
├── icago_luuy.png       ← Ảnh Lưu ý
├── requirements.txt
├── README.md
└── README_BOT.md        ← File này
```

---

## Bước 1 — Tạo bot Telegram

1. Mở Telegram, tìm **@BotFather**
2. Gửi `/newbot`
3. Đặt tên bot (vd: `ICAGO PDF Bot`)
4. Đặt username (vd: `icago_pdf_bot`) — phải kết thúc bằng `bot`
5. Copy **token** vừa nhận được (dạng `123456:ABCdef...`)

---

## Bước 2 — Chạy local (test nhanh)

```bash
# Cài thư viện
pip install -r requirements.txt

# Set token (Windows)
set BOT_TOKEN=123456:ABCdef...

# Set token (macOS/Linux)
export BOT_TOKEN=123456:ABCdef...

# Chạy bot
python bot.py
```

Vào Telegram, tìm bot của bạn → gửi `/start` → gửi file PDF → nhận kết quả.

---

## Bước 3 — Deploy lên Render (miễn phí, chạy 24/7)

### 3.1 Tạo tài khoản Render
Vào https://render.com → Sign up (dùng GitHub)

### 3.2 Push code lên GitHub
```bash
git init
git add .
git commit -m "ICAGO Bot"
git remote add origin https://github.com/YOUR_USERNAME/icago-bot.git
git push -u origin main
```

### 3.3 Tạo Web Service trên Render

| Mục | Giá trị |
|-----|---------|
| Repository | icago-bot |
| Runtime | Python 3 |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `gunicorn bot:flask_app` |
| Instance Type | Free |

### 3.4 Thêm Environment Variables

Vào **Environment** tab, thêm:

| Key | Value |
|-----|-------|
| `BOT_TOKEN` | `123456:ABCdef...` (token từ BotFather) |
| `RENDER_URL` | `https://icago-bot.onrender.com` (URL Render cấp) |

### 3.5 Đăng ký Webhook với Telegram

Sau khi Render deploy xong, mở trình duyệt truy cập URL sau
(thay `TOKEN` và `YOUR_APP` bằng giá trị thực):

```
https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://<YOUR_APP>.onrender.com/<TOKEN>
```

Ví dụ:
```
https://api.telegram.org/bot123456:ABCdef/setWebhook?url=https://icago-bot.onrender.com/123456:ABCdef
```

Nếu thấy `{"ok":true}` → thành công!

### 3.6 Kiểm tra bot

Vào Telegram → tìm bot → gửi `/start` → gửi PDF → nhận kết quả 🎉

---

## Cấu hình nâng cao (tùy chọn)

| Biến môi trường | Mặc định | Mô tả |
|-----------------|----------|-------|
| `BOT_TOKEN` | *(bắt buộc)* | Token từ BotFather |
| `RENDER_URL` | *(trống)* | URL Render — bật webhook mode |
| `LOGO_PATH` | `icago_logo.png` | Đường dẫn logo ICAGO |
| `LUUY_PATH` | `icago_luuy.png` | Đường dẫn ảnh Lưu ý |
| `MAX_MB` | `20` | Giới hạn file upload (MB) |

---

## Chạy local vs Render

| | Local (`python bot.py`) | Render (`gunicorn`) |
|--|--|--|
| Mode | Polling | Webhook |
| Cần internet liên tục | Có | Không |
| Miễn phí | Có | Có (free tier) |
| Uptime 24/7 | Phụ thuộc máy | ✅ |
| Setup | Đơn giản | Cần GitHub + Render |
