"""Single source of truth for the ~40 supported display languages.

Pure data + tiny helpers — no I/O, no side effects.
"""

LANGUAGES: list[dict] = [
    {"code": "de", "name": "German",     "native": "Deutsch",       "color": "#b8c9e8", "centroid": [51.16, 10.45]},
    {"code": "en", "name": "English",    "native": "English",       "color": "#b8e0c8", "centroid": [51.5, -0.12]},
    {"code": "fr", "name": "French",     "native": "Français",      "color": "#f5d0a9", "centroid": [46.23, 2.21]},
    {"code": "es", "name": "Spanish",    "native": "Español",       "color": "#f5d0d0", "centroid": [40.42, -3.7]},
    {"code": "it", "name": "Italian",    "native": "Italiano",      "color": "#d0e8b8", "centroid": [41.9, 12.5]},
    {"code": "pt", "name": "Portuguese", "native": "Português",     "color": "#c8d0e8", "centroid": [39.4, -8.2]},
    {"code": "nl", "name": "Dutch",      "native": "Nederlands",    "color": "#e8dab8", "centroid": [52.37, 4.9]},
    {"code": "pl", "name": "Polish",     "native": "Polski",        "color": "#e8c8c8", "centroid": [52.0, 19.0]},
    {"code": "ru", "name": "Russian",    "native": "Русский",       "color": "#c8c0e0", "centroid": [55.75, 37.62]},
    {"code": "uk", "name": "Ukrainian",  "native": "Українська",    "color": "#e0daa8", "centroid": [48.38, 31.17]},
    {"code": "ro", "name": "Romanian",   "native": "Română",        "color": "#d0e0c8", "centroid": [45.94, 24.97]},
    {"code": "el", "name": "Greek",      "native": "Ελληνικά",      "color": "#c8d8e8", "centroid": [39.0, 22.0]},
    {"code": "sv", "name": "Swedish",    "native": "Svenska",       "color": "#e8c8d8", "centroid": [60.0, 18.0]},
    {"code": "da", "name": "Danish",     "native": "Dansk",         "color": "#d8e8d0", "centroid": [56.0, 10.0]},
    {"code": "fi", "name": "Finnish",    "native": "Suomi",         "color": "#e0c8d8", "centroid": [62.0, 26.0]},
    {"code": "cs", "name": "Czech",      "native": "Čeština",       "color": "#d8d0e8", "centroid": [49.74, 15.34]},
    {"code": "hu", "name": "Hungarian",  "native": "Magyar",        "color": "#e8d8c0", "centroid": [47.16, 19.5]},
    {"code": "tr", "name": "Turkish",    "native": "Türkçe",        "color": "#c8e0d0", "centroid": [39.0, 35.0]},
    {"code": "ar", "name": "Arabic",     "native": "العربية",       "color": "#e8c8a8", "centroid": [25.0, 45.0]},
    {"code": "fa", "name": "Persian",    "native": "فارسی",         "color": "#d0c8e8", "centroid": [32.43, 53.69]},
    {"code": "he", "name": "Hebrew",     "native": "עברית",         "color": "#c0d8c8", "centroid": [31.05, 34.85]},
    {"code": "hi", "name": "Hindi",      "native": "हिन्दी",         "color": "#e8d0b8", "centroid": [22.0, 79.0]},
    {"code": "bn", "name": "Bengali",    "native": "বাংলা",         "color": "#d8e8c8", "centroid": [23.68, 90.35]},
    {"code": "ur", "name": "Urdu",       "native": "اردو",          "color": "#c8e8d8", "centroid": [30.38, 69.35]},
    {"code": "ta", "name": "Tamil",      "native": "தமிழ்",          "color": "#e0d0c8", "centroid": [11.13, 78.66]},
    {"code": "zh", "name": "Chinese",    "native": "中文",           "color": "#c8d0c8", "centroid": [35.0, 104.0]},
    {"code": "ja", "name": "Japanese",   "native": "日本語",          "color": "#e8c8e0", "centroid": [36.2, 138.25]},
    {"code": "ko", "name": "Korean",     "native": "한국어",          "color": "#c8e0e8", "centroid": [36.5, 127.98]},
    {"code": "id", "name": "Indonesian", "native": "Bahasa Indonesia","color": "#d8c8d8", "centroid": [-0.79, 113.92]},
    {"code": "ms", "name": "Malay",      "native": "Bahasa Melayu",  "color": "#e0e8c8", "centroid": [4.21, 101.98]},
    {"code": "vi", "name": "Vietnamese", "native": "Tiếng Việt",    "color": "#c8d8d0", "centroid": [14.06, 108.28]},
    {"code": "th", "name": "Thai",       "native": "ภาษาไทย",       "color": "#e8d8e0", "centroid": [15.87, 100.99]},
    {"code": "sw", "name": "Swahili",    "native": "Kiswahili",     "color": "#d0e0d8", "centroid": [-1.29, 36.82]},
    {"code": "am", "name": "Amharic",    "native": "አማርኛ",          "color": "#e8e0c8", "centroid": [9.15, 40.49]},
    {"code": "ha", "name": "Hausa",      "native": "Hausa",         "color": "#c8c8d8", "centroid": [12.0, 8.52]},
    {"code": "yo", "name": "Yoruba",     "native": "Yorùbá",        "color": "#d8c0c8", "centroid": [7.86, 3.94]},
    {"code": "af", "name": "Afrikaans",  "native": "Afrikaans",     "color": "#c8e8e0", "centroid": [-29.0, 25.0]},
    {"code": "no", "name": "Norwegian",  "native": "Norsk",         "color": "#d8e0e8", "centroid": [60.5, 8.5]},
    {"code": "sk", "name": "Slovak",     "native": "Slovenčina",    "color": "#e8e8c8", "centroid": [48.67, 19.7]},
    {"code": "bg", "name": "Bulgarian",  "native": "Български",     "color": "#d0c8c8", "centroid": [42.73, 25.48]},
]

_BY_CODE: dict[str, dict] = {lang["code"]: lang for lang in LANGUAGES}

CODES: list[str] = [lang["code"] for lang in LANGUAGES]


def by_code(code: str) -> dict | None:
    return _BY_CODE.get(code)
