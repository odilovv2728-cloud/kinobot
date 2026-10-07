import os
import sqlite3
import asyncio
from logging import basicConfig, INFO
from threading import Thread
from flask import Flask
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from dotenv import load_dotenv

# --- 1. .ENV FAYLIDAGI MAXFIY MA'LUMOTLARNI YUKLASH ---
load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))

# --- 2. RENDER UCHUN FLASK VEB SERVER KODI ---
app = Flask('')

@app.route('/')
def home():
    return "Kino bot muvaffaqiyatli ishlamoqda!"

def run():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.start()

# --- 3. MA'LUMOTLAR BAZASI (SQLITE) STRUKTURASI ---
DB_NAME = "movies.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS movies (
            movie_code TEXT PRIMARY KEY,
            file_id TEXT
        )
    ''')
    conn.commit()
    conn.close()

def add_movie_to_db(code, file_id):
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO movies (movie_code, file_id) VALUES (?, ?)", (code, file_id))
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False

def get_movie_from_db(code):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT file_id FROM movies WHERE movie_code = ?", (code,))
    result = cursor.fetchone()
    conn.close()
    return result if result else None

# --- 4. BOT O'ZGARUVCHILARI VA FSM (HOLATLAR) ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

class AdminStates(StatesGroup):
    waiting_for_code = State()
    waiting_for_video = State()

def get_admin_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="➕ Yangi kino qo'shish")]],
        resize_keyboard=True
    )

# --- 5. BOT LOGIKASI VA KOMANDALAR ---

@dp.message(CommandStart())
async def start_cmd(message: Message):
    if message.from_user.id == ADMIN_ID:
        await message.answer(
            "👋 Salom Admin! Kino bot boshqaruv paneliga xush kelibsiz.\n"
            "Yangi kino qo'shish uchun pastdagi tugmani bosing:",
            reply_markup=get_admin_keyboard()
        )
    else:
        await message.answer(
            "👋 Salom! Kino qidiruv botiga xush kelibsiz!\n\n"
            "🎬 Kinoni yuklab olish uchun uning kodini (masalan: 100) yuboring.",
            reply_markup=ReplyKeyboardRemove()
        )

# --- ADMIN PANEL FUNKSIYALARI ---

@dp.message(F.text == "➕ Yangi kino qo'shish", F.from_user.id == ADMIN_ID)
async def admin_add_movie(message: Message, state: FSMContext):
    await state.set_state(AdminStates.waiting_for_code)
    await message.answer("🔢 Yangi kino uchun kod raqami kiriting (masalan: 105):", reply_markup=ReplyKeyboardRemove())

@dp.message(AdminStates.waiting_for_code, F.from_user.id == ADMIN_ID)
async def admin_receive_code(message: Message, state: FSMContext):
    code = message.text.strip()
    await state.update_data(movie_code=code)
    await state.set_state(AdminStates.waiting_for_video)
    await message.answer(f"🎬 Kod qabul qilindi: {code}.\n\nEndi ushbu kodga biriktiriladigan Kino videosini yuboring:")

# --- ADMIN KINONI KANAL ORQALI SAQLASH FUNKSIYASI ---

@dp.message(AdminStates.waiting_for_video, F.from_user.id == ADMIN_ID)
async def admin_receive_video(message: Message, state: FSMContext):
    # Admin kinoni o'z maxfiy kanalidan forward qilib yuborishi kerak
    if not message.forward_from_chat:
        await message.answer(
            "⚠️ Xatolik! Kinoni to'g'ridan-to'g'ri bu yerga yuklamang (Server o'chib qoladi).\n\n"
            "👉 Avval kinoni o'zingiz ochgan maxfiy kanalga yuklang, keyin o'sha kanaldagi kinoni bu botga Forward (Yo'naltirish) qilib yuboring!"
        )
        return

    # Kanaldagi chat ID va xabar ID sini olamiz
    channel_id = message.forward_from_chat.id
    message_id = message.forward_from_message_id
    
    # Bazada ikkala ma'lumotni qo'shib saqlaymiz (masalan: "-10012345678:45")
    combined_id = f"{channel_id}:{message_id}"

    user_data = await state.get_data()
    movie_code = user_data['movie_code']
    
    if add_movie_to_db(movie_code, combined_id):
        await message.answer(f"✅ Muvaffaqiyatli saqlash!\n🎬 Kino kodi: {movie_code}\n📌 Kino kanaldan ulandi.", reply_markup=get_admin_keyboard())
    else:
        await message.answer("❌ Bazaga saqlashda xatolik yuz berdi.", reply_markup=get_admin_keyboard())
        
    await state.clear()

# --- FOYDALANUVCHILAR UCHUN QIDIRUV (FORWARD USULI) ---

@dp.message(F.text)
async def search_movie(message: Message):
    code = message.text.strip()
    file_id = get_movie_from_db(code)
    
    if file_id:
        await message.answer("🔍 Kino topildi! Yuklanmoqda, iltimos kuting...")
        try:
            # 💡 Kinoni server xotirasiga yuklamasdan, to'g'ridan-to'g'ri forward qilamiz
            # file_id ichida aslida kanal_id va message_id saqlangan bo'ladi (split orqali ajratamiz)
            if ":" in file_id:
                chat_id, msg_id = file_id.split(":")
                await bot.forward_message(
                    chat_id=message.chat.id,
                    from_chat_id=int(chat_id),
                    message_id=int(msg_id)
                )
            else:
                # Agar eski matnli file_id qolib ketgan bo'lsa, document sifatida yuborishga urinadi
                await message.answer_document(document=file_id, caption=f"🎬 Kino kodi: {code}")
        except Exception as e:
            print(f"Yuborishda xatolik: {e}")
            await message.answer("❌ Kinoni yuborishda xatolik yuz berdi. Admin kanalni tekshirishi kerak.")
    else:
        await message.answer("⚠️ Kechirasiz, bu kod bilan hech qanday kino topilmadi.")

# --- ISHGA TUSHIRISH ---
async def main():
    basicConfig(level=INFO)
    init_db()       # SQLite bazani faollashtirish
    keep_alive()    # Render server uchun veb portni yoqish
    print("Kino bot muvaffaqiyatli ishga tushdi...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())