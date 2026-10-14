"""Batch translation with validation, retries, glossary, cache and resume."""
import json
import re

from . import glossary as gl
from .lang import lang_name
from .protect import protect, restore, needs_translation, TOKEN_RE

RULES = """You are a professional video-game localization translator.
Translate every line into {target}. The source language is detected automatically.
Rules:
- Keep every token like [[1]], [[2]] EXACTLY as written, once each, nothing added or removed. They are game codes (variables, tags, line breaks).
- Translate only the text around the tokens. Do not explain, do not add notes.
- Keep names and terms consistent with the glossary when one is given. Use natural, fluent wording that fits a game.
- Keep punctuation style, capitalization intent and line length reasonable for a game UI.
- Lines marked as context are for understanding only: never translate or return them.
Answer with JSON only, in this exact shape: {{"items":[{{"id":1,"t":"translation of line 1"}}, ...]}}
Return exactly one item for every numbered line, using the same ids."""


def system_prompt(target_code, glossary=None):
    s = RULES.format(target=lang_name(target_code))
    if glossary:
        s += "\n\n### GLOSSARY (source => required translation)\n" + "\n".join(
            "%s => %s" % (k, v) for k, v in glossary.items())
    return s


def user_prompt(items, before=(), after=()):
    """items: list of (local_id, masked_text). The numbered lines always come last."""
    parts = []
    if before:
        parts.append("### CONTEXT BEFORE (do not translate)\n" + "\n".join(_one_line(b) for b in before))
    if after:
        parts.append("### CONTEXT AFTER (do not translate)\n" + "\n".join(_one_line(a) for a in after))
    parts.append("### LINES\n" + "\n".join("%d| %s" % (i, t) for i, t in items))
    return "\n\n".join(parts)


def _one_line(s):
    return protect(s)[0].replace("\n", " ")[:200]


def parse_items(text):
    """Tolerant parser -> {local_id: translation}. Empty dict when nothing usable."""
    if not text:
        return {}
    t = text.strip()
    t = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", t)
    data = None
    for cand in (t, _outer(t, "{", "}"), _outer(t, "[", "]")):
        if cand:
            try:
                data = json.loads(cand)
                break
            except ValueError:
                continue
    out = {}
    if data is not None:
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            data = data["items"]
        elif isinstance(data, dict) and all(isinstance(v, (str, dict)) for v in data.values()):
            for k, v in data.items():
                if str(k).strip().isdigit() and isinstance(v, str):
                    out[int(k)] = v
            if out:
                return out
        if isinstance(data, list):
            for it in data:
                if isinstance(it, dict):
                    i = it.get("id", it.get("n"))
                    v = next((it[k] for k in ("t", "text", "tr", "translation") if k in it), None)
                    if i is not None and str(i).strip().isdigit() and isinstance(v, str):
                        out[int(i)] = v
            if out:
                return out
    for m in re.finditer(r'"id"\s*:\s*(\d+)\s*,\s*"(?:t|text|tr|translation)"\s*:\s*"((?:[^"\\]|\\.)*)"', t, re.S):
        try:
            out[int(m.group(1))] = json.loads('"%s"' % m.group(2))
        except ValueError:
            out[int(m.group(1))] = m.group(2)
    return out


def _outer(t, a, b):
    i, j = t.find(a), t.rfind(b)
    return t[i:j + 1] if 0 <= i < j else None


class Stopped(Exception):
    pass


