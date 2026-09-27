from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

# Курируемый список голосов edge-tts. Проверяется на старте бота
# (validate_voices), чтобы опечатка не вылезала юзеру во время озвучки.
VOICES: list[tuple[str, str]] = [
    # Русский
    ("RU · Дмитрий (М)", "ru-RU-DmitryNeural"),
    ("RU · Светлана (Ж)", "ru-RU-SvetlanaNeural"),
    ("RU · Дарья (Ж)", "ru-RU-DariyaNeural"),
    # Украинский
    ("UA · Остап (М)", "uk-UA-OstapNeural"),
    ("UA · Полина (Ж)", "uk-UA-PolinaNeural"),
    # Английский (US)
    ("EN · Guy (М)", "en-US-GuyNeural"),
    ("EN · Davis (М)", "en-US-DavisNeural"),
    ("EN · Aria (Ж)", "en-US-AriaNeural"),
    ("EN · Jenny (Ж)", "en-US-JennyNeural"),
    # Английский (UK)
    ("EN-GB · Ryan (М)", "en-GB-RyanNeural"),
    ("EN-GB · Sonia (Ж)", "en-GB-SoniaNeural"),
    # Немецкий
    ("DE · Conrad (М)", "de-DE-ConradNeural"),
    ("DE · Katja (Ж)", "de-DE-KatjaNeural"),
    # Французский
    ("FR · Henri (М)", "fr-FR-HenriNeural"),
    ("FR · Denise (Ж)", "fr-FR-DeniseNeural"),
    # Испанский
    ("ES · Alvaro (М)", "es-ES-AlvaroNeural"),
    ("ES · Elvira (Ж)", "es-ES-ElviraNeural"),
    # Турецкий
    ("TR · Ahmet (М)", "tr-TR-AhmetNeural"),
    ("TR · Emel (Ж)", "tr-TR-EmelNeural"),
]


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
    kb = [
        [InlineKeyboardButton(text=name, callback_data=f"voice_{mode}_{code}")]
        for name, code in VOICES
    ]
    kb.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_settings")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


RATES: list[tuple[str, str]] = [
    ("-10% (Медленно)", "-10%"),
    ("+0% (Нормально)", "+0%"),
    ("+15% (Быстро)", "+15%"),
    ("+25% (Очень быстро)", "+25%"),
    ("+50% (Турбо)", "+50%"),
]


def rates_menu():
    kb = [[InlineKeyboardButton(text=name, callback_data=f"rate_{code}")] for name, code in RATES]
    kb.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_settings")])
    return InlineKeyboardMarkup(inline_keyboard=kb)
