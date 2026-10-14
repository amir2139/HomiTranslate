"""Glossary extraction: proper nouns, repeated phrases, short menu strings."""
import re
from collections import Counter

from .protect import protect, looks_technical, TOKEN_RE

_STOP = {"the", "a", "an", "i", "you", "he", "she", "it", "we", "they", "this", "that", "these", "those",
         "and", "or", "but", "if", "is", "are", "was", "be", "to", "of", "in", "on", "at", "for", "with",
         "my", "your", "his", "her", "our", "their", "what", "where", "when", "who", "why", "how", "yes",
         "no", "ok", "oh", "well", "so", "then", "there", "here", "not", "do", "did", "don't", "can",
         "will", "let", "we'll", "i'm", "it's", "that's", "he's", "she's", "you're", "i'll", "let's", "just",
         "now", "all", "as", "by", "from", "no.", "hey", "please", "thanks", "sorry", "come", "get", "go"}
_CAP = re.compile(r"[A-Z][a-zA-Z'’-]+(?:\s+(?:of|the|de|von|van)?\s*[A-Z][a-zA-Z'’-]+)*")
_WORD = re.compile(r"[A-Za-z][A-Za-z'’-]+")


def _clean(t):
    return TOKEN_RE.sub(" ", protect(t)[0]).strip()


def extract(sources, max_terms=300, min_count=2):
    """Return list of (term, count), best candidates first."""
    texts = [_clean(s) for s in sources]
    names = Counter()
    mid = Counter()
    for t in texts:
        for m in _CAP.finditer(t):
            term = m.group(0).strip(" -'’")
            if term.lower() in _STOP or len(term) < 3:
                continue
            names[term] += 1
            if m.start() > 0 and not re.search(r"[.!?…:]\s*$", t[:m.start()]):
                mid[term] += 1
    out = {}
    for term, c in names.items():
        # proper nouns: seen mid-sentence, or a repeated multi-word name
        if mid[term] >= 1 or (" " in term and c >= min_count):
            out[term] = c
    # short menu-like strings
    shorts = Counter()
    for t in texts:
        w = t.split()
        if 1 <= len(w) <= 4 and len(t) <= 32 and not re.search(r"[.!?,;]$", t) and any(ch.isalpha() for ch in t) and not looks_technical(t):
            shorts[t] += 1
    for term, c in shorts.items():
        out.setdefault(term, c)
    # frequent phrases (2-3 words)
    grams = Counter()
    for t in texts:
        ws = [w.lower() for w in _WORD.findall(t)]
        for n in (2, 3):
            for i in range(len(ws) - n + 1):
                g = ws[i:i + n]
                if g[0] in _STOP or g[-1] in _STOP:
                    continue
                grams[" ".join(g)] += 1
    for term, c in grams.items():
        if c >= max(4, min_count + 2) and not any(term in k.lower() for k in out):
            out[term] = c
    ranked = sorted(out.items(), key=lambda kv: (-kv[1], kv[0].lower()))
    return ranked[:max_terms]


def relevant(glossary, texts, limit=80):
    """Glossary entries to put in a prompt: all when small, otherwise only those occurring in texts."""
    if len(glossary) <= limit:
        return dict(glossary)
    blob = "\n".join(texts).lower()
    hit = {k: v for k, v in glossary.items() if k.lower() in blob}
    return dict(list(hit.items())[:limit])
