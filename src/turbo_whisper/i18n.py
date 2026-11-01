"""UI internationalization for Turbo Whisper.

The translation tables live in turbo_whisper.i18n_translations (generated);
this module provides the language registry and the tr() lookup.

Usage:
    from turbo_whisper.i18n import tr, set_language
    set_language("de")
    label = QLabel(tr("Settings"))

tr() falls back to the English original when a translation is missing.
"""

from __future__ import annotations

# 25 supported languages (NVIDIA Parakeet TDT 0.6B v3 coverage) + English.
# code -> native display name (shown in the language picker)
LANGUAGES: dict[str, str] = {
    "en": "English",
    "bg": "Български",
    "cs": "Čeština",
    "da": "Dansk",
    "de": "Deutsch",
    "el": "Ελληνικά",
    "es": "Español",
    "et": "Eesti",
    "fi": "Suomi",
    "fr": "Français",
    "hr": "Hrvatski",
    "hu": "Magyar",
    "it": "Italiano",
    "lt": "Lietuvių",
    "lv": "Latviešu",
    "mt": "Malti",
    "nl": "Nederlands",
    "pl": "Polski",
    "pt": "Português",
    "ro": "Română",
    "ru": "Русский",
    "sk": "Slovenčina",
    "sl": "Slovenščina",
    "sv": "Svenska",
    "uk": "Українська",
}

_current_lang = "en"


def set_language(code: str) -> None:
    """Set the active UI language (falls back to English when unknown)."""
    global _current_lang
    _current_lang = code if code in LANGUAGES else "en"


def get_language() -> str:
    return _current_lang


def tr(text: str) -> str:
    """Translate a UI string to the active language (fallback: original)."""
    if _current_lang == "en":
        return text
    try:
        from turbo_whisper.i18n_translations import TRANSLATIONS
    except ImportError:
        return text
    return TRANSLATIONS.get(_current_lang, {}).get(text, text)
