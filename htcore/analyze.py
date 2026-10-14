"""File analysis: size stats, code ratio, game-engine guess + extraction guide, font warning."""
import re
from pathlib import Path

from .lang import detect_lang, RTL
from .protect import protect

GUIDES = {
    "unity": {
        "name": "Unity",
        "fa": "Unity: ساده‌ترین راه افزونه‌ی XUnity.AutoTranslator است که متن‌ها را در حین بازی در فایل key=value ذخیره می‌کند (فرمت «key=value» همین برنامه). برای درآوردن از فایل‌های assets می‌توان از UABEA استفاده کرد؛ بعد از ترجمه متن را با همان ابزار برگردانید. فونت باید TextMeshPro/Unity با حروف فارسی باشد.",
        "en": "Unity: the easiest way is the XUnity.AutoTranslator plugin, which dumps strings to a key=value file (the key=value format of this tool). To pull text out of assets use UABEA, then import the translated text back with the same tool. The font must be a TextMeshPro/Unity font with Persian glyphs.",
    },
    "unreal": {
        "name": "Unreal Engine",
        "fa": "Unreal: فایل‌های .locres را با UnrealLocres به CSV تبدیل کنید، ترجمه کنید و دوباره به .locres برگردانید. بسته‌های .pak را با repak/UnrealPak باز و دوباره ببندید (معمولاً به‌صورت پچ _P).",
        "en": "Unreal: convert .locres files to CSV with UnrealLocres, translate, then convert back to .locres. Unpack/repack .pak archives with repak or UnrealPak (usually as a _P patch pak).",
    },
    "renpy": {
        "name": "Ren'Py",
        "fa": "Ren'Py: با دستور «renpy.sh <پروژه> translate persian» فایل‌های tl/ ساخته می‌شود. متن‌های old/new را استخراج کنید، ترجمه کنید و فقط مقدار new را جایگزین کنید. فونت جداگانه‌ی فارسی در gui لازم است.",
        "en": "Ren'Py: run \"renpy.sh <project> translate persian\" to generate tl/ files. Extract the old/new strings, translate, and replace only the new value. A Persian-capable font must be set in the GUI config.",
    },
    "rpgmaker": {
        "name": "RPG Maker",
        "fa": "RPG Maker (MV/MZ): فایل‌های www/data/*.json (Map*.json، Items.json، System.json ...) را مستقیم می‌توان ترجمه کرد؛ ساختار JSON حفظ می‌شود. کدهای \\C[1] و \\V[1] خودکار قفل می‌شوند. فونت را در css/fonts یا فایل gamefont.css عوض کنید.",
        "en": "RPG Maker (MV/MZ): translate www/data/*.json (Map*.json, Items.json, System.json ...) directly; JSON structure is preserved. Codes like \\C[1] and \\V[1] are locked automatically. Change the font in css/fonts or gamefont.css.",
    },
    "godot": {
        "name": "Godot",
        "fa": "Godot: ترجمه‌ها معمولاً CSV (ستون keys + ستون زبان‌ها) یا PO هستند؛ همین فایل را بدهید و ستون مقصد را انتخاب کنید. اگر بازی فشرده (.pck) است، با ابزار GDRE Tools باز کنید.",
        "en": "Godot: translations are usually CSV (a keys column plus one column per language) or PO; pass that file and pick the destination column. For packed .pck games use GDRE Tools to extract.",
    },
    "gettext": {
        "name": "gettext (PO)",
        "fa": "فایل PO/POT استاندارد gettext است؛ مستقیم قابل ترجمه است و بعد از ترجمه با msgfmt به .mo تبدیل می‌شود.",
        "en": "A standard gettext PO/POT file; translate directly, then compile to .mo with msgfmt.",
    },
    "generic": {
        "name": "Unknown",
        "fa": "موتور بازی از ساختار فایل مشخص نشد. اگر فایل را از خود بازی درآورده‌اید، همین‌طور ترجمه می‌شود؛ فقط مطمئن شوید ساختار فایل را بعد از ترجمه به همان ابزار استخراج برمی‌گردانید.",
        "en": "Could not tell the game engine from the file structure. If you extracted the file from the game it can still be translated; re-import it with the same tool you used to extract it.",
    },
}

FONT_WARN = {
    "fa": "هشدار فونت: بیشتر فونت‌های بازی‌ها حروف فارسی/عربی ندارند. اگر بعد از ترجمه به‌جای متن مربع یا علامت سؤال دیدید، باید فونت بازی را با فونتی که فارسی دارد (مثل Vazirmatn) عوض کنید. بعضی موتورها حروف را نمی‌چسبانند و راست‌به‌چپ نمی‌کنند؛ در آن صورت گزینه‌ی «چسباندن حروف» را روشن کنید.",
    "en": "Font warning: most game fonts lack Persian/Arabic glyphs. If you see boxes or question marks after translating, replace the game font with one that has them (e.g. Vazirmatn). Some engines do not join letters or flow right-to-left; turn on the \"letter shaping\" option for those.",
}


def guess_engine(path, text, entries, fmt):
    p = Path(path)
    name = p.name.lower()
    sample = text[:200000]
    if fmt == "po":
        return "gettext", "high"
    if name.endswith(".rpy") or re.search(r'^\s*old\s+".*"\s*\n\s*new\s+"', sample, re.M):
        return "renpy", "high"
    if fmt == "json":
        if re.search(r'"code"\s*:\s*(101|401|102|402|105|405)', sample) or re.search(r'"(gameTitle|currencyUnit)"', sample) \
                or re.search(r'"(note|battlerName|characterName)"', sample):
            return "rpgmaker", "high"
        if re.search(r'"m_(Script|Name|GameObject)"', sample):
            return "unity", "medium"
    if fmt == "csv":
        head = sample.splitlines()[0].lower() if sample else ""
        if "sourcestring" in head or "namespace" in head:
            return "unreal", "high"
        if head.startswith("keys,") or head.startswith('"keys"'):
            return "godot", "high"
    if fmt == "kv":
        return "unity", "medium"
    if name.endswith((".locres",)):
        return "unreal", "high"
    return "generic", "low"


def analyze(path, doc, text=None):
    entries = doc.entries
    chars = sum(len(e) for e in entries)
    with_code = sum(1 for e in entries if protect(e)[1])
    if text is None:
        try:
            text = Path(path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            text = ""
    eng, conf = guess_engine(path, text, entries, doc.fmt)
    code, name = detect_lang(entries)
    return {
        "file": str(path),
        "format": doc.fmt,
        "lines": len(entries),
        "unique_lines": len(set(entries)),
        "chars": chars,
        "tokens": int(chars / 3.2),
        "code_percent": round(100.0 * with_code / max(1, len(entries)), 1),
        "engine": eng,
        "engine_confidence": conf,
        "engine_name": GUIDES[eng]["name"],
        "guide": {"fa": GUIDES[eng]["fa"], "en": GUIDES[eng]["en"]},
        "source_lang": code,
        "source_lang_name": name,
        "font_warning": dict(FONT_WARN),
        "info": doc.info,
    }


def needs_font_warning(target):
    return target in RTL or target in ("zh", "ja", "ko", "hi", "ru", "uk", "el")
