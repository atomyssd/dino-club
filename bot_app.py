import asyncio
import logging
import sqlite3
from datetime import datetime
import re
import json
import os
import sys

# --- НОВЫЕ ИМПОРТЫ ДЛЯ WEBHOOK/RENDER ---
from contextlib import asynccontextmanager
from aiohttp import web
from gunicorn.app.base import BaseApplication
from aiogram.fsm.storage.memory import MemoryStorage # Добавлен для FSM
# ----------------------------------------

# --- 1. КОНФИГУРАЦИЯ И КОНСТАНТЫ ---

# !!! ВАЖНО: ЗАМЕНИТЕ ЭТИ ЗНАЧЕНИЯ !!!
# API_TOKEN должен быть получен из ENV на Render, но оставляем для локального теста
API_TOKEN = os.getenv("BOT_TOKEN", '8483546485:AAEtBnI8QDW07CgHbHXoapLYov1ELwORjeA') # ВАШ ТОКЕН
ADMIN_ID = 752078351 # ВАШ ID
ADMIN_USERNAME = "@Dina_Di_Ru"
CONTACT_PHONES = ["+998972488886", "+998975690286"]
DB_NAME = 'dino_club.db'
LOCATION_COORDS = {'latitude': 40.4979864, 'longitude': 68.7777999}
PHONE_REGEX = re.compile(r'^\+?\d{9,15}$')

# --- КОНСТАНТЫ ДЛЯ WEBHOOK (Render) ---
WEB_SERVER_HOST = "0.0.0.0"
# Render автоматически устанавливает PORT
WEB_SERVER_PORT = int(os.environ.get("PORT", 8080))
WEBHOOK_PATH = "/webhook"
# Полный URL будет формироваться на Render
BASE_WEBHOOK_URL = os.getenv("RENDER_EXTERNAL_URL") 
WEBHOOK_URL = f"{BASE_WEBHOOK_URL}{WEBHOOK_PATH}" if BASE_WEBHOOK_URL else None
# ----------------------------------------

# --- 2. БАЗА ДАННЫХ (БЕЗ ИЗМЕНЕНИЙ) ---

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
    cursor.execute('SELECT id, question_text, date FROM questions ORDER BY date DESC')
    rows = cursor.fetchall()
    conn.close()
    return rows

