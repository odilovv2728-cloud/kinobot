import os
import sqlite3
import asyncio
from logging import basicConfig, INFO
from threading import Thread
from flask import Flask
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton, ChatJoinRequest
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

# --- 1. SOZLAMALARNI YUKLASH ---
from dotenv import load_dotenv
load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID_LIST = [int(i) for i in os.getenv("ADMIN_ID", "").split(",") if i.strip().isdigit()]
SUPER_ADMIN_ID = ADMIN_ID_LIST[0] if ADMIN_ID_LIST else 0

# --- 2. RENDER UCHUN FLASK SERVER ---
app = Flask('')

@app.route('/')
def home():
    return "Kino bot zayavka tizimida faol!"

def run():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.start()

# --- 3. MA'LUMOTLAR BAZASI TIZIMI ---
DB_NAME = "movies_pro.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS movies
                      (movie_code TEXT PRIMARY KEY, channel_id TEXT, message_id INTEGER)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS users
                      (user_id INTEGER PRIMARY KEY, status TEXT DEFAULT 'active')''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS admins
                      (admin_id INTEGER PRIMARY KEY)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS channels
                      (channel_id TEXT PRIMARY KEY, invite_link TEXT)''')
    # Zayavka (so'rov) yuborganlarni saqlash uchun yangi jadval
    cursor.execute('''CREATE TABLE IF NOT EXISTS pending_requests
                      (user_id INTEGER, channel_id TEXT, PRIMARY KEY (user_id, channel_id))''')

    for admin_id in ADMIN_ID_LIST:
        cursor.execute("INSERT OR IGNORE INTO admins (admin_id) VALUES (?)", (admin_id,))
    conn.commit()
    conn.close()

def add_user(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    conn.commit()
    conn.close()

def get_user_status(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    conn.close()
    return res[0] if res else "active"

def set_user_status(user_id, status):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO users (user_id, status) VALUES (?, ?)", (user_id, status))
    conn.commit()
    conn.close()

def add_pending_request(user_id, channel_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO pending_requests (user_id, channel_id) VALUES (?, ?)", (user_id, str(channel_id)))
    conn.commit()
    conn.close()

def check_pending_request(user_id, channel_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM pending_requests WHERE user_id = ? AND channel_id = ?", (user_id, str(channel_id)))
    res = cursor.fetchone()
    conn.close()
    return res is not None

def is_admin(user_id):
    if user_id == SUPER_ADMIN_ID: return True
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT admin_id FROM admins WHERE admin_id = ?", (user_id,))
    res = cursor.fetchone()
    conn.close()
    return res is not None

def get_db_stats():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM users WHERE status='banned'")
    banned_users = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM movies")
    total_movies = cursor.fetchone()[0]
    conn.close()
    return total_users, banned_users, total_movies

# --- 4. BOT TUZILMASI VA INTERFEYS ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

class AdminStates(StatesGroup):
    waiting_for_code = State()
    waiting_for_forward = State()
    waiting_for_broadcast = State()
    waiting_for_single_user = State()
    waiting_for_single_msg = State()
    waiting_for_channel_id = State()
    waiting_for_channel_link = State()
    waiting_for_new_admin = State()
    waiting_for_ban_user = State()

def get_admin_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Yangi kino qo'shish"), KeyboardButton(text="📊 Statistika")],
            [KeyboardButton(text="📢 Barchaga xabar yuborish"), KeyboardButton(text="👤 Bir kishiga xabar")],
            [KeyboardButton(text="📣 Majburiy obuna sozlash"), KeyboardButton(text="➕ Admin qo'shish")],
            [KeyboardButton(text="🚫 Foydalanuvchini banlash"), KeyboardButton(text="🚪 Panelni yopish")]
        ],
        resize_keyboard=True
    )

# --- 5. MAJBURIY OBUNA VA ZAYAVKANI TEKSHIRISH ---
async def check_subscription(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT channel_id, invite_link FROM channels")
    channels = cursor.fetchall()
    conn.close()

    not_subscribed = []
    for ch_id, link in channels:
        # 1. Agar foydalanuvchi allaqachon zayavka yuborgan bo'lsa, o'tkazamiz
        if check_pending_request(user_id, ch_id):
            continue

        # 2. Agar zayavka yubormagan bo'lsa, kanaldagi a'zoligini tekshiramiz
        try:
            member = await bot.get_chat_member(chat_id=ch_id, user_id=user_id)
            if member.status in ['left', 'kicked']:
                not_subscribed.append(link)
        except Exception:
            # Agar tekshirishda xatolik bo'lsa havola ro'yxatga qo'shiladi
            not_subscribed.append(link)

    return not_subscribed

# --- 6. ZAYAVKA (JOIN REQUEST) USHLOVCHI FUNKSIYA ---
@dp.chat_join_request()
async def handle_join_request(update: ChatJoinRequest):
    # Foydalanuvchi kanalga zayavka tashlashi bilan uni bazada tasdiqlaymiz
    add_pending_request(update.from_user.id, update.chat.id)
    try:
        # Xohlasangiz admin tasdiqlashini kutmasdan botning o'zi ham zayavkani avtomatik qabul qilib yuborishi mumkin:
        # await update.approve()
        await bot.send_message(
            chat_id=update.from_user.id,
            text="✅ Kanalga qo'shilish so'rovingiz qabul qilindi! Endi botdan to'liq foydalanishingiz mumkin."
        )
    except Exception:
        pass

# --- 7. BOT BUYRUQLARI ---

@dp.message(CommandStart())
async def start_cmd(message: Message):
    user_id = message.from_user.id
    add_user(user_id)

    if get_user_status(user_id) == "banned":
        await message.answer("❌ Kechirasiz, siz ushbu botdan foydalanishdan chetlashtirilgansiz (Banned).")
        return

    if is_admin(user_id):
        await message.answer("👋 Professional boshqaruv paneliga xush kelibsiz, Admin!", reply_markup=get_admin_keyboard())
    else:
        unsubscribed = await check_subscription(user_id)
        if unsubscribed:
            ikb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔗 Kanalga a'zo bo'lish (Zayavka)", url=link)] for link in unsubscribed
            ] + [[InlineKeyboardButton(text="✅ Obunani tekshirish", callback_data="check_sub")]])
            await message.answer("⚠️ Botdan foydalanish uchun quyidagi kanallarga a'zo bo'lish so'rovingizni yuborishingiz shart:", reply_markup=ikb)
            return

        await message.answer("👋 Kino qidiruv botiga xush kelibsiz!\n\n🎬 Kinoni olish uchun uning kodini yuboring.")


@dp.callback_query(F.data == "check_sub")
async def check_sub_cb(callback: Message):
    user_id = callback.from_user.id
    unsubscribed = await check_subscription(user_id)
    if unsubscribed:
        await callback.answer("❌ Hali kanalga so'rov yubormadingiz yoki a'zo bo'lmadingiz!", show_alert=True)
    else:
        await callback.message.delete()
        await bot.send_message(chat_id=user_id, text="✅ Rahmat! Obuna tasdiqlandi. Endi kino kodini yuborishingiz mumkin.")

@dp.message(F.text == "🚪 Panelni yopish", F.from_user.id.func(is_admin))
async def close_panel(message: Message):
    await message.answer("Panel yopildi. Foydalanuvchi rejimiga o'tildi.", reply_markup=ReplyKeyboardRemove())

@dp.message(F.text == "📊 Statistika", F.from_user.id.func(is_admin))
async def show_stats(message: Message):
    total, banned, movies = get_db_stats()
    await message.answer(f"📊 Bot Statistikasi:\n\n👤 Jami a'zolar: {total} ta\n🚫 Banlanganlar: {banned} ta\n🎬 Yuklangan kinolar: {movies} ta")

# Kino qo'shish
@dp.message(F.text == "➕ Yangi kino qo'shish", F.from_user.id.func(is_admin))
async def admin_add_movie(message: Message, state: FSMContext):
    await state.set_state(AdminStates.waiting_for_code)
    await message.answer("🔢 Yangi kino uchun kod raqami kiriting:")

@dp.message(AdminStates.waiting_for_code, F.from_user.id.func(is_admin))
async def admin_receive_code(message: Message, state: FSMContext):
    await state.update_data(movie_code=message.text.strip())
    await state.set_state(AdminStates.waiting_for_forward)
    await message.answer("🎬 Kinoni ochilgan maxfiy kanaldan botga Forward (Yo'naltirish) qilib yuboring:")

@dp.message(AdminStates.waiting_for_forward, F.from_user.id.func(is_admin))
async def admin_receive_forward(message: Message, state: FSMContext):
    if not message.forward_from_chat:
        await message.answer("❌ Xatolik! Iltimos, kinoni kanaldan Forward qiling!")
        return

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    data = await state.get_data()
    try:
        cursor.execute(
            "INSERT OR REPLACE INTO movies (movie_code, channel_id, message_id) VALUES (?, ?, ?)",
            (data['movie_code'], str(message.forward_from_chat.id), int(message.forward_from_message_id))
        )
        conn.commit()
        await message.answer(f"✅ Kino {data['movie_code']} kodi bilan saqlandi.", reply_markup=get_admin_keyboard())
    except Exception:
        await message.answer("❌ Xatolik yuz berdi.")
    conn.close()
    await state.clear()


# Barchaga xabar yuborish
@dp.message(F.text == "📢 Barchaga xabar yuborish", F.from_user.id.func(is_admin))
async def broadcast_msg(message: Message, state: FSMContext):
    await state.set_state(AdminStates.waiting_for_broadcast)
    await message.answer("📝 Barcha foydalanuvchilarga yuboriladigan xabarni kiriting:")


@dp.message(AdminStates.waiting_for_broadcast, F.from_user.id.func(is_admin))
async def send_broadcast(message: Message, state: FSMContext):
    await message.answer("🚀 Xabar yuborish boshlandi...")
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE status='active'")
    users = cursor.fetchall()
    conn.close()
    count = 0
    for user in users:
        try:
            await message.copy_to(chat_id=user[0])
            count += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass
    await message.answer(f"✅ Xabar {count} ta foydalanuvchiga yetkazildi.", reply_markup=get_admin_keyboard())
    await state.clear()


# Bir kishiga xabar yuborish
@dp.message(F.text == "👤 Bir kishiga xabar", F.from_user.id.func(is_admin))
async def single_msg_start(message: Message, state: FSMContext):
    await state.set_state(AdminStates.waiting_for_single_user)
    await message.answer("👤 Foydalanuvchining Telegram ID raqamini yozing:")


@dp.message(AdminStates.waiting_for_single_user, F.from_user.id.func(is_admin))
async def single_msg_user(message: Message, state: FSMContext):
    await state.update_data(target_user=message.text.strip())
    await state.set_state(AdminStates.waiting_for_single_msg)
    await message.answer("📝 Yuboriladigan xabarni kiriting:")


@dp.message(AdminStates.waiting_for_single_msg, F.from_user.id.func(is_admin))
async def single_msg_send(message: Message, state: FSMContext):
    data = await state.get_data()
    try:
        await message.copy_to(chat_id=int(data['target_user']))
        await message.answer("✅ Xabar muvaffaqiyatli yuborildi.", reply_markup=get_admin_keyboard())
    except Exception as e:
        await message.answer(f"❌ Xatolik: {e}", reply_markup=get_admin_keyboard())
    await state.clear()


# Majburiy obuna sozlash
@dp.message(F.text == "📣 Majburiy obuna sozlash", F.from_user.id.func(is_admin))
async def channel_setup(message: Message, state: FSMContext):
    await state.set_state(AdminStates.waiting_for_channel_id)
    await message.answer("🆔 Kanalning ID sini kiriting (masalan: -10012345678):")


@dp.message(AdminStates.waiting_for_channel_id, F.from_user.id.func(is_admin))
async def channel_id_rec(message: Message, state: FSMContext):
    await state.update_data(ch_id=message.text.strip())
    await state.set_state(AdminStates.waiting_for_channel_link)
    await message.answer("🔗 Kanalning zayavka (taklif) havolasini yuboring:")


@dp.message(AdminStates.waiting_for_channel_link, F.from_user.id.func(is_admin))
async def channel_link_rec(message: Message, state: FSMContext):
    data = await state.get_data()
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO channels (channel_id, invite_link) VALUES (?, ?)",
        (data['ch_id'], message.text.strip())
    )
    conn.commit()
    conn.close()
    await message.answer("✅ Zayavka kanali majburiy obunaga qo'shildi!", reply_markup=get_admin_keyboard())
    await state.clear()


# Admin qo'shish
@dp.message(F.text == "➕ Admin qo'shish", F.from_user.id == SUPER_ADMIN_ID)
async def add_admin_start(message: Message, state: FSMContext):
    await state.set_state(AdminStates.waiting_for_new_admin)
    await message.answer("👤 Yangi admin Telegram ID raqamini yozing:")


@dp.message(AdminStates.waiting_for_new_admin, F.from_user.id == SUPER_ADMIN_ID)
async def add_admin_finish(message: Message, state: FSMContext):
    new_admin = message.text.strip()
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO admins (admin_id) VALUES (?)", (int(new_admin),))
        conn.commit()
        conn.close()
        await message.answer(f"✅ Foydalanuvchi {new_admin} admin qilindi.", reply_markup=get_admin_keyboard())
    except Exception:
        await message.answer("❌ Xato ID.")
    await state.clear()


# Banlash
@dp.message(F.text == "🚫 Foydalanuvchini banlash", F.from_user.id.func(is_admin))
async def ban_user_start(message: Message, state: FSMContext):
    await state.set_state(AdminStates.waiting_for_ban_user)
    await message.answer("🚫 Ban qilmoqchi bo'lgan foydalanuvchi ID sini yozing:")


@dp.message(AdminStates.waiting_for_ban_user, F.from_user.id.func(is_admin))
async def ban_user_finish(message: Message, state: FSMContext):
    target = int(message.text.strip())
    if target == SUPER_ADMIN_ID:
        await message.answer("❌ Super Adminni ban qilib bo'lmaydi!", reply_markup=get_admin_keyboard())
        await state.clear()
        return
    set_user_status(target, "banned")
    await message.answer(f"🚫 Foydalanuvchi {target} banlandi.", reply_markup=get_admin_keyboard())
    await state.clear()


# --- FOYDALANUVCHILAR UCHUN KINO QIDIRUV ---
@dp.message(F.text)
async def search_movie(message: Message):
    user_id = message.from_user.id
    if get_user_status(user_id) == "banned":
        return
    unsubscribed = await check_subscription(user_id)
    if unsubscribed:
        ikb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔗 Kanalga a'zo bo'lish", url=link)] for link in unsubscribed
        ] + [[InlineKeyboardButton(text="✅ Obunani tekshirish", callback_data="check_sub")]])
        await message.answer("⚠️ Botdan foydalanish uchun kanallarga a'zo bo'lish so'rovini yuborishingiz shart:", reply_markup=ikb)
        return
    code = message.text.strip()
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT channel_id, message_id FROM movies WHERE movie_code = ?", (code,))
    movie_data = cursor.fetchone()
    conn.close()
    if movie_data:
        channel_id, message_id = movie_data
        await message.answer("🔍 Kino topildi! Yo'naltirilmoqda...")
        try:
            await bot.forward_message(chat_id=message.chat.id, from_chat_id=int(channel_id), message_id=int(message_id))
        except Exception:
            await message.answer("❌ Kinoni uzatishda xatolik. Bot kanalda admin ekanini tekshiring.")
    else:
        await message.answer("⚠️ Kechirasiz, bu kod bilan hech qanday kino topilmadi.")


# --- BOTNI ISHGA TUSHIRISH ---
async def main():
    basicConfig(level=INFO)
    init_db()
    keep_alive()
    print("Kino bot professional versiyasi ishga tushdi...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())