"""Method C: no API. Build copy-paste packets for free chat sites and merge the pasted answers."""
import json
import re

from . import glossary as gl
from .engine import parse_items
from .lang import lang_name
from .protect import protect, restore, needs_translation

INSTRUCTIONS = """You are a professional video-game localization translator.
Translate every numbered line below into {target}. The source language is detected automatically.
STRICT RULES:
1. Reply with ONLY the translated lines, one per line, in this exact format: [ID] translation
   (keep the same IDs, keep the square brackets around the ID, one line for each ID, nothing else, no commentary).
2. Tokens like [[1]], [[2]] are game codes. Copy each one exactly as written, once each, in the translated line.
3. Do not translate the glossary terms differently from the glossary. Keep wording natural for a game.
4. Do not skip, merge or reorder lines. If a line has no letters to translate, repeat it as is.
{glossary}
LINES:
"""


def build_prompt(target, lines, glossary=None):
    """lines: list of (id, masked_text)."""
    g = ""
    if glossary:
        g = "\nGLOSSARY (source => required translation):\n" + "\n".join("%s => %s" % kv for kv in glossary.items()) + "\n"
    head = INSTRUCTIONS.format(target=lang_name(target), glossary=g)
    return head + "\n".join("[%d] %s" % (i, t) for i, t in lines) + "\n"


def make_packets(project, target, max_chars=7000, max_lines=60, only_ids=None):
    """Split pending lines into packets that fit a free chat box. Returns [{'ids': [...], 'prompt': str}]."""
    glossary = project.gl_dict()
    pending = []
    seen = {}
    for r in project.pending():
        if only_ids is not None and r["id"] not in only_ids:
            continue
        if not needs_translation(r["src"]):
            project.set_tr([r["id"]], r["src"], "skip")
            continue
        pending.append((r["id"], protect(r["src"])[0]))
    packets, cur, size = [], [], 0
    for item in pending:
        add = len(item[1]) + 8
        if cur and (size + add > max_chars or len(cur) >= max_lines):
            packets.append(cur)
            cur, size = [], 0
        cur.append(item)
        size += add
    if cur:
        packets.append(cur)
    out = []
    for pk in packets:
        g = gl.relevant(glossary, [t for _, t in pk])
        out.append({"ids": [i for i, _ in pk], "prompt": build_prompt(target, pk, g)})
    return out


def parse_answer(text):
    """Accept '[12] text' lines, '12| text', or the JSON shape used by the API methods."""
    out = {}
    t = re.sub(r"```[a-zA-Z]*", "", text)
    for ln in t.splitlines():
        m = re.match(r"^\s*(?:[-*•]\s*)?\[?(\d+)\]?\s*[|:.)\-–]?\s?(.*)$", ln)
        if m and re.match(r"^\s*(?:[-*•]\s*)?(\[\d+\]|\d+\s*[|:.)\-–])", ln):
            out[int(m.group(1))] = m.group(2).strip()
    if not out:
        out = parse_items(text)
    return out


def merge(project, cache, target, text):
    """Validate pasted answer, store good lines. Returns report dict."""
    answer = parse_answer(text)
    ok, bad, unknown = [], [], []
    for i, tr in answer.items():
        row = project.get(i)
        if not row:
            unknown.append(i)
            continue
        if row["status"] != "pending":
            continue
        _, codes = protect(row["src"])
        res = restore(tr, codes) if tr else None
        if res is None:
            bad.append(i)
            continue
        project.set_tr([i], res, "done", "manual")
        cache.put(row["src"], target, res)
        ok.append(i)
    missing = [r["id"] for r in project.pending() if needs_translation(r["src"]) and r["id"] not in answer
               and r["id"] not in bad]
    return {"ok": len(ok), "bad": bad, "unknown": unknown, "missing_in_answer": missing,
            "remaining": len([r for r in project.pending() if needs_translation(r["src"])])}
