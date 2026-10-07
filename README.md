# Kino Bot

Telegram orqali kino kodlari bilan kodiqiy video va fayllarni qayta oluvchi bot.

## Xususiyatlar

- Admin paneli orqali kino kodri va video fayli qo'shish
- SQLite bazasida kino kodlarini saqlash
- Foydalanuvchilar uchun kod bo‘yicha qidiruv
- Telegram Bot API va Flask asosida ishlaydigan bot

## Ishlatish

1. `.env` faylini yaratib, quyidagi o'zgaruvchilarni kiriting:
   - `TELEGRAM_BOT_TOKEN` — Telegram bot tokeni
   - `ADMIN_ID` — admin foydalanuvchining Telegram ID-si
   - `PORT` — qo‘lga tushish porti (masalan: `8080`)
2. `python -m pip install -r requirements.txt` buyrug‘ini bajaring.
3. `python main.py` buyrug‘ini bajaring.

## GitHub deploy

Botni Render yoki boshqa Python hosting tizimi orqali deploy qilishingiz mumkin. Botning `PORT` o'zgaruvchisi undan xohlagan port foydalanadi.

> `.env` fayli GitHubga qo‘yilmasin. GitHub deploy vaqtida tizim uning o'zgaruvchilarini takin qo‘yadi.
