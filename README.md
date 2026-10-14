# HomiTranslate

**فارسی** · [English](#english)

دستیار هوشمند ترجمه‌ی متن بازی‌ها به فارسی یا هر زبان دیگر. فقط پایتون ۳ و کتابخانه‌ی استاندارد؛ بدون نصب بسته، بدون CDN. رابط کاربری یک صفحه‌ی وب محلی است که فقط روی `127.0.0.1` اجرا می‌شود.

## معرفی

- فرمت‌ها بدون تغییر ساختار: JSON تودرتو (فقط مقدارهای رشته‌ای)، CSV (انتخاب ستون مبدأ و مقصد)، PO/POT، TXT خط‌به‌خط، و `key=value` (مثل XUnity.AutoTranslator).
- فایل ورودی هرگز بازنویسی نمی‌شود؛ خروجی در پوشه‌ی `out/` ذخیره می‌شود.
- کدهای داخل متن (`{0}`، `%s`، `<color=...>`، `[b]`، `\n`، `{{...}}` و ...) قفل می‌شوند. اگر مدل کدی را گم یا عوض کند، همان خط تا ۲ بار دوباره ترجمه می‌شود و اگر باز خراب بود علامت می‌خورد و متن اصلی می‌ماند.
- واژه‌نامه‌ی خودکار (اسم‌های خاص، عبارت‌های پرتکرار، متن‌های کوتاه منو) که ویرایش‌پذیر است و در همه‌ی دسته‌ها به مدل داده می‌شود.
- کش و ادامه‌ی کار با sqlite: خط تکراری دوبار ترجمه نمی‌شود و اگر کار قطع شد از همان‌جا ادامه می‌یابد.
- ویزارد وب: فایل ← تحلیل ← پیشنهاد روش ← واژه‌نامه ← ترجمه ← بازبینی ← خروجی. رابط دوزبانه (فارسی/انگلیسی) و حالت تاریک.

## نصب و اجرا

فقط پایتون ۳.9 یا بالاتر لازم است.

```bash
git clone https://github.com/amir2139/HomiTranslate.git
cd HomiTranslate
python homitranslate.py
```

مرورگر باز می‌شود. همه‌ی کارها با CLI هم ممکن است:

```bash
python homitranslate.py analyze samples/sample.json          # آمار + حدس موتور بازی
python homitranslate.py recommend samples/sample.json        # تشخیص GPU/Ollama و پیشنهاد روش
python homitranslate.py glossary game.json --extract --translate --export
python homitranslate.py translate game.json                  # واژه‌نامه + ترجمه + ذخیره در out/
python homitranslate.py translate data.csv --src-col en --dst-col fa
python homitranslate.py manual game.json --packets           # روش دستی: ساخت بسته‌ها در out/packets
python homitranslate.py manual game.json --merge answer.txt  # ادغام جواب paste‌شده
python homitranslate.py export game.json texts               # file | texts | glossary | prompt
python homitranslate.py pull gemma3:12b                      # دانلود مدل Ollama (فقط با دستور خودتان)
```

گزینه‌ها: `--target fa` (زبان مقصد، پیش‌فرض فارسی)، `--backend ollama|openai|mock`، `--model`، `--batch`، `--shape`.

## سه روش ترجمه

| روش | چه چیزی لازم است | مناسب برای |
|---|---|---|
| **A. محلی (Ollama)** | Ollama روی `http://127.0.0.1:11434` و کارت گرافیک | حجم زیاد، رایگان و آفلاین |
| **B. API سازگار با OpenAI** | `base_url`، کلید، مدل (DeepSeek، OpenRouter، Gemini، ...) و پروکسی HTTP اختیاری | سرعت و کیفیت بالا |
| **C. دستی (بدون API)** | یک سایت چت رایگان | حجم کم، بدون کلید |

- کلید API فقط در `config.local.json` ذخیره می‌شود و این فایل در `.gitignore` است (نمونه: `config.example.json`).
- مثال Gemini: `base_url = https://generativelanguage.googleapis.com/v1beta/openai`.
- روش C: برنامه متن را به بسته‌های مناسب سایت‌های چت تقسیم می‌کند؛ هر بسته پرامپت آماده‌ی کپی (دستورالعمل + واژه‌نامه + خط‌های شماره‌دار) است. جواب را paste کنید؛ برنامه اعتبارسنجی و ادغام می‌کند و برای خط‌های جاافتاده یا خراب فقط بسته‌ی همان خط‌ها را دوباره می‌سازد.

### پیشنهاد مدل بر اساس حافظه‌ی گرافیک

| حافظه‌ی گرافیک | مدل پیشنهادی |
|---|---|
| کمتر از ۶ گیگ | `gemma3:4b` |
| ۶ تا ۱۰ گیگ | `aya-expanse:8b` |
| ۱۰ تا ۱۶ گیگ | `gemma3:12b` |
| بیشتر از ۱۶ گیگ | `gemma3:27b` |

مدل‌های coder و مدل‌های زیر ۴ میلیارد پارامتر برای ترجمه پیشنهاد نمی‌شوند. دانلود مدل فقط با تأیید شما و با نمایش پیشرفت انجام می‌شود. اگر حجم کم باشد یا کارت گرافیک نباشد، روش C یا B پیشنهاد می‌شود.

### چسباندن حروف و راست‌به‌چپ (اختیاری، پیش‌فرض خاموش)

بعضی موتورهای بازی فارسی را نمی‌چسبانند و راست‌به‌چپ نمی‌کنند. گزینه‌ی `--shape` (یا تیک در رابط) با ماژول `htcore/shaper.py` حروف را به Presentation Forms تبدیل و ترتیب نمایش را برعکس می‌کند، طوری که اعداد و متن انگلیسی برعکس نشوند. کدهای بازی دست‌نخورده می‌مانند.

## راهنمای کوتاه موتورها

| موتور | درآوردن متن / برگرداندن |
|---|---|
| **Unity** | افزونه‌ی XUnity.AutoTranslator متن‌ها را در فایل `key=value` می‌ریزد (همین فرمت پشتیبانی می‌شود)؛ برای assets از UABEA. فونت باید حروف فارسی داشته باشد. |
| **Unreal** | `.locres` را با UnrealLocres به CSV تبدیل کنید و برگردانید؛ `.pak` را با repak/UnrealPak باز و ببندید. |
| **Ren'Py** | `renpy.sh <پروژه> translate persian` فایل‌های `tl/` می‌سازد؛ فقط مقدار `new` را عوض کنید. فونت فارسی در gui لازم است. |
| **RPG Maker** | فایل‌های `www/data/*.json` مستقیم قابل ترجمه‌اند؛ کدهای `\C[1]` و `\V[1]` قفل می‌شوند. |
| **Godot** | ترجمه‌ها معمولاً CSV یا PO هستند؛ برای `.pck` از GDRE Tools استفاده کنید. |

اگر بازی فونت فارسی ندارد، بعد از ترجمه به‌جای متن مربع یا علامت سؤال می‌بینید؛ فونت بازی را با فونتی که فارسی دارد (مثل Vazirmatn) عوض کنید.

## هشدار

دست‌کاری فایل‌های **بازی‌های آنلاین** ممکن است به **بن شدن حساب** بینجامد. قوانین و شرایط استفاده‌ی سازنده‌ی بازی را رعایت کنید و فقط روی بازی‌هایی کار کنید که اجازه‌ی تغییر فایل دارید. مسئولیت استفاده با خود شماست.

## تست

```bash
python -m unittest discover -s tests
```

تست‌ها از مترجم ساختگی استفاده می‌کنند و هیچ API واقعی صدا زده نمی‌شود.

لایسنس: MIT

---

<a id="english"></a>

# HomiTranslate (English)

A smart assistant for translating game text into Persian or any other language. Python 3 standard library only - nothing to install, no CDN. The UI is a single local web page served on `127.0.0.1` only.

## Features

- Structure-preserving formats: nested JSON (string values only), CSV (pick source/destination columns), PO/POT, line-by-line TXT, and `key=value` (XUnity.AutoTranslator style).
- The input file is never overwritten; results are written to `out/`.
- Inline codes (`{0}`, `%s`, `<color=...>`, `[b]`, `\n`, `{{...}}`, ...) are locked behind safe tokens. If the model loses or changes a code the line is retried up to 2 times, then flagged and the original text is kept.
- Automatic, editable glossary (proper nouns, repeated phrases, short menu strings) sent with every batch.
- sqlite cache and resume: duplicate lines are never translated twice and an interrupted run continues where it stopped.
- Web wizard: file → analysis → method suggestion → glossary → translate → review → export. Bilingual UI (Persian/English) with dark mode.

## Install & run

Python 3.9+ only.

```bash
git clone https://github.com/amir2139/HomiTranslate.git
cd HomiTranslate
python homitranslate.py
```

Everything is also available from the CLI (see the commands in the Persian section above). Options: `--target fa`, `--backend ollama|openai|mock`, `--model`, `--batch`, `--shape`.

## Three translation methods

| Method | Needs | Best for |
|---|---|---|
| **A. Local (Ollama)** | Ollama at `http://127.0.0.1:11434` and a GPU | big files, free and offline |
| **B. OpenAI-compatible API** | `base_url`, key, model (DeepSeek, OpenRouter, Gemini, ...), optional HTTP proxy | speed and quality |
| **C. Manual (no API)** | any free chat website | small files, no key |

- The API key lives only in `config.local.json`, which is git-ignored (see `config.example.json`).
- Method C splits the text into packets sized for free chat sites. Each packet is a ready-to-copy prompt (instructions + glossary + numbered lines). Paste the answer back: it is validated and merged, and a new packet is built for just the lines that were missed or broken.

### Model suggestion by VRAM

| VRAM | Suggested model |
|---|---|
| < 6 GB | `gemma3:4b` |
| 6-10 GB | `aya-expanse:8b` |
| 10-16 GB | `gemma3:12b` |
| > 16 GB | `gemma3:27b` |

Coder models and models under 4B parameters are never suggested for translation. Model downloads (`ollama pull`) happen only after you confirm, with progress. Small files or no GPU → method C or B is suggested.

### Letter shaping / RTL (optional, off by default)

For engines that cannot join Arabic-script letters or flow right-to-left, `--shape` (or the checkbox) converts letters to Presentation Forms and reorders them visually using `htcore/shaper.py`, keeping numbers and Latin text readable. Game codes are left untouched.

## Engine quick guide

| Engine | Extract / re-import |
|---|---|
| **Unity** | XUnity.AutoTranslator dumps a `key=value` file (supported directly); UABEA for assets. The font needs Persian glyphs. |
| **Unreal** | Convert `.locres` to CSV with UnrealLocres and back; unpack/repack `.pak` with repak/UnrealPak. |
| **Ren'Py** | `renpy.sh <project> translate persian` generates `tl/` files; replace only the `new` strings. A Persian font is required in the GUI. |
| **RPG Maker** | Translate `www/data/*.json` directly; `\C[1]`, `\V[1]` codes are locked. |
| **Godot** | Translations are usually CSV or PO; use GDRE Tools for `.pck`. |

If the game font has no Persian glyphs you will see boxes or question marks - replace the game font with one that supports Persian (e.g. Vazirmatn).

## Warning

Modifying the files of **online games** can get your **account banned**. Respect the game developer's terms and only modify games where file changes are allowed. Use at your own risk.

## Tests

```bash
python -m unittest discover -s tests
```

Tests use a fake translator; no real API is called.

License: MIT
