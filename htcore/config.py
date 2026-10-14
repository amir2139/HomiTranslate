"""Settings. API keys live only in config.local.json (git-ignored)."""
import copy
import json
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = APP_DIR / "config.local.json"
DEFAULTS = {
    "backend": "ollama",          # ollama | openai | mock
    "target": "fa",
    "batch": 30,
    "context": 3,
    "shape": False,
    "ollama": {"url": "http://127.0.0.1:11434", "model": "gemma3:4b"},
    "openai": {"base_url": "https://api.deepseek.com", "api_key": "", "model": "deepseek-chat", "proxy": ""},
}


def _merge(base, over):
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def load(path=CONFIG_PATH):
    cfg = copy.deepcopy(DEFAULTS)
    try:
        _merge(cfg, json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return cfg


def save(cfg, path=CONFIG_PATH):
    Path(path).write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def public(cfg):
    """Copy safe to send to the browser: the key is replaced by a flag."""
    c = copy.deepcopy(cfg)
    c["openai"]["has_key"] = bool(c["openai"].get("api_key"))
    c["openai"]["api_key"] = ""
    return c


def update(cfg, new):
    """Apply browser changes; an empty api_key means 'keep the saved one'."""
    new = copy.deepcopy(new)
    if "openai" in new and not new["openai"].get("api_key"):
        new["openai"].pop("api_key", None)
    new["openai"].pop("has_key", None) if "openai" in new else None
    _merge(cfg, new)
    save(cfg)
    return cfg
