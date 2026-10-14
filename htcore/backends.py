"""Translation backends: Ollama (local), OpenAI-compatible API, mock (tests/demo)."""
import json
import time
import urllib.error
import urllib.request


class BackendError(Exception):
    pass


def http_json(url, payload=None, headers=None, proxy=None, timeout=600, method=None, direct=False):
    """POST/GET JSON. direct=True ignores any system proxy (used for localhost)."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method or ("POST" if data else "GET"))
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    if direct:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    elif proxy:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    else:
        opener = urllib.request.build_opener()
    with opener.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _retry(fn, tries=3):
    delay = 2
    for n in range(tries):
        try:
            return fn()
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:300]
            if e.code in (429, 500, 502, 503, 504) and n < tries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise BackendError("HTTP %d: %s" % (e.code, body))
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            if n < tries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise BackendError("connection failed: %s" % e)


class Ollama:
    name = "ollama"

    def __init__(self, url="http://127.0.0.1:11434", model="gemma3:4b", num_ctx=8192, **_):
        self.url = url.rstrip("/")
        self.model = model
        self.num_ctx = num_ctx

    def chat(self, system, user):
        payload = {"model": self.model, "stream": False, "format": "json",
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                   "options": {"temperature": 0.2, "num_ctx": self.num_ctx}}
        payload["think"] = False  # reasoning output would only slow translation down
        try:
            r = _retry(lambda: http_json(self.url + "/api/chat", payload, direct=True))
        except BackendError as e:
            if "think" not in str(e):
                raise
            payload.pop("think")
            r = _retry(lambda: http_json(self.url + "/api/chat", payload, direct=True))
        msg = r.get("message", {})
        return msg.get("content") or msg.get("thinking") or ""  # some reasoning models fill only "thinking"


class OpenAICompat:
    name = "openai"

    def __init__(self, base_url="", api_key="", model="", proxy="", **_):
        self.base = base_url.rstrip("/")
        self.key = api_key
        self.model = model
        self.proxy = proxy or None

    def chat(self, system, user):
        if not self.base or not self.model:
            raise BackendError("base_url / model not set")
        payload = {"model": self.model, "temperature": 0.2,
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        hdr = {"Authorization": "Bearer " + self.key} if self.key else {}
        r = _retry(lambda: http_json(self.base + "/chat/completions", payload, hdr, self.proxy, 300))
        try:
            return r["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise BackendError("unexpected response: %s" % str(r)[:200])


class Mock:
    """Toy translator for demos/tests: wraps each text in guillemets. Never calls the network."""
    name = "mock"

    def __init__(self, **_):
        pass

    def chat(self, system, user):
        import re
        items = []
        for m in re.finditer(r"^(\d+)\|\s?(.*)$", user.split("### LINES")[-1], re.M):
            items.append({"id": int(m.group(1)), "t": "«%s»" % m.group(2)})
        return json.dumps({"items": items}, ensure_ascii=False)


def make_backend(cfg):
    kind = cfg.get("backend", "ollama")
    if kind == "mock":
        return Mock()
    if kind == "openai":
        return OpenAICompat(**cfg.get("openai", {}))
    return Ollama(**cfg.get("ollama", {}))
