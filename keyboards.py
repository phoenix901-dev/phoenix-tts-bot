from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="⚙️ Настройки")],
        ],
        resize_keyboard=True
    )

def settings_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🗣 Голос: Текст", callback_data="set_voice_text")],
            [InlineKeyboardButton(text="📚 Голос: Книги", callback_data="set_voice_book")],
            [InlineKeyboardButton(text="⚡ Скорость речи", callback_data="set_rate")],
            [InlineKeyboardButton(text="💬 Поддержка", url="https://t.me/phoenix901bot")]
        ]
    )

def voices_menu(mode: str):
    # Оставляем только те голоса, которые 100% работают в публичном API Microsoft Edge
    voices = [
        ("RU - Дмитрий (Муж)", "ru-RU-DmitryNeural"),
        ("RU - Светлана (Жен)", "ru-RU-SvetlanaNeural"),
        ("EN - Guy (Муж)", "en-US-GuyNeural"),
        ("EN - Aria (Жен)", "en-US-AriaNeural")
    ]
    kb = [[InlineKeyboardButton(text=name, callback_data=f"voice_{mode}_{code}")] for name, code in voices]
    kb.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_settings")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def rates_menu():
    rates = [
        ("-10% (Медленно)", "-10%"),
        ("+0% (Нормально)", "+0%"),
        ("+15% (Быстро)", "+15%"),
        ("+25% (Очень быстро)", "+25%"),
        ("+50% (Турбо)", "+50%")
    ]
    kb = [[InlineKeyboardButton(text=name, callback_data=f"rate_{code}")] for name, code in rates]
    kb.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_settings")])
    return InlineKeyboardMarkup(inline_keyboard=kb)