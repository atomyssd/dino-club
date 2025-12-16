import asyncio
import logging
import sqlite3
from datetime import datetime
import re
import os
from contextlib import asynccontextmanager

# --- ИМПОРТЫ ДЛЯ WEBHOOK/RENDER ---
from aiohttp import web
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
# ----------------------------------

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

# --- 1. КОНФИГУРАЦИЯ И КОНСТАНТЫ ---

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# !!! ВАЖНО: ЗАМЕНИТЕ ЭТИ ЗНАЧЕНИЯ !!!
# API_TOKEN должен быть получен из ENV на Render
API_TOKEN = os.getenv("BOT_TOKEN", 'ВАШ_ТОКЕН_ДЛЯ_ТЕСТА') 
ADMIN_ID = int(os.getenv("ADMIN_ID", 752078351)) # ВАШ ID
ADMIN_USERNAME = "@Dina_Di_Ru"
CONTACT_PHONES = ["+998972488886", "+998975690286"]
DB_NAME = 'dino_club.db'
LOCATION_COORDS = {'latitude': 40.4979864, 'longitude': 68.7777999}
PHONE_REGEX = re.compile(r'^\+?\d{9,15}$')

# --- КОНСТАНТЫ ДЛЯ WEBHOOK (Render) ---
WEB_SERVER_HOST = "0.0.0.0"
WEB_SERVER_PORT = int(os.environ.get("PORT", 8080))
WEBHOOK_PATH = "/webhook"
BASE_WEBHOOK_URL = os.getenv("RENDER_EXTERNAL_URL")
WEBHOOK_URL = f"{BASE_WEBHOOK_URL}{WEBHOOK_PATH}" if BASE_WEBHOOK_URL else None
# ----------------------------------------

if not API_TOKEN:
    raise ValueError("Переменная окружения BOT_TOKEN не установлена")


# --- 2. БАЗА ДАННЫХ (ОСТАВЛЕНО БЕЗ ИЗМЕНЕНИЙ) ---

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY, full_name TEXT, phone TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS questions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, 
        question_text TEXT, date TEXT)'''
    )
    cursor.execute('''CREATE TABLE IF NOT EXISTS enrollments (
        user_id INTEGER PRIMARY KEY, course_key TEXT, 
        FOREIGN KEY(user_id) REFERENCES users(user_id))'''
    )
    conn.commit()
    conn.close()

def save_user(user_id, name, info):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('INSERT OR REPLACE INTO users VALUES (?, ?, ?)', (user_id, name, info))
    conn.commit()
    conn.close()

def get_user_data(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT 
            u.full_name, 
            u.phone, 
            e.course_key 
        FROM users u 
        LEFT JOIN enrollments e ON u.user_id = e.user_id 
        WHERE u.user_id = ?
    ''', (user_id,))
    data = cursor.fetchone()
    conn.close()
    return data

def save_enrollment(user_id, course_key):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('INSERT OR REPLACE INTO enrollments VALUES (?, ?)', (user_id, course_key))
    conn.commit()
    conn.close()