class Engine:
    def __init__(self, project, translator, cache, target="fa", batch=30, ctx=3, retries=2,
                 max_chars=6000, progress=None, stop=None, glossary=None):
        self.p = project
        self.tr = translator
        self.cache = cache
        self.target = target
        self.batch = max(1, min(int(batch), 60))
        self.ctx = ctx
        self.retries = retries
        self.max_chars = max_chars
        self.progress = progress or (lambda **k: None)
        self.stop = stop
        self.glossary = project.gl_dict() if glossary is None else glossary
        self.stats = {"done": 0, "flagged": 0, "cached": 0, "skipped": 0, "calls": 0}

    # ------------------------------------------------------------ helpers
    def _check_stop(self):
        if self.stop is not None and self.stop.is_set():
            raise Stopped()

    def _emit(self, total, msg=""):
        self.progress(done=self.stats["done"] + self.stats["flagged"] + self.stats["cached"] + self.stats["skipped"],
                      total=total, flagged=self.stats["flagged"], msg=msg)

    def _ask(self, units):
        """units: list of (key, src). Returns {key: restored_translation or None} and error info."""
        prepared = []
        for key, src in units:
            masked, codes = protect(src)
            prepared.append((key, src, masked, codes))
        items = [(n + 1, p[2]) for n, p in enumerate(prepared)]
        first = min(p[0] for p in prepared)
        last = max(p[0] for p in prepared)
        before, after = self.p.neighbors(first, last, self.ctx) if self.ctx else ([], [])
        gloss = gl.relevant(self.glossary, [p[2] for p in prepared])
        self.stats["calls"] += 1
        reply = self.tr.chat(system_prompt(self.target, gloss), user_prompt(items, before, after))
        parsed = parse_items(reply)
        out = {}
        for n, (key, src, masked, codes) in enumerate(prepared, 1):
            raw = parsed.get(n)
            if raw is None or not raw.strip():
                out[key] = None
                continue
            out[key] = restore(raw.strip(), codes)
        return out

    # ------------------------------------------------------------ main
    def run(self):
        rows = self.p.pending()
        groups = {}
        order = []
        for r in rows:
            src = r["src"]
            if not needs_translation(src):
                self.p.set_tr([r["id"]], src, "skip")
                self.stats["skipped"] += 1
                continue
            hit = self.cache.get(src, self.target)
            if hit is not None:
                self.p.set_tr([r["id"]], hit, "done", "cache")
                self.stats["cached"] += 1
                continue
            if src not in groups:
                groups[src] = []
                order.append(src)
            groups[src].append(r["id"])
        total = len(rows)
        self._emit(total, "start")
        i = 0
        while i < len(order):
            self._check_stop()
            chunk, size = [], 0
            while i < len(order) and len(chunk) < self.batch and (not chunk or size + len(order[i]) < self.max_chars):
                chunk.append(order[i])
                size += len(order[i])
                i += 1
            self._translate_chunk(chunk, groups, total)
        self._emit(total, "finished")
        return self.stats

    def _translate_chunk(self, chunk, groups, total):
        todo = list(chunk)
        for attempt in range(1 + self.retries):
            if not todo:
                break
            self._check_stop()
            units = [(groups[s][0], s) for s in todo]
            result = self._ask(units)
            nxt = []
            for s in todo:
                tr = result.get(groups[s][0])
                if tr is None:
                    nxt.append(s)
                    continue
                self.p.set_tr(groups[s], tr, "done")
                self.cache.put(s, self.target, tr)
                self.stats["done"] += len(groups[s])
            todo = nxt
            self._emit(total)
        for s in todo:  # still broken after all attempts: keep original, mark
            self.p.set_tr(groups[s], s, "flagged", "codes/missing after %d tries" % (1 + self.retries))
            self.stats["flagged"] += len(groups[s])
        self._emit(total)

    def retranslate(self, line_id):
        """Force a fresh translation for one line (ignores cache)."""
        row = self.p.get(line_id)
        if not row:
            return None
        src = row["src"]
        for _ in range(1 + self.retries):
            tr = self._ask([(line_id, src)]).get(line_id)
            if tr is not None:
                self.p.set_tr([line_id], tr, "done")
                self.cache.put(src, self.target, tr)
                return tr
        self.p.set_tr([line_id], src, "flagged", "codes/missing after retries")
        return None


# ---------------------------------------------------------------- glossary translation
def translate_glossary(project, translator, target, progress=None, stop=None, chunk=40):
    """Translate glossary terms (entries without a user value). Terms are sent as plain lines."""
    terms = [g["term"] for g in project.gl_all() if not g["tr"] and not g["user"]]
    for k in range(0, len(terms), chunk):
        if stop is not None and stop.is_set():
            raise Stopped()
        part = terms[k:k + chunk]
        sys_p = ("You translate a glossary for a video game into %s. Proper names (people, places, items) should be "
                 "transliterated or translated the way a localized game would. Keep it short. "
                 'Answer JSON only: {"items":[{"id":1,"t":"..."}]} with one item per numbered term.'
                 % lang_name(target))
        user = "### LINES\n" + "\n".join("%d| %s" % (n + 1, t) for n, t in enumerate(part))
        got = parse_items(translator.chat(sys_p, user))
        for n, t in enumerate(part, 1):
            v = (got.get(n) or "").strip()
            if v and not TOKEN_RE.search(v):
                project.gl_set(t, v, user=0)
        if progress:
            progress(done=min(k + chunk, len(terms)), total=len(terms), msg="glossary")
