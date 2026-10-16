from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

from engines import silero_code

# Курируемый список голосов. edge-коды проверяются на старте бота
# (validate_voices), чтобы опечатка не вылезала юзеру во время озвучки.
# Silero-голоса помечены префиксом silero: — это локальный движок.
EDGE_VOICES: list[tuple[str, str]] = [
    # Русский
    ("RU · Дмитрий (М)", "ru-RU-DmitryNeural"),
    ("RU · Светлана (Ж)", "ru-RU-SvetlanaNeural"),
    # Украинский
    ("UA · Остап (М)", "uk-UA-OstapNeural"),
    ("UA · Полина (Ж)", "uk-UA-PolinaNeural"),
    # Английский (US)
    ("EN · Guy (М)", "en-US-GuyNeural"),
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

# Локальный движок Silero: работает без интернета, голоса только русские.
# Список голосов берётся из модели (см. scripts/update_silero.sh), здесь —
# те, что есть во всех актуальных v5-моделях.
SILERO_VOICES: list[tuple[str, str]] = [
    ("RU офлайн · Айдар (М)", silero_code("aidar")),
    ("RU офлайн · Байя (Ж)", silero_code("baya")),
    ("RU офлайн · Ксения (Ж)", silero_code("kseniya")),
    ("RU офлайн · Евгений (М)", silero_code("eugene")),
    ("RU офлайн · Ксения-2 (Ж)", silero_code("xenia")),
]

VOICES: list[tuple[str, str]] = EDGE_VOICES + SILERO_VOICES

_VOICE_LABELS = {code: name for name, code in VOICES}


def voice_label(code: str) -> str:
    """Человеческое имя голоса для настроек: 'silero:aidar' -> 'RU офлайн · Айдар (М)'."""
    return _VOICE_LABELS.get(code, code)



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
