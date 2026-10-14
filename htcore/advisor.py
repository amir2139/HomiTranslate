"""Smart method advisor: GPU / Ollama detection, model suggestion, pull with progress, speed test."""
import json
import re
import subprocess
import time
import urllib.request

from . import engine
from .backends import BackendError, http_json
from .protect import protect

OLLAMA_URL = "http://127.0.0.1:11434"

# rough seconds per line on a matching GPU (used only before a real speed test)
ROUGH = {"gemma3:4b": 0.7, "aya-expanse:8b": 1.1, "gemma3:12b": 1.6, "gemma3:27b": 3.2}


def detect_gpu():
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=8)
        if r.returncode != 0 or not r.stdout.strip():
            return None
        best = None
        for ln in r.stdout.strip().splitlines():
            name, mem = [x.strip() for x in ln.rsplit(",", 1)]
            gb = round(float(mem) / 1024.0, 1)
            if best is None or gb > best["vram_gb"]:
                best = {"name": name, "vram_gb": gb}
        return best
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def ollama_status(url=OLLAMA_URL):
    try:
        data = http_json(url.rstrip("/") + "/api/tags", timeout=4, direct=True)
        return {"running": True, "models": [m["name"] for m in data.get("models", [])]}
    except Exception:
        return {"running": False, "models": []}


def suggest_model(vram_gb):
    if vram_gb < 6:
        return "gemma3:4b"
    if vram_gb < 10:
        return "aya-expanse:8b"
    if vram_gb <= 16:
        return "gemma3:12b"
    return "gemma3:27b"


def _param_billions(name):
    m = re.search(r"[:\-_](\d+(?:\.\d+)?)([bm])\b", name.lower())
    if not m:
        return None
    v = float(m.group(1))
    return v / 1000.0 if m.group(2) == "m" else v


def usable_for_translation(name):
    """Excludes coder / embedding / vision-only helpers and anything under 4B parameters."""
    low = name.lower()
    if any(w in low for w in ("coder", "embed", "code", "rerank", "cloud")):
        return False
    b = _param_billions(low)
    return b is None or b >= 4


def recommend(lines, cfg=None, gpu="auto", ollama="auto"):
    cfg = cfg or {}
    gpu = detect_gpu() if gpu == "auto" else gpu
    ol = ollama_status() if ollama == "auto" else ollama
    model = suggest_model(gpu["vram_gb"]) if gpu else None
    installed_ok = [m for m in ol["models"] if usable_for_translation(m)]
    use_installed = None
    if model:
        base = model.split(":")[0]
        same = [m for m in installed_ok if m.startswith(base)]
        use_installed = same[0] if same else None
    has_key = bool(cfg.get("openai", {}).get("api_key"))
    small = lines <= 150
    packets = max(1, lines // 45 + 1)
    per_line = ROUGH.get(model, 1.0) if model else None
    est = {
        "A": None if per_line is None else int(lines * per_line),
        "B": int(max(1, lines / 30) * 8),
        "C": packets * 150,
    }
    methods = []
    if small or not gpu:
        order = ["C", "B", "A"] if not has_key else ["B", "C", "A"]
    else:
        order = ["A", "B", "C"]
    why = {
        "A": {"fa": ("کارت %s با %s گیگ حافظه‌ی گرافیک پیدا شد؛ ترجمه‌ی محلی با Ollama بدون هزینه و آفلاین است." % (gpu["name"], gpu["vram_gb"])) if gpu else "کارت گرافیک NVIDIA پیدا نشد؛ ترجمه‌ی محلی روی CPU بسیار کند است.",
              "en": ("Found %s with %s GB VRAM; local translation with Ollama is free and offline." % (gpu["name"], gpu["vram_gb"])) if gpu else "No NVIDIA GPU found; local translation on CPU is very slow."},
        "B": {"fa": "API سازگار با OpenAI (DeepSeek، OpenRouter، Gemini ...) سریع و باکیفیت است ولی کلید لازم دارد." + (" کلید ذخیره شده است." if has_key else ""),
              "en": "An OpenAI-compatible API (DeepSeek, OpenRouter, Gemini ...) is fast and high quality but needs a key." + (" A key is saved." if has_key else "")},
        "C": {"fa": "روش دستی با سایت‌های چت رایگان؛ بدون کلید و بدون نصب. برای حجم کم عالی است.",
              "en": "Manual method with free chat websites; no key and no install. Great for small files."},
    }
    for rank, m in enumerate(order, 1):
        methods.append({"id": m, "rank": rank, "reason": why[m], "est_seconds": est[m]})
    notes = []
    if gpu and not ol["running"]:
        notes.append({"fa": "Ollama اجرا نمی‌شود یا نصب نیست؛ از ollama.com نصب و اجرا کنید.",
                      "en": "Ollama is not running or not installed; install and start it from ollama.com."})
    return {"gpu": gpu, "ollama": ol, "suggest_model": model, "installed_ok": installed_ok,
            "use_installed": use_installed, "needs_pull": bool(model and not use_installed),
            "methods": methods, "primary": order[0], "notes": notes}


def pull_model(model, on_progress, url=OLLAMA_URL):
    """Streams /api/pull. Call only after the user approved the download."""
    req = urllib.request.Request(url.rstrip("/") + "/api/pull",
                                 data=json.dumps({"model": model, "stream": True}).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=60) as r:
        for raw in r:
            if not raw.strip():
                continue
            d = json.loads(raw.decode("utf-8"))
            if "error" in d:
                raise BackendError(d["error"])
            total, done = d.get("total") or 0, d.get("completed") or 0
            on_progress(status=d.get("status", ""), done=done, total=total)


def speed_test(translator, target, sample_lines, total_lines):
    """Translate a few lines, return seconds/line and an estimate for the whole job."""
    sample = [s for s in sample_lines if s.strip()][:5]
    if not sample:
        return {"seconds_per_line": None, "estimate_seconds": None, "ok": 0, "n": 0}
    items = [(n + 1, protect(s)[0]) for n, s in enumerate(sample)]
    t0 = time.time()
    reply = translator.chat(engine.system_prompt(target), engine.user_prompt(items))
    dt = time.time() - t0
    got = engine.parse_items(reply)
    per = dt / len(sample)
    return {"seconds_per_line": round(per, 2), "estimate_seconds": int(per * total_lines),
            "ok": len(got), "n": len(sample), "sample": [got.get(i + 1, "") for i in range(len(sample))]}
