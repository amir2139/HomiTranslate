"""Lock inline codes ({0}, %s, <color=..>, [b], \\n ...) behind safe tokens and restore them."""
import re

_ALT = [
    r"\[\[\d+\]\]",
    r"\{\{.*?\}\}",
    r"\$\{[^{}\n]*\}",
    r"\{[^{}\n]*\}",
    r"%(?:\d+\$)?[-+#0]*\d*(?:\.\d+)?[sdifuxXcgeEo]",
    r"%%",
    r"</?[A-Za-z][^<>\n]*>",
    r"\[/?[a-z][a-z0-9]{0,15}(?:[= ][^\[\]\n]*)?\]",
    r"\\[A-Za-z]+\[[^\]\n]*\]",
    r"\\[nrt]",
    r"\\[.!|^$<>{}]",
    r"\r\n|[\r\n\t]",
]
CODE_RE = re.compile("(?:" + "|".join(_ALT) + ")+", re.S)
TOKEN_RE = re.compile(r"\[\[\s*(\d+)\s*\]\]")
NEWLINE_ONLY_RE = re.compile(r"^(?:\\n|\\r|\r\n|\r|\n)+$")


def protect(text):
    """Return (masked_text, codes). Adjacent codes are merged into one token."""
    codes = []

    def sub(m):
        codes.append(m.group(0))
        return "[[%d]]" % len(codes)

    return CODE_RE.sub(sub, text), codes


def restore(translated, codes):
    """Put codes back. Returns None when a token is missing, duplicated or invented."""
    found = sorted(int(n) for n in TOKEN_RE.findall(translated))
    if found != list(range(1, len(codes) + 1)):
        return None
    return TOKEN_RE.sub(lambda m: codes[int(m.group(1)) - 1], translated)


def has_letters(masked):
    return any(c.isalpha() for c in TOKEN_RE.sub("", masked))


def looks_technical(s):
    """Identifiers, paths, URLs: not worth translating."""
    s = s.strip()
    if not s or re.search(r"\s", s):
        return False
    return bool(re.search(r"[_/\\]", s) or re.match(r"^(https?:|www\.)", s)
                or re.match(r"^\w+\.\w{1,5}$", s) or re.match(r"^#?[0-9a-fA-F]{6,8}$", s))


def needs_translation(text):
    masked, _ = protect(text)
    return has_letters(masked) and not looks_technical(text)