def delete_user(user_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM users WHERE user_id = ?', (user_id,))
    cursor.execute('DELETE FROM enrollments WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def delete_question(q_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM questions WHERE id = ?', (q_id,))
    conn.commit()
    conn.close()

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


# --- 3. НАСТРОЙКА БОТА, ТЕКСТЫ И ПРЕДМЕТЫ ---
logging.basicConfig(level=logging.INFO)
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest

# Инициализация диспетчера с хранилищем (обязательно для FSM)
dp = Dispatcher(storage=MemoryStorage())
bot = Bot(token=API_TOKEN)

# ИСПРАВЛЕННЫЙ БЛОК ТЕКСТОВ: Все узбекские строки с апострофами обернуты в ДВОЙНЫЕ кавычки
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
        'reg_complete': 'Регистрация завершена! Вы записаны на курс:'
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
        'reg_complete': "Ro'yxatdan o'tish yakunlandi! Siz kursga yozildingiz:"
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


# --- 5. ОБРАБОТЧИКИ БОТА (ЛОГИКА) ---

@dp.message(Command("start"))
async def start(m: types.Message):
    kb = InlineKeyboardBuilder()
    kb.add(types.InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang_ru"),
           types.InlineKeyboardButton(text="🇺🇿 O'zbek", callback_data="lang_uzb"))
    await m.answer("Выберите язык / Tilni tanlang:", reply_markup=kb.as_markup())


@dp.callback_query(F.data.startswith("lang_"))
async def set_lang(c: types.CallbackQuery):
    lang = c.data.split("_")[1]
    try:
        await c.message.edit_text(STRINGS[lang]['menu'], reply_markup=main_kb(lang))
    except TelegramBadRequest:
        await c.message.answer(STRINGS[lang]['menu'], reply_markup=main_kb(lang))


@dp.callback_query(F.data.startswith("nav_"))
async def route(c: types.CallbackQuery, state: FSMContext):
    await c.answer()
    _, act, lang = c.data.split("_")
    s = STRINGS[lang]

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
        await bot.send_location(c.message.chat.id, 
                                 latitude=LOCATION_COORDS['latitude'], 
                                 longitude=LOCATION_COORDS['longitude'])

        text = (
            "📍 **Мы находимся здесь:**\n"
            "[Открыть в Google Maps](https://maps.app.goo.gl/YourActualLink)" if lang == 'ru' else
            "📍 **Biz bu yerda joylashganmiz:**\n"
            "[Google Xaritada ochish](https://maps.app.goo.gl/YourActualLink)"
        )
        await c.message.answer(text, parse_mode="Markdown")

    elif act == "ask":
        await state.update_data(l=lang)
        await c.message.answer(
            "❓ Введите ваш анонимный вопрос:" if lang == 'ru' else "❓ Anonim savolingizni kiriting:")
        await state.set_state(Form.ask_q)

    elif act == "res":
        await c.message.answer(
            "🏆 Результаты учеников и достижения: скоро здесь!" if lang == 'ru' else "🏆 O'quvchilar natijalari va yutuqlari: tez orada shu yerda bo'ladi!")

    elif act == "tst":
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
        text += f"👤 **Telegram:** {ADMIN_USERNAME}\n"
        for i, phone in enumerate(CONTACT_PHONES, 1):
            text += f"📱 **Телефон {i}:** [{phone}](tel:{phone})\n"
        text += "\nМы рады вам помочь!" if lang == 'ru' else "\nSizga yordam berishdan mamnunmiz!"
        await c.message.answer(text, parse_mode="Markdown")

    elif act == "cab":
        user_data = get_user_data(c.from_user.id)
        
        if lang == 'ru':
            if not user_data:
                await c.message.answer("❌ Вы еще не зарегистрированы. Нажмите '📞 Регистрация'.",
                                       reply_markup=main_kb(lang))
                return
            full_name, phone, course_key = user_data
            text = f"👤 <b>Ваш Личный Кабинет</b>\n\nИмя: {full_name}\nТелефон: {phone}\n"
            button_text = "✏️ Изменить данные/курс"
            not_selected = "❌ Не выбран"
            select_prompt = "Для выбора курса нажмите '✏️ Изменить данные/курс'."

        else:  # uzb
            # --- ИСПРАВЛЕНИЕ IndentationError (Блок else был без отступа) ---
            if not user_data:
                # ВАША ИСПРАВЛЕННАЯ СТРОКА с ТЕКСТОМ и ОТСТУПОМ
                await c.message.answer(f"❌ {STRINGS['uzb']['cab'].replace('👤 Kabinet', 'Siz hali ro\'yxatdan o\'tmagansiz.')} '{STRINGS['uzb']['reg']}' tugmasini bosing.",
                                        reply_markup=main_kb(lang))
                return
            
            full_name, phone, course_key = user_data
            text = f"👤 <b>Sizning shaxsiy kabinetingiz</b>\n\nIsm: {full_name}\nTelefon: {phone}\n"
            button_text = "✏️ Ma'lumotlarni/kursni o'zgartirish"
            not_selected = "❌ Tanlanmagan"
            select_prompt = "Kursni tanlash uchun '✏️ Ma'lumotlarni/kursni o'zgartirish' tugmasini bosing."
            # -----------------------------------------------------------------

        if course_key and course_key in SUBJECTS:
            course_name = SUBJECTS[course_key][lang]['name']
            
            course_text = "Ваш курс:" if lang == 'ru' else "Sizning kursingiz:"
            text += f"\n{course_text} <b>{course_name}</b>\n"
            
            try:
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
        await c.message.answer(text, parse_mode="HTML", reply_markup=kb.as_markup())


@dp.callback_query(F.data.startswith("reg_type_"))
async def process_reg_type(c: types.CallbackQuery, state: FSMContext):
    await c.answer()
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
    
    if not PHONE_REGEX.match(m.text):
        await m.answer(STRINGS[lang]['tel_error'])
        return

    save_user(m.from_user.id, data['n'], m.text)

    reg_status_ru = "УЖЕ УЧИТСЯ" if data.get('reg_type') == 'already' else "НОВЫЙ КАНДИДАТ"

    await bot.send_message(
        ADMIN_ID,
        f"🔔 НОВЫЙ ВВОД ДАННЫХ ({reg_status_ru}):\n"
        f"ФИО: {data['n']}\n"
        f"Телефон: {m.text}"
    )

    kb = InlineKeyboardBuilder()
    for k in SUBJECTS:
        kb.row(types.InlineKeyboardButton(text=SUBJECTS[k][lang]['name'], callback_data=f"reg_course_{k}_{lang}"))

    await m.answer(STRINGS[lang]['select_course'], reply_markup=kb.as_markup())
    await state.set_state(Form.select_course)


@dp.callback_query(F.data.startswith("reg_course_"), Form.select_course)
async def enroll_course(c: types.CallbackQuery, state: FSMContext):
    await c.answer()
    _, _, course_key, lang = c.data.split("_")

    save_enrollment(c.from_user.id, course_key)

    course_name = SUBJECTS[course_key][lang]['name']
    
    user_data = get_user_data(c.from_user.id)
    # Исправление: если user_data нет (хотя не должно быть), даем значения по умолчанию
    name, phone, _ = user_data if user_data else ("Неизвестно", "Неизвестно", None)

    await bot.send_message(
        ADMIN_ID, 
        f"✅ **КУРС ОБНОВЛЕН/ЗАПИСЬ:**\n"
        f"Пользователь: {name} (ID: {c.from_user.id})\n"
        f"Телефон: {phone}\n"
        f"Курс: **{course_name}**", 
        parse_mode="Markdown")

    reg_complete_text = STRINGS[lang]['reg_complete']
    text = f"✅ {reg_complete_text} <b>{course_name}</b>."

    try:
        await c.message.edit_text(text, parse_mode="HTML")
    except TelegramBadRequest:
        await c.message.answer(text, parse_mode="HTML")

    await state.clear()


@dp.message(Form.ask_q)
async def process_ask(m: types.Message, state: FSMContext):
    save_question(m.from_user.id, m.text)
    
    user_info = get_user_data(m.from_user.id)
    name = user_info[0] if user_info else "Неизвестный пользователь"

    await bot.send_message(
        ADMIN_ID, 
        f"❓ **НОВЫЙ ВОПРОС (АННОНИМНО):**\n"
        f"От: {name} (ID: {m.from_user.id})\n"
        f"Текст: {m.text}", 
        parse_mode="Markdown")
    
    lang = (await state.get_data())['l']
    await m.answer("✅ OK! Ваш вопрос передан администратору." if lang == 'ru' else "✅ OK! Savolingiz administratorga yuborildi.")
    await state.clear()


@dp.callback_query(F.data.startswith("cat_"))
async def show_cat(c: types.CallbackQuery):
    await c.answer()
    _, key, lang = c.data.split("_")
    s = STRINGS[lang]
    kb = InlineKeyboardBuilder()

    if SUBJECTS[key][lang]['items']:
        for i, t in enumerate(SUBJECTS[key][lang]['items']):
            kb.row(types.InlineKeyboardButton(
                text=f"👨‍🏫 {t['n']}", 
                callback_data=f"det_{key}_{i}_{lang}"))
    else:
        text = (f"По направлению {SUBJECTS[key][lang]['name']} пока нет данных. Выберите другой язык или направление." 
                if lang == 'ru' else 
                f"{SUBJECTS[key][lang]['name']} yo'nalishi bo'yicha ma'lumot yo'q. Boshqa yo'nalishni tanlang.")
        await c.message.answer(text)

    kb.row(types.InlineKeyboardButton(text=s['back'], callback_data=f"nav_sub_{lang}"))

    try:
        await c.message.edit_text(SUBJECTS[key][lang]['name'], reply_markup=kb.as_markup())
    except TelegramBadRequest:
        await c.message.answer(SUBJECTS[key][lang]['name'], reply_markup=kb.as_markup())


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


# --- ОБРАБОТЧИКИ ТЕСТА (БЕЗ ИЗМЕНЕНИЙ) ---

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

    correct_answer_index = questions[q_index_answered][2]

    try:
        if answer_index == correct_answer_index:
            current_score += 1
            await c.message.edit_text(c.message.text + (
                "\n\n✅ **Верно!**" if lang == 'ru' else "\n\n✅ **To'g'ri!**"), parse_mode="Markdown")
        else:
            await c.message.edit_text(c.message.text + (
                "\n\n❌ **Неверно.**" if lang == 'ru' else "\n\n❌ **Noto'g'ri.**"), parse_mode="Markdown")
    except TelegramBadRequest:
        pass

    next_index = q_index_answered + 1
    await state.update_data(test_score=current_score, question_index=next_index)

    await asyncio.sleep(0.5)

    await ask_test_question(c.message, state)


# --- ИСПРАВЛЕННЫЕ ОБРАБОТЧИКИ ТЕСТА ---

# ... (остальной код) ...

async def finish_test(message: types.Message, state: FSMContext):
    data = await state.get_data()
    final_score = data['test_score']
    total_questions = len(data['test_questions'])
    lang = data['l']
    s = STRINGS[lang]

    if final_score <= 5:
        level = "Beginner/Elementary (A1/A2)"
        recommendation = ("Вам необходима сильная базовая программа для изучения основ. Начните с нашего общего курса для начинающих!" if lang == 'ru' else
                          "Sizga asoslarni o'rganish uchun kuchli boshlang'ich dastur kerak. Yangi boshlanuvchilar uchun umumiy kursimizdan boshlang!")
    elif final_score <= 10:
        level = "Pre-Intermediate (A2/B1)"
        recommendation = ("У вас есть хорошие базовые знания. Рекомендуем курс для среднего уровня." if lang == 'ru' else
                          "Unda yaxshi asosiy bilimlar bor. O'rta darajadagi kursni tavsiya qilamiz.")
    else:
        level = "Intermediate (B1) или выше"
        recommendation = ("Отличный результат! Вы можете попробовать курс подготовки к IELTS." if lang == 'ru' else
                          "Ajoyib natija! Siz IELTS ga tayyorgarlik kursini sinab ko'rishingiz mumkin.")
    
    # --- ИСПРАВЛЕННЫЙ БЛОК: Устранено использование слэшей и вложенных кавычек в f-строке ---
    
    # Определяем основные переменные для текста
    header = "Тест завершен!" if lang == 'ru' else "Test yakunlandi!"
    result_label = "Ваш результат:" if lang == 'ru' else "Sizning taxminiy darajangiz (aniq emas):"
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
    # ----------------------------------------------------------------------------------------

    await message.answer(result_text, parse_mode="Markdown")

    await state.clear()
    await message.answer(s['menu'], reply_markup=main_kb(lang))

# ... (остальной код) ...


# --- ОБРАБОТЧИКИ АДМИНА ---

@dp.message(Command("admin"))
async def admin(m: types.Message):
    if m.from_user.id == ADMIN_ID:
        kb = InlineKeyboardBuilder()
        kb.row(types.InlineKeyboardButton(text="👥 Список учеников", callback_data="adm_l"),
               types.InlineKeyboardButton(text="📢 Рассылка", callback_data="adm_b"))
        kb.row(types.InlineKeyboardButton(text="❓ Список вопросов", callback_data="adm_q"))
        kb.row(types.InlineKeyboardButton(text="🗑 Очистить вопросы (ВСЕ)", callback_data="adm_clear_q"),
               types.InlineKeyboardButton(text="❌ Очистить учеников (ВСЕ)", callback_data="adm_clear_u"))
        await m.answer("🛠 Панель администратора:", reply_markup=kb.as_markup())


@dp.callback_query(F.data == "adm_l")
async def adm_l(c: types.CallbackQuery):
    await c.answer()
    users = get_all_users()
    if not users:
        await c.message.answer("База учеников пуста.")
        return

    await c.message.answer("👥 Зарегистрированные ученики (нажмите '❌ Удалить' для удаления записи):")

    for u in users:
        user_id, full_name, phone = u
        text = f"👤 ФИО: {full_name}\n📞 Телефон: {phone}\nID: {user_id}"
        kb = InlineKeyboardBuilder()
        kb.add(types.InlineKeyboardButton(text="❌ Удалить", callback_data=f"adm_del_u_{user_id}"))
        await c.message.answer(text, reply_markup=kb.as_markup())

    await c.message.answer("--- Конец списка учеников ---")


@dp.callback_query(F.data == "adm_q")
async def adm_q(c: types.CallbackQuery):
    await c.answer()
    questions = get_all_questions()
    if not questions:
        await c.message.answer("Список вопросов пуст.")
        return

    await c.message.answer("❓ Анонимные вопросы (нажмите '🗑 Удалить' для удаления записи):")

    for q in questions:
        q_id, question_text, date = q
        text = f"❓ Вопрос #{q_id} от {date}:\n{question_text}"
        kb = InlineKeyboardBuilder()
        kb.add(types.InlineKeyboardButton(text="🗑 Удалить", callback_data=f"adm_del_q_{q_id}"))
        await c.message.answer(text, reply_markup=kb.as_markup())

    await c.message.answer("--- Конец списка вопросов ---")


@dp.callback_query(F.data == "adm_b")
async def adm_b(c: types.CallbackQuery, state: FSMContext):
    await c.answer()
    await c.message.answer("Введите текст для рассылки:")
    await state.set_state(Form.bc)


@dp.message(Form.bc)
async def bc_f(m: types.Message, state: FSMContext):
    u = get_all_users()
    sent_count = 0
    for x in u:
        try:
            await bot.send_message(x[0], m.text)
            sent_count += 1
        except Exception as e:
            logging.error(f"Failed to send to {x[0]}: {e}")
    await m.answer(f"✅ Рассылка завершена! Отправлено {sent_count}/{len(u)}.")
    await state.clear()


@dp.callback_query(F.data.startswith("adm_del_u_"))
async def adm_del_u(c: types.CallbackQuery):
    await c.answer("Удаление...")
    user_id_to_delete = int(c.data.split("_")[3])
    delete_user(user_id_to_delete)
    await c.message.edit_text(c.message.text + "\n\n❌ **Удален.**", parse_mode="Markdown")


@dp.callback_query(F.data.startswith("adm_del_q_"))
async def adm_del_q(c: types.CallbackQuery):
    await c.answer("Удаление...")
    q_id_to_delete = int(c.data.split("_")[3])
    delete_question(q_id_to_delete)
    await c.message.edit_text(c.message.text + "\n\n🗑 **Удален.**", parse_mode="Markdown")


@dp.callback_query(F.data == "adm_clear_q")
async def adm_clear_q(c: types.CallbackQuery):
    await c.answer()
    clear_questions()
    await c.message.answer("🗑 Все вопросы очищены.")


@dp.callback_query(F.data == "adm_clear_u")
async def adm_clear_u(c: types.CallbackQuery):
    await c.answer()
    clear_users()
    await c.message.answer("❌ Все ученики и записи очищены.")

# ----------------------------------------------------------------------
# --- 6. ЗАПУСК ПРИЛОЖЕНИЯ (ЛОГИКА WEBHOOK/GUNICORN) ---
# ----------------------------------------------------------------------

# 1. Контекстный менеджер для установки/удаления Webhook
@asynccontextmanager
async def webhook_life_span(dispatcher: Dispatcher, bot: Bot):
    # Инициализация DB
    init_db()
    
    if WEBHOOK_URL:
        # Установка Webhook при запуске
        logging.info(f"Установка Webhook: {WEBHOOK_URL}")
        await bot.set_webhook(url=WEBHOOK_URL, allowed_updates=dispatcher.resolve_used_update_types())
    else:
        # Локальный режим, если нет публичного URL (для теста)
        logging.warning("Нет публичного URL. Запуск в режиме Long Polling (только для локальной отладки).")
        asyncio.create_task(dispatcher.start_polling(bot))
    
    yield # Ожидание работы

    # Удаление Webhook при завершении работы (при остановке Gunicorn)
    if WEBHOOK_URL:
        await bot.delete_webhook()
        logging.info("Webhook удален.")


# 2. Создание Aiohttp приложения для Gunicorn
def init_app():
    # Настройка Webhook-роутера для aiohttp
    webhook_request_handler = dp.get_web_app_factory()
    
    # Применяем life_span к Dispatcher
    webhook_request_handler.__self__.startup_lifespan = webhook_life_span(dp, bot)
    
    # Назначаем роутер на путь, который будет слушать Gunicorn
    webhook_request_handler.__self__.webhook_path = WEBHOOK_PATH
    
    # Назначаем сам бот для использования в хендлере
    webhook_request_handler.__self__.bot = bot
    
    return webhook_request_handler


# 3. Класс, который Gunicorn использует для запуска приложения
class StandaloneApplication(BaseApplication):
    def __init__(self, app, options=None):
        self.options = options or {}
        self.application = app
        super().__init__()

    def load_config(self):
        config = {
            key: value
            for key, value in self.options.items()
            if key in self.cfg.settings and value is not None
        }
        for key, value in config.items():
            self.cfg.set(key.lower(), value)

    def load(self):
        return self.application

# 4. Главный объект, который запускает Gunicorn
# ЭТО 'bot_app:application' в вашем Procfile!
application = init_app()

if __name__ == '__main__':
    # Эта часть для локального запуска (если нет WEBHOOK_URL), 
    # на Render не используется, так как Gunicorn вызывает application()
    if WEBHOOK_URL:
        web.run_app(application, host=WEB_SERVER_HOST, port=WEB_SERVER_PORT)
    else:
        # Для локальной отладки Long Polling
        async def main_polling():
            init_db()
            await dp.start_polling(bot)
        asyncio.run(main_polling())

# ----------------------------------------------------------------------
# --- ФИНАЛЬНЫЙ ШАГ ---
# ----------------------------------------------------------------------