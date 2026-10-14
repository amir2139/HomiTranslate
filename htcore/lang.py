LANGS = {
    "fa": ("Persian (Farsi)", "فارسی"), "en": ("English", "انگلیسی"), "ar": ("Arabic", "عربی"),
    "tr": ("Turkish", "ترکی"), "ru": ("Russian", "روسی"), "de": ("German", "آلمانی"),
    "fr": ("French", "فرانسوی"), "es": ("Spanish", "اسپانیایی"), "it": ("Italian", "ایتالیایی"),
    "pt": ("Portuguese", "پرتغالی"), "zh": ("Chinese", "چینی"), "ja": ("Japanese", "ژاپنی"),
    "ko": ("Korean", "کره‌ای"), "uk": ("Ukrainian", "اوکراینی"), "pl": ("Polish", "لهستانی"),
    "hi": ("Hindi", "هندی"), "ur": ("Urdu", "اردو"), "id": ("Indonesian", "اندونزیایی"),
}
RTL = {"fa", "ar", "ur", "he"}


def lang_name(code):
    return LANGS.get(code, (code, code))[0]


_RANGES = [("cyrillic", 0x400, 0x4FF), ("arabic", 0x600, 0x6FF), ("arabic", 0xFB50, 0xFEFF),
           ("kana", 0x3040, 0x30FF), ("cjk", 0x4E00, 0x9FFF), ("hangul", 0xAC00, 0xD7AF),
           ("devanagari", 0x900, 0x97F), ("hebrew", 0x590, 0x5FF), ("greek", 0x370, 0x3FF)]
_HINTS = {"en": (" the ", " and ", " you ", " to ", " of "), "de": (" der ", " und ", " nicht ", " ich "),
          "fr": (" le ", " les ", " est ", " vous ", " une "), "es": (" el ", " los ", " que ", " una "),
          "tr": (" ve ", " bir ", " bu ", " için "), "pt": (" não ", " uma ", " você "),
          "it": (" il ", " che ", " non ", " una ")}
_SCRIPT_LANG = {"cyrillic": "ru", "arabic": "ar", "kana": "ja", "cjk": "zh", "hangul": "ko",
                "devanagari": "hi", "hebrew": "he", "greek": "el"}


def detect_lang(texts):
    """Rough script/stopword based detection -> (code, name)."""
    sample = " ".join(texts[:400])[:20000]
    scores = {}
    latin = 0
    for ch in sample:
        o = ord(ch)
        if ch.isascii():
            latin += ch.isalpha()
            continue
        for name, lo, hi in _RANGES:
            if lo <= o <= hi:
                scores[name] = scores.get(name, 0) + 1
                break
        else:
            latin += ch.isalpha()
    if not scores and not latin:
        return "auto", "auto"
    top = max(scores, key=scores.get) if scores else None
    if top and scores[top] > latin:
        if top == "cjk" and scores.get("kana", 0) > scores["cjk"] * 0.1:
            top = "kana"
        code = _SCRIPT_LANG[top]
        return code, lang_name(code)
    low = " " + sample.lower() + " "
    best, bs = "en", 0
    for code, words in _HINTS.items():
        s = sum(low.count(w) for w in words)
        if s > bs:
            best, bs = code, s
    return best, lang_name(best)