def save_question(user_id, text):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('INSERT INTO questions (user_id, question_text, date) VALUES (?, ?, ?)',
                   (user_id, text, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()

def get_all_users():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT user_id, full_name, phone FROM users')
    rows = cursor.fetchall()
    conn.close()
    return rows

def get_all_questions():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT id, user_id, question_text, date FROM questions ORDER BY date DESC')
    rows = cursor.fetchall()
    conn.close()
    return rows

def clear_users():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM users')
    cursor.execute('DELETE FROM enrollments')
    conn.commit()
    conn.close()

def clear_questions():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM questions')
    conn.commit()
    conn.close()


# --- 3. НАСТРОЙКА БОТА, ТЕКСТЫ И ПРЕДМЕТЫ (ОСТАВЛЕНО БЕЗ ИЗМЕНЕНИЙ) ---

# Инициализация диспетчера с хранилищем
dp = Dispatcher(storage=MemoryStorage())
bot = Bot(token=API_TOKEN)

# Текстовые константы (без изменений)
STRINGS = {
    'ru': {
        'menu': 'Выберите действие:', 'sub': '📚 Курсы', 'reg': '📞 Регистрация',
        'cab': '👤 Кабинет', 'ask': '❓ Вопрос', 'loc': '📍 Локация', 'res': '🏆 Результаты', 'tst': '📝 Тест',
        'back': '⬅️ Назад', 'cat': 'Направление:', 'fio': 'Введите ФИО:',
        'tel': 'Введите телефон (например: +998901234567):', 'tel_error': '❌ Неверный формат телефона. Пожалуйста, введите корректный номер, например: +998901234567',
        'saved': '✅ Сохранено!', 'select_course': 'Выберите направление для записи:',
        'contact': '📞 Связь',
        'reg_already': 'Я уже учусь в Dino Club', 'reg_new': 'Я еще не учусь, но планирую',
        'reg_prompt': 'Выберите, пожалуйста, ваш статус:',
        'fio_msg_already': 'Введите Ваше полное ФИО, чтобы мы могли найти Ваш профиль и обновить данные:',
        'fio_msg_new': 'Введите Ваше полное ФИО для первичной регистрации:',
        'schedule_header': 'Обзор расписания по курсу:',
        'reg_complete': 'Регистрация завершена! Вы записаны на курс:',
        'reg_data_saved': 'Ваши данные сохранены. Теперь выберите курс.'
    },
    'uzb': {
        'menu': "Harakatni tanlang:", 'sub': "📚 Kurslar", 'reg': "📞 Ro'yxatdan o'tish",
        'cab': "👤 Kabinet", 'ask': "❓ Savol", 'loc': "📍 Manzil", 'res': "🏆 Natijalar", 'tst': "📝 Test",
        'back': "⬅️ Orqaga", 'cat': "Yo’nalish:", 'fio': "F.I.SH. kiriting:",
        'tel': "Telefonni kiriting (masalan: +998901234567):", 'tel_error': "❌ Noto'g'ri telefon formati. Iltimos, to'g'ri raqam kiriting, masalan: +998901234567",
        'saved': "✅ Saqlandi!",
        'loc_text': "📍 Biz bu yerda joylashganmiz (Google Xarita havolasi): [Manzil]",
        'select_course': "Ro'yxatdan o'tish uchun kursni tanlang:",
        'contact': "📞 Kontakt",
        'reg_already': "Men allaqachon Dino Clubda o'qiyman",
        'reg_new': "Men hali o'qimayman, lekin rejalashtirmoqdaman",
        'reg_prompt': "Iltimos, holatingizni tanlang:",
        'fio_msg_already': "Ma'lumotlaringizni yangilash uchun to'liq F.I.SH.ingizni kiriting:",
        'fio_msg_new': "Boshlang'ich ro'yxatdan o'tish uchun to'liq F.I.SH.ingizni kiriting:",
        'schedule_header': "Kurs bo'yicha dars jadvali:",
        'reg_complete': "Ro'yxatdan o'tish yakunlandi! Siz kursga yozildingiz:",
        'reg_data_saved': "Ma'lumotlaringiz saqlandi. Endi kursni tanlang."
    }
}

SUBJECTS = {
    "english": {
        'ru': {'name': "🇬🇧 Английский", 'items': [
            {"n": "Дина Р.", "t": "Дина Рустамовна", "s": "• Общий курс: Пн/Ср/Пт: 09:30, 14:00, 15:30\n• Общий курс: Вт/Чт/Сб: 09:30, 14:00, 15:30\n• Взрослые: Вечернее время (по договору)"},
            {"n": "Алина А.", "t": "Алина Алексеевна", "s": "• 5-7 лет: Пн/Ср/Пт 16:30\n• 2-4 классы: Пн/Ср/Пт 14:00\n• 3-4 классы: Вт/Чт/Сб 09:30"},
            {"n": "IELTS", "t": "Ширин Рустамовна", "s": "• 10-11 классы: Пн/Ср/Пт (время уточняется)"},
            {"n": "Икболой", "t": "Икболой", "s": "• 4-6 классы: Пн, Ср, Пт 09:00"},
            {"n": "Дилафруз Ф.", "t": "Дилафруз Фархадовна", "s": "• 3-4 классы: Вт/Чт/Сб 08:30 и 13:30\n• 5-6 классы: Вт/Чт/Сб 15:00"}
        ]},
        'uzb': {'name': "🇬🇧 Ingliz tili", 'items': [
            {"n": "Dina R.", "t": "Dina Rustamovna", "s": "• Umumiy kurs: Du/Cho/Ju: 09:30, 14:00, 15:30\n• Umumiy kurs: Se/Pay/Sha: 09:30, 14:00, 15:30\n• Katta yoshdagilar: Kechki vaqt (so'rov bo'yicha)"},
            {"n": "Alina A.", "t": "Alina Alekseevna", "s": "• 5-7 yosh: Du/Cho/Ju 16:30\n• 2-4 sinf: Du/Cho/Ju 14:00\n• 3-4 sinf: Se/Pay/Sha 09:30"},
            {"n": "IELTS", "t": "Shirin Rustamovna", "s": "• 10-11 sinf: Du/Cho/Ju (vaqt aniqlanadi)"},
            {"n": "Iqboloy", "t": "Iqboloy", "s": "• 4-6 sinf: Du, Cho, Ju 09:00"},
            {"n": "Dilafruz F.", "t": "Dilafruz Farxadovna", "s": "• 3-4 sinf: Se/Pay/Sha 08:30 va 13:30\n• 5-6 sinf: Se/Pay/Sha 15:00"}
        ]}
    },
    "math": {
        'ru': {'name': "📐 Математика", 'items': [
            {"n": "Юрий С.", "t": "Юрий С.", "s": "• 6-11 классы: Вт, Чт 14:00-16:00\n• 2-5 классы: Ср, Сб 14:00-16:00"}
        ]},
        'uzb': {'name': "📐 Matematika", 'items': [
            {"n": "Yuriy S.", "t": "Yuriy S.", "s": "• 6-11 sinf: Se, Pay 14:00-16:00\n• 2-5 sinf: Cho, Sha 14:00-16:00"}
        ]},
    },
    "russian": {
        'ru': {'name': "🇷🇺 Русский", 'items': [
            {"n": "Зарина А.", "t": "Зарина А.", "s": "• Групповые занятия (Индивидуально): 16:00"}
        ]},
        'uzb': {'name': "🇷🇺 Rus tili", 'items': [
            {"n": "Zarina A.", "t": "Zarina A.", "s": "• Gruppa darslar (Individual): 16:00"}
        ]}
    },
    "pochemuchka": {
        'ru': {'name': "👶 Почемучка", 'items': [
            {"n": "Почемучка", "t": "Алие Ш.", "s": "• Подготовка к школе (русский язык) (5-7 лет): Пн, Ср, Пт 16:30"}
        ]},
        'uzb': {'name': '👶 Pochemuchka', 'items': [
            {"n": "Pochemuchka", "t": "Aliye Sh.", "s": "• Maktabga tayyorlash (Rus Tili) (5-6 yosh): Du, Cho, Ju 16:30"}
        ]}
    },
    "gymnastics": {
        'ru': {'name': "🤸 ГИМНАСТИКА", 'items': [
            {"n": "Уточняется", "t": "Тренер", "s": "• Вт, Чт, Сб: время уточняется"}
        ]},
        'uzb': {'name': "🤸 GIMNASTIKA", 'items': [
            {"n": "Anıqlanadi", "t": "Trener", "s": "• Se, Pay, Sha: vaqti aniqlanadi"}
        ]}
    },
    "choreography": {
        'ru': {'name': "💃 ХОРЕОГРАФИЯ", 'items': [
            {"n": "Уточняется", "t": "Тренер", "s": "• Даты и время уточняются"}
        ]},
        'uzb': {'name': "💃 XOREOGRAFIYA", 'items': [
            {"n": "Anıqlanadi", "t": "Trener", "s": "• Sanalar va vaqtlar aniqlanadi"}
        ]}
    },
}

ENGLISH_TEST_QUESTIONS = [
    ["1. My sister ____ at home now.", ["am", "is", "are", "be"], 1],
    ["2. This is ____ car. We drive it every day.", ["I", "our", "their", "she"], 1],
    ["3. He always ____ his homework after school.", ["do", "doing", "does", "did"], 2],
    ["4. I want to buy ____ umbrella.", ["a", "an", "the", "no article"], 1],
    ["5. They ____ to Paris last year.", ["go", "going", "went", "goes"], 2],
    ["6. I ____ this film three times already.", ["see", "saw", "have seen", "seeing"], 2],
    ["7. You ____ study harder if you want to pass the exam.", ["might", "should", "must", "can"], 1],
    ["8. This book is ____ interesting than the last one.", ["many", "much", "more", "most"], 2],
    ["9. If it ____ tomorrow, we will stay at home.", ["will rain", "rains", "rained", "raining"], 1],
    ["10. The meeting was postponed ____ the manager’s illness.", ["despite", "because", "due to", "although"], 2],
    ["11. She avoids ____ late at night.", ["to drive", "drive", "driving", "drove"], 2],
    ["12. When the phone ____, I was having dinner.", ["rang", "ring", "was ringing", "has rung"], 0],
    ["13. If I had a million dollars, I ____ around the world.", ["will travel", "would travel", "travel", "travelled"], 1],
    ["14. She has lived in London ____ ten years.", ["since", "for", "on", "at"], 1],
    ["15. The new hospital ____ next year.", ["build", "will be built", "is building", "built"], 1],
]

# --- 4. МАШИНА СОСТОЯНИЙ И КЛАВИАТУРА ---

class Form(StatesGroup):
    name = State()
    phone = State()
    select_course = State()
    ask_q = State()
    bc = State()
    test_q = State()
    # НОВОЕ СОСТОЯНИЕ для ответа администратора
    wait_for_admin_answer = State() 


def main_kb(lang):
    s = STRINGS[lang]
    kb = InlineKeyboardBuilder()
    kb.row(types.InlineKeyboardButton(text=s['sub'], callback_data=f"nav_sub_{lang}"))
    kb.row(types.InlineKeyboardButton(text=s['reg'], callback_data=f"nav_reg_{lang}"),
           types.InlineKeyboardButton(text=s['cab'], callback_data=f"nav_cab_{lang}"))
    kb.row(types.InlineKeyboardButton(text=s['loc'], callback_data=f"nav_loc_{lang}"),
           types.InlineKeyboardButton(text=s['res'], callback_data=f"nav_res_{lang}"))
    kb.row(types.InlineKeyboardButton(text=s['tst'], callback_data=f"nav_tst_{lang}"),
           types.InlineKeyboardButton(text=s['ask'], callback_data=f"nav_ask_{lang}"))
    kb.row(types.InlineKeyboardButton(text=s['contact'], callback_data=f"nav_contact_{lang}"))

    return kb.as_markup()

# --- Новые клавиатуры для админ-панели ---

def admin_reply_kb(target_user_id: int):
    """Клавиатура с кнопкой 'Ответить' для уведомления о вопросе."""
    kb = InlineKeyboardBuilder()
    # callback_data содержит ID пользователя, которому нужно ответить
    kb.add(types.InlineKeyboardButton(text="➡️ Ответить", callback_data=f"admin_reply_{target_user_id}"))
    return kb.as_markup()

def admin_cancel_kb():
    """Клавиатура для отмены действия админа."""
    kb = InlineKeyboardBuilder()
    kb.add(types.InlineKeyboardButton(text="❌ Отмена", callback_data="admin_cancel"))
    return kb.as_markup()
    
def admin_main_kb():
    """Основная клавиатура админ-панели."""
    kb = InlineKeyboardBuilder()
    kb.row(types.InlineKeyboardButton(text="👥 Все пользователи", callback_data="admin_users_list"))
    kb.row(types.InlineKeyboardButton(text="❓ Все вопросы", callback_data="admin_questions_list"))
    kb.row(types.InlineKeyboardButton(text="🔄 Главное меню бота", callback_data="lang_ru")) # Возвращение в RU меню
    return kb.as_markup()


# --- 5. ОБРАБОТЧИКИ БОТА (ЛОГИКА) ---

@dp.message(Command("start"))
async def start(m: types.Message):
    kb = InlineKeyboardBuilder()
    kb.add(types.InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang_ru"),
           types.InlineKeyboardButton(text="🇺🇿 O'zbek", callback_data="lang_uzb"))
    await m.answer("Выберите язык / Tilni tanlang:", reply_markup=kb.as_markup())


@dp.callback_query(F.data.startswith("lang_"))
async def set_lang(c: types.CallbackQuery, state: FSMContext):
    await c.answer()
    lang = c.data.split("_")[1]
    
    await state.clear()
    
    try:
        await c.message.edit_text(STRINGS[lang]['menu'], reply_markup=main_kb(lang))
    except TelegramBadRequest:
        await c.message.answer(STRINGS[lang]['menu'], reply_markup=main_kb(lang))


@dp.callback_query(F.data.startswith("nav_"))
async def route(c: types.CallbackQuery, state: FSMContext):
    await c.answer()
    await state.clear()
    _, act, lang = c.data.split("_")
    s = STRINGS[lang]

    # ... (ОБРАБОТЧИКИ NAV_REG, NAV_SUB, NAV_RES, NAV_CONTACT, NAV_CAB, NAV_TST - БЕЗ СУЩЕСТВЕННЫХ ИЗМЕНЕНИЙ) ...
    
    if act == "reg":
        kb = InlineKeyboardBuilder()
        kb.row(types.InlineKeyboardButton(text=s['reg_already'], callback_data=f"reg_type_already_{lang}"))
        kb.row(types.InlineKeyboardButton(text=s['reg_new'], callback_data=f"reg_type_new_{lang}"))
        kb.row(types.InlineKeyboardButton(text=s['back'], callback_data=f"lang_{lang}"))
        try:
            await c.message.edit_text(s['reg_prompt'], reply_markup=kb.as_markup())
        except TelegramBadRequest:
            await c.message.answer(s['reg_prompt'], reply_markup=kb.as_markup())

    elif act == "sub":
        kb = InlineKeyboardBuilder()
        for k in SUBJECTS:
            kb.row(types.InlineKeyboardButton(text=SUBJECTS[k][lang]['name'], callback_data=f"cat_{k}_{lang}"))
        kb.row(types.InlineKeyboardButton(text=s['back'], callback_data=f"lang_{lang}"))
        try:
            await c.message.edit_text(s['cat'], reply_markup=kb.as_markup())
        except TelegramBadRequest:
            await c.message.answer(s['cat'], reply_markup=kb.as_markup())

    elif act == "loc":
        # Отправляем локацию
        try:
            await bot.send_location(c.message.chat.id, 
                                     latitude=LOCATION_COORDS['latitude'], 
                                     longitude=LOCATION_COORDS['longitude'])
        except Exception as e:
            logging.error(f"Failed to send location: {e}")
            
        # ИСПРАВЛЕНИЕ: Корректный URL для Google Maps (long-link)
        maps_url = f"https://www.google.com/maps/search/?api=1&query={LOCATION_COORDS['latitude']},{LOCATION_COORDS['longitude']}"
        text = (
            "📍 **Мы находимся здесь:**\n"
            f"[Открыть в Google Maps]({https://maps.app.goo.gl/iX4zLumXwVS1v58p8})" if lang == 'ru' else
            "📍 **Biz bu yerda joylashganmiz:**\n"
            f"[Google Xaritada ochish]({https://maps.app.goo.gl/iX4zLumXwVS1v58p8})"
        )
        await c.message.answer(text, parse_mode="Markdown", reply_markup=main_kb(lang))
        
    elif act == "ask":
        await state.update_data(l=lang)
        await c.message.answer(
            "❓ Введите ваш анонимный вопрос:" if lang == 'ru' else "❓ Anonim savolingizni kiriting:")
        await state.set_state(Form.ask_q)

    elif act == "res":
        await c.message.answer(
            "🏆 Результаты учеников и достижения: скоро здесь!" if lang == 'ru' else "🏆 O'quvchilar natijalari va yutuqlari: tez orada shu yerda bo'ladi!", reply_markup=main_kb(lang))

    elif act == "tst":
        await state.clear() 
        
        await state.update_data(
            l=lang,
            test_score=0,
            question_index=0,
            test_questions=ENGLISH_TEST_QUESTIONS
        )
        intro_text = (
            "📝 **Начинаем тест на определение уровня английского языка!**\n\n_Выберите один правильный вариант ответа._" if lang == 'ru' else
            "📝 **Ingliz tili darajasini aniqlash testini boshlaymiz!**\n\n_Bitta to'g'ri javobni tanlang._")

        try:
            await c.message.edit_text(intro_text, parse_mode="Markdown")
        except TelegramBadRequest:
            await c.message.answer(intro_text, parse_mode="Markdown")
            
        await ask_test_question(c.message, state)

    elif act == "contact":
        text = (
            "📞 **Связь с администрацией DINO CLUB**\n\n" if lang == 'ru' else
            "📞 **DINO CLUB ma'muriyati bilan bog'lanish**\n\n"
        )
        text += (
            "По всем вопросам записи, расписания и оплаты:\n\n" if lang == 'ru' else
            "Ro'yxatdan o'tish, dars jadvali va to'lov masalalari bo'yicha:\n\n"
        )
        admin_link = f"https://t.me/{ADMIN_USERNAME.strip('@')}"
        text += f"👤 **Telegram:** [{ADMIN_USERNAME}]({admin_link})\n"
        
        for i, phone in enumerate(CONTACT_PHONES, 1):
            # Ссылка tel: используется, чтобы на мобильном телефоне сразу начать звонок
            text += f"📱 **Телефон {i}:** [{phone}](tel:{phone.strip('+')})\n"
            
        text += "\nМы рады вам помочь!" if lang == 'ru' else "\nSizga yordam berishdan mamnunmiz!"
        
        kb = InlineKeyboardBuilder().row(types.InlineKeyboardButton(text=s['back'], callback_data=f"lang_{lang}")).as_markup()
        
        try:
            await c.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)
        except TelegramBadRequest:
             await c.message.answer(text, parse_mode="Markdown", reply_markup=kb)

    elif act == "cab":
        user_data = get_user_data(c.from_user.id)
        
        if not user_data:
            await c.message.answer("❌ Вы еще не зарегистрированы. Нажмите '📞 Регистрация'." if lang == 'ru' else f"❌ Siz hali ro'yxatdan o'tmagansiz. '{s['reg']}' tugmasini bosing.",
                                     reply_markup=main_kb(lang))
            return

        full_name, phone, course_key = user_data
        
        if lang == 'ru':
            text = f"👤 <b>Ваш Личный Кабинет</b>\n\nИмя: {full_name}\nТелефон: {phone}\n"
            button_text = "✏️ Изменить данные/курс"
            not_selected = "❌ Не выбран"
            select_prompt = "Для выбора курса нажмите '✏️ Изменить данные/курс'."

        else:  # uzb
            text = f"👤 <b>Sizning shaxsiy kabinetingiz</b>\n\nIsm: {full_name}\nTelefon: {phone}\n"
            button_text = "✏️ Ma'lumotlarni/kursni o'zgartirish"
            not_selected = "❌ Tanlanmagan"
            select_prompt = "Kursni tanlash uchun '✏️ Ma'lumotlarni/kursni o'zgartirish' tugmasini bosing."
            
        if course_key and course_key in SUBJECTS:
            course_name = SUBJECTS[course_key][lang]['name']
            
            course_text = "Ваш курс:" if lang == 'ru' else "Sizning kursingiz:"
            text += f"\n{course_text} <b>{course_name}</b>\n"
            
            try:
                # Берем расписание первого преподавателя в списке
                schedule = SUBJECTS[course_key][lang]['items'][0]['s']
                
                schedule_header = STRINGS[lang]['schedule_header']
                
                text += f"{schedule_header}\n<pre>{schedule}</pre>"
            except (IndexError, KeyError):
                text += ("Расписание пока не найдено." if lang == 'ru' else "Dars jadvali topilmadi.")
        else:
            course_text = "Ваш курс:" if lang == 'ru' else "Sizning kursingiz:"
            text += f"\n{course_text} {not_selected}\n"
            text += select_prompt

        kb = InlineKeyboardBuilder()
        kb.row(types.InlineKeyboardButton(text=button_text, callback_data=f"nav_reg_{lang}"))
        kb.row(types.InlineKeyboardButton(text=s['back'], callback_data=f"lang_{lang}"))
        
        try:
            await c.message.edit_text(text, parse_mode="HTML", reply_markup=kb.as_markup())
        except TelegramBadRequest:
             await c.message.answer(text, parse_mode="HTML", reply_markup=kb.as_markup())


# ... (ОБРАБОТЧИКИ REG_TYPE, NAME, PHONE, REG_COURSE - БЕЗ СУЩЕСТВЕННЫХ ИЗМЕНЕНИЙ) ...

@dp.callback_query(F.data.startswith("reg_type_"))
async def process_reg_type(c: types.CallbackQuery, state: FSMContext):
    await c.answer()
    
    await state.clear()
    
    _, _, reg_type, lang = c.data.split("_")
    s = STRINGS[lang]

    await state.update_data(l=lang, reg_type=reg_type)

    prompt_text = s['fio_msg_already'] if reg_type == 'already' else s['fio_msg_new']

    try:
        await c.message.edit_text(prompt_text)
    except TelegramBadRequest:
        await c.message.answer(prompt_text)

    await state.set_state(Form.name)


@dp.message(Form.name)
async def get_name(m: types.Message, state: FSMContext):
    data = await state.get_data()
    lang = data['l']
    await state.update_data(n=m.text)

    await m.answer(STRINGS[lang]['tel'])
    await state.set_state(Form.phone)


@dp.message(Form.phone)
async def get_phone(m: types.Message, state: FSMContext):
    data = await state.get_data()
    lang = data['l']
    s = STRINGS[lang]
    
    if not PHONE_REGEX.match(m.text):
        await m.answer(s['tel_error'])
        return

    save_user(m.from_user.id, data['n'], m.text)

    reg_status_ru = "УЖЕ УЧИТСЯ" if data.get('reg_type') == 'already' else "НОВЫЙ КАНДИДАТ"

    # Уведомление администратора
    try:
        await bot.send_message(
            ADMIN_ID,
            f"🔔 НОВЫЙ ВВОД ДАННЫХ ({reg_status_ru}):\n"
            f"ФИО: {data['n']}\n"
            f"Телефон: {m.text}",
            parse_mode="Markdown"
        )
    except (TelegramBadRequest, TelegramForbiddenError) as e:
        logging.error(f"Failed to send admin notification: {e}")

    kb = InlineKeyboardBuilder()
    for k in SUBJECTS:
        kb.row(types.InlineKeyboardButton(text=SUBJECTS[k][lang]['name'], callback_data=f"reg_course_{k}_{lang}"))

    await m.answer(s['reg_data_saved'])
    await m.answer(s['select_course'], reply_markup=kb.as_markup())
    await state.set_state(Form.select_course)


@dp.callback_query(F.data.startswith("reg_course_"), Form.select_course)
async def enroll_course(c: types.CallbackQuery, state: FSMContext):
    await c.answer()
    _, _, course_key, lang = c.data.split("_")
    s = STRINGS[lang]

    save_enrollment(c.from_user.id, course_key)

    course_name = SUBJECTS[course_key][lang]['name']
    
    user_data = get_user_data(c.from_user.id)
    name, phone, _ = user_data if user_data else ("Неизвестно", "Неизвестно", None)

    # Уведомление администратора
    try:
        await bot.send_message(
            ADMIN_ID, 
            f"✅ **КУРС ОБНОВЛЕН/ЗАПИСЬ:**\n"
            f"Пользователь: {name} (ID: {c.from_user.id})\n"
            f"Телефон: {phone}\n"
            f"Курс: **{course_name}**", 
            parse_mode="Markdown")
    except (TelegramBadRequest, TelegramForbiddenError) as e:
        logging.error(f"Failed to send admin notification: {e}")


    reg_complete_text = s['reg_complete']
    text = f"✅ {reg_complete_text} <b>{course_name}</b>."

    try:
        await c.message.edit_text(text, parse_mode="HTML", reply_markup=main_kb(lang))
    except TelegramBadRequest:
        await c.message.answer(text, parse_mode="HTML", reply_markup=main_kb(lang))

    await state.clear()


# --- ОБРАБОТЧИК ВОПРОСА (ДОБАВЛЕНА КНОПКА "ОТВЕТИТЬ") ---

@dp.message(Form.ask_q)
async def process_ask(m: types.Message, state: FSMContext):
    save_question(m.from_user.id, m.text)
    
    user_info = get_user_data(m.from_user.id)
    name = user_info[0] if user_info else "Неизвестный пользователь"

    # 🚨 ИЗМЕНЕНИЕ: Добавлена кнопка "Ответить"
    target_id = m.from_user.id
    
    # Уведомление администратора
    try:
        await bot.send_message(
            ADMIN_ID, 
            f"❓ **НОВЫЙ ВОПРОС (АННОНИМНО):**\n"
            f"От: {name} (ID: `{target_id}`)\n"
            f"Текст: {m.text}", 
            parse_mode="Markdown",
            reply_markup=admin_reply_kb(target_id) # <- Кнопка Ответить
        )
    except (TelegramBadRequest, TelegramForbiddenError) as e:
        logging.error(f"Failed to send admin notification: {e}")
    
    lang = (await state.get_data())['l']
    await m.answer("✅ OK! Ваш вопрос передан администратору." if lang == 'ru' else "✅ OK! Savolingiz administratorga yuborildi.", reply_markup=main_kb(lang))
    await state.clear()


# ... (ОСТАЛЬНЫЕ ОБРАБОТЧИКИ НАВИГАЦИИ БЕЗ ИЗМЕНЕНИЙ) ...

@dp.callback_query(F.data.startswith("cat_"))
async def show_cat(c: types.CallbackQuery):
    await c.answer()
    _, key, lang = c.data.split("_")
    s = STRINGS[lang]
    kb = InlineKeyboardBuilder()

    text_to_edit = SUBJECTS[key][lang]['name']
    
    if SUBJECTS[key][lang]['items']:
        for i, t in enumerate(SUBJECTS[key][lang]['items']):
            kb.row(types.InlineKeyboardButton(
                text=f"👨‍🏫 {t['n']}", 
                callback_data=f"det_{key}_{i}_{lang}"))
    else:
        text_to_edit = (
            f"По направлению {SUBJECTS[key][lang]['name']} пока нет данных. Выберите другой язык или направление." 
            if lang == 'ru' else 
            f"{SUBJECTS[key][lang]['name']} yo'nalishi bo'yicha ma'lumot yo'q. Boshqa yo'nalishni tanlang."
        )

    kb.row(types.InlineKeyboardButton(text=s['back'], callback_data=f"nav_sub_{lang}"))

    try:
        await c.message.edit_text(text_to_edit, reply_markup=kb.as_markup())
    except TelegramBadRequest:
        await c.message.answer(text_to_edit, reply_markup=kb.as_markup())


@dp.callback_query(F.data.startswith("det_"))
async def show_det(c: types.CallbackQuery):
    await c.answer()
    _, key, idx, lang = c.data.split("_")
    it = SUBJECTS[key][lang]['items'][int(idx)]

    if lang == 'ru':
        text = (
            f"📖 <b>{it['n']}</b>\n"
            f"👨‍🏫 Преподаватель: {it['t']}\n"
            f"<b>⏰ Расписание и классы:</b>\n"
            f"<pre>{it['s']}</pre>"
        )
    else:
        text = (
            f"📖 <b>{it['n']}</b>\n"
            f"👨‍🏫 O'qituvchi: {it['t']}\n"
            f"<b>⏰ Dars jadvali va sinflar:</b>\n"
            f"<pre>{it['s']}</pre>"
        )

    kb = InlineKeyboardBuilder()
    kb.row(types.InlineKeyboardButton(text=STRINGS[lang]['back'], callback_data=f"cat_{key}_{lang}"))

    try:
        await c.message.edit_text(text, parse_mode="HTML", reply_markup=kb.as_markup())
    except TelegramBadRequest:
        await c.message.answer(text, parse_mode="HTML", reply_markup=kb.as_markup())


# --- ОБРАБОТЧИКИ ТЕСТА (ОСТАВЛЕНО БЕЗ ИЗМЕНЕНИЙ) ---

async def ask_test_question(message: types.Message, state: FSMContext):
    data = await state.get_data()
    q_index = data['question_index']
    questions = data['test_questions']
    lang = data['l']

    if q_index >= len(questions):
        await finish_test(message, state)
        return

    question_data = questions[q_index]
    question_text = question_data[0]
    options = question_data[1]

    kb = InlineKeyboardBuilder()

    option_names = ['A', 'B', 'C', 'D']
    for i, option in enumerate(options):
        kb.add(types.InlineKeyboardButton(text=f"{option_names[i]}) {option}",
                                         callback_data=f"test_q_{q_index}_{i}"))

    kb.adjust(2)

    await message.answer(
        f"**{('Вопрос' if lang == 'ru' else 'Savol')} {q_index + 1}/{len(questions)}:**\n`{question_text}`",
        reply_markup=kb.as_markup(), parse_mode="Markdown")
    
    await state.set_state(Form.test_q)


@dp.callback_query(F.data.startswith("test_q_"), Form.test_q)
async def process_test_answer(c: types.CallbackQuery, state: FSMContext):
    await c.answer()
    data = await state.get_data()

    parts = c.data.split("_")
    q_index_answered = int(parts[2])
    answer_index = int(parts[3])

    questions = data['test_questions']
    current_score = data['test_score']
    lang = data['l']

    if q_index_answered != data.get('question_index'):
        return

    correct_answer_index = questions[q_index_answered][2]

    try:
        # Редактируем предыдущее сообщение с вопросом
        if answer_index == correct_answer_index:
            current_score += 1
            await c.message.edit_text(c.message.text + (
                f"\n\n✅ **Верно!** (Выбран ответ: {questions[q_index_answered][1][answer_index]})" if lang == 'ru' else 
                f"\n\n✅ **To'g'ri!** (Tanlangan javob: {questions[q_index_answered][1][answer_index]})"), parse_mode="Markdown")
        else:
            await c.message.edit_text(c.message.text + (
                f"\n\n❌ **Неверно.** (Правильный: {questions[q_index_answered][1][correct_answer_index]})" if lang == 'ru' else 
                f"\n\n❌ **Noto'g'ri.** (To'g'ri: {questions[q_index_answered][1][correct_answer_index]})"), parse_mode="Markdown")
    except TelegramBadRequest:
        pass # Игнорируем ошибки редактирования

    next_index = q_index_answered + 1
    await state.update_data(test_score=current_score, question_index=next_index)

    await asyncio.sleep(0.5)

    await ask_test_question(c.message, state)


async def finish_test(message: types.Message, state: FSMContext):
    data = await state.get_data()
    final_score = data['test_score']
    total_questions = len(data['test_questions'])
    lang = data['l']
    s = STRINGS[lang]

    # Логика определения уровня
    if final_score <= 5:
        level = "Beginner/Elementary (A1/A2)"
        recommendation = ("Начните с нашего общего курса для начинающих!" if lang == 'ru' else
                          "Yangi boshlanuvchilar uchun umumiy kursimizdan boshlang!")
    elif final_score <= 10:
        level = "Pre-Intermediate (A2/B1)"
        recommendation = ("У вас есть хорошие базовые знания." if lang == 'ru' else
                          "Unda yaxshi asosiy bilimlar bor.")
    else:
        level = "Intermediate (B1) или выше"
        recommendation = ("Отличный результат!" if lang == 'ru' else
                          "Ajoyib natija!")
    
    header = "Тест завершен!" if lang == 'ru' else "Test yakunlandi!"
    result_label = "Ваш результат:" if lang == 'ru' else "To'g'ri javoblar soni:"
    correct_answers_text = "правильных ответов." if lang == 'ru' else "to'g'ri javob."
    level_label = "Ваш примерный уровень (неточный):" if lang == 'ru' else "Sizning darajangiz (aniq emas):"
    rec_label = "Рекомендация:" if lang == 'ru' else "Tavsiya:"
    footer_text = f"Чтобы записаться, нажмите '📞 {s['reg']}' в главном меню." if lang == 'ru' else f"Ro'yxatdan o'tish uchun bosing '📞 {s['reg']}' asosiy menyuda."
    
    result_text = (
        f"🎉 **{header}**\n"
        f"{result_label} **{final_score} из {total_questions}** {correct_answers_text}\n\n"
        f"📊 **{level_label}** {level}\n"
        f"💡 **{rec_label}** {recommendation}\n\n"
        f"{footer_text}"
    )

    await message.answer(result_text, parse_mode="Markdown")

    await state.clear()
    await message.answer(s['menu'], reply_markup=main_kb(lang))


# --- 6. ОБРАБОТЧИКИ АДМИНА (СИСТЕМА ОТВЕТОВ) ---

@dp.message(Command("admin"))
async def admin_panel(m: types.Message):
    if m.from_user.id != ADMIN_ID:
        return
    
    await m.answer("⚙️ **Админ-панель**\nВыберите действие:", 
                   parse_mode="Markdown",
                   reply_markup=admin_main_kb())


@dp.callback_query(F.data.startswith("admin_reply_"), F.from_user.id == ADMIN_ID)
async def start_admin_reply(c: types.CallbackQuery, state: FSMContext):
    await c.answer("Начало ответа...")
    
    # Извлекаем ID пользователя из callback_data
    try:
        target_user_id = int(c.data.split("_")[-1])
    except ValueError:
        await c.message.answer("❌ Ошибка: Некорректный ID пользователя.")
        return

    await state.update_data(target_id=target_user_id)
    await state.set_state(Form.wait_for_admin_answer)
    
    # Редактируем сообщение, чтобы показать, кому отвечаем, и добавляем Отмену
    try:
        await c.message.edit_text(
            f"✅ Вы отвечаете пользователю с ID: `{target_user_id}`. Введите текст ответа:",
            parse_mode="Markdown",
            reply_markup=admin_cancel_kb()
        )
    except TelegramBadRequest:
        # Если не удалось отредактировать, отправляем новое сообщение
        await c.message.answer(
            f"✅ Вы отвечаете пользователю с ID: `{target_user_id}`. Введите текст ответа:",
            parse_mode="Markdown",
            reply_markup=admin_cancel_kb()
        )


@dp.message(Form.wait_for_admin_answer, F.from_user.id == ADMIN_ID)
async def process_admin_answer(m: types.Message, state: FSMContext):
    data = await state.get_data()
    target_user_id = data.get('target_id')
    
    if not target_user_id:
        await m.answer("❌ Ошибка: Не найден ID пользователя для ответа. Начните заново.")
        await state.clear()
        return

    answer_text = m.text
    
    # Формируем и отправляем ответ целевому пользователю
    user_message = (
        "📩 **Ответ от администрации Dino Club:**\n\n"
        f"***{answer_text}***"
    )

    try:
        await bot.send_message(target_user_id, user_message, parse_mode="Markdown")
        await m.answer(f"✅ Ответ успешно доставлен пользователю с ID: `{target_user_id}`")
    except (TelegramForbiddenError, TelegramBadRequest):
        # TelegramForbiddenError - пользователь заблокировал бота
        await m.answer(f"⚠️ Не удалось доставить ответ пользователю `{target_user_id}` (возможно, он заблокировал бота).")
    
    # Полностью очищаем состояние после отправки
    await state.clear()
    
    # Возвращаем админа в его меню
    await admin_panel(m)


@dp.callback_query(F.data == "admin_cancel", F.from_user.id == ADMIN_ID)
async def admin_cancel(c: types.CallbackQuery, state: FSMContext):
    await c.answer("Отменено")
    await state.clear()
    
    try:
        # Редактируем сообщение, чтобы убрать кнопку "Отмена"
        await c.message.edit_text(c.message.text.split('\n')[0], reply_markup=None)
    except TelegramBadRequest:
        pass

    await c.message.answer("Действие отменено. Вы вернулись в главное меню администратора.")
    await admin_panel(c.message) # Вызываем админ-панель снова


# --- Добавление обработчиков для просмотра списков (базовый функционал) ---

@dp.callback_query(F.data == "admin_users_list", F.from_user.id == ADMIN_ID)
async def show_all_users(c: types.CallbackQuery):
    await c.answer()
    users = get_all_users()
    
    if not users:
        text = "🤷‍♂️ Пользователей пока нет."
    else:
        text = "👥 **Список зарегистрированных пользователей:**\n\n"
        for user_id, full_name, phone in users:
            text += f"• `{user_id}` | **{full_name}** | {phone}\n"

    kb = InlineKeyboardBuilder()
    kb.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_panel_back"))
    
    try:
        await c.message.edit_text(text, parse_mode="Markdown", reply_markup=kb.as_markup())
    except TelegramBadRequest:
        await c.message.answer(text, parse_mode="Markdown", reply_markup=kb.as_markup())


@dp.callback_query(F.data == "admin_questions_list", F.from_user.id == ADMIN_ID)
async def show_all_questions(c: types.CallbackQuery):
    await c.answer()
    questions = get_all_questions() # Эта функция возвращает (id, user_id, question_text, date)
    
    if not questions:
        text = "🤷‍♂️ Вопросов пока нет."
    else:
        text = "❓ **Список всех вопросов (новые сверху):**\n\n"
        for q_id, user_id, q_text, date in questions[:10]: # Показываем только 10 последних
            text += f"**ID:{q_id}** | `{date}`\n"
            text += f"От: `{user_id}`\n"
            text += f"Текст: _{q_text[:50]}..._\n"
            # Добавляем кнопку "Ответить" прямо здесь (можно было бы сделать отдельный список)
            text += f"[➡️ Ответить на вопрос](https://t.me/{(await bot.get_me()).username}?start=reply_{user_id})\n\n"

    kb = InlineKeyboardBuilder()
    kb.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_panel_back"))
    
    try:
        await c.message.edit_text(text, parse_mode="Markdown", reply_markup=kb.as_markup())
    except TelegramBadRequest:
        await c.message.answer(text, parse_mode="Markdown", reply_markup=kb.as_markup())


@dp.callback_query(F.data == "admin_panel_back", F.from_user.id == ADMIN_ID)
async def admin_panel_back(c: types.CallbackQuery, state: FSMContext):
    await c.answer()
    await state.clear()
    await admin_panel(c.message)


# --- 7. ЗАПУСК БОТА ---

@asynccontextmanager
async def webhook_context(dp: Dispatcher, bot: Bot):
    if WEBHOOK_URL:
        # Устанавливаем Webhook
        await bot.set_webhook(WEBHOOK_URL)
        logging.info(f"Webhook set to: {WEBHOOK_URL}")
        yield
        # Удаляем Webhook при завершении
        await bot.delete_webhook()
        logging.info("Webhook deleted.")
    else:
        # Для локального тестирования (Long Polling)
        logging.info("Starting in Long Polling mode (WEBHOOK_URL not set).")
        try:
            yield
        finally:
            pass


async def main():
    init_db()
    
    async with webhook_context(dp, bot):
        if WEBHOOK_URL:
            # Настройка aiohttp Web App для Webhook
            app = web.Application()
            app['bot'] = bot
            
            # Регистрируем обработчик aiogram
            SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
            
            # Настройка запуска
            setup_application(app, dp, bot=bot)
            
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, WEB_SERVER_HOST, WEB_SERVER_PORT)
            
            logging.info(f"Starting web server on {WEB_SERVER_HOST}:{WEB_SERVER_PORT}")
            await site.start()
            
            # Ждем завершения (постоянно)
            while True:
                await asyncio.sleep(3600)
        else:
            # Запуск Long Polling
            await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Bot stopped by user.")
    except Exception as e:
        logging.error(f"Critical error: {e}")