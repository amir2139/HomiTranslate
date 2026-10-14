"""Optional Arabic-script shaping (Presentation Forms) + visual reordering for engines
without RTL support. Numbers and Latin text keep their left-to-right order. Off by default."""
import re
import unicodedata

from .protect import CODE_RE, NEWLINE_ONLY_RE

# Build the contextual-form table from Unicode data itself (no hand-written tables).
_FORMS = {}
for _cp in list(range(0xFB50, 0xFDFF)) + list(range(0xFE70, 0xFEFD)):
    _d = unicodedata.decomposition(chr(_cp))
    _m = re.match(r"<(isolated|initial|medial|final)> ([0-9A-F]{4})$", _d)
    if _m:
        _FORMS.setdefault(chr(int(_m.group(2), 16)), {})[_m.group(1)] = chr(_cp)
# lam + alef ligatures
_LIG = {}
for _cp in range(0xFEF5, 0xFEFD):
    _m = re.match(r"<(isolated|final)> 0644 (06[0-9A-F]{2})$", unicodedata.decomposition(chr(_cp)))
    if _m:
        _LIG.setdefault(chr(int(_m.group(2), 16)), {})[_m.group(1)] = chr(_cp)

_NL_SPLIT = re.compile(r"((?:\\n|\\r|\r\n|\r|\n)+)")
_ZWNJ = "‌"
_MARKS = set(chr(c) for c in range(0x064B, 0x0660)) | {"ٰ"}


def _dual(c):
    return c in _FORMS and "initial" in _FORMS[c]


def _joins_after(c):  # can connect to the following letter
    return _dual(c)


def _joins_before(c):  # can connect to the preceding letter
    return c in _FORMS and ("final" in _FORMS[c])


def join_letters(text):
    """Logical-order string -> string with Presentation Forms glyphs (still logical order)."""
    chars = list(text)
    out = []
    n = len(chars)
    i = 0

    def prev_letter(k):
        k -= 1
        while k >= 0 and chars[k] in _MARKS:
            k -= 1
        return chars[k] if k >= 0 else ""

    def next_letter(k):
        k += 1
        while k < n and chars[k] in _MARKS:
            k += 1
        return chars[k] if k < n else ""

    while i < n:
        c = chars[i]
        if c == _ZWNJ:
            i += 1
            continue
        if c not in _FORMS:
            out.append(c)
            i += 1
            continue
        p = prev_letter(i)
        join_prev = bool(p) and _joins_after(p) and _joins_before(c)
        if c == "ل":  # lam-alef
            j = i + 1
            while j < n and chars[j] in _MARKS:
                j += 1
            if j < n and chars[j] in _LIG:
                form = "final" if join_prev else "isolated"
                out.append(_LIG[chars[j]][form])
                out.extend(chars[i + 1:j])
                i = j + 1
                continue
        nx = next_letter(i)
        join_next = bool(nx) and _joins_after(c) and _joins_before(nx)
        form = ("medial" if join_prev and join_next else "final" if join_prev else
                "initial" if join_next else "isolated")
        out.append(_FORMS[c].get(form) or _FORMS[c].get("isolated") or c)
        i += 1
    return "".join(out)


def _is_rtl(c):
    o = ord(c)
    return 0x590 <= o <= 0x8FF or 0xFB1D <= o <= 0xFDFF or 0xFE70 <= o <= 0xFEFF


_MIRROR = str.maketrans("()[]{}<>«»", ")(][}{><»«")


def _reorder(text):
    """Logical (RTL paragraph) -> visual left-to-right string. Latin words/digits stay readable."""
    kinds = []
    for c in text:
        if _is_rtl(c):
            kinds.append("R")
        elif c.isalnum() or 0xE000 <= ord(c) <= 0xF8FF:
            kinds.append("L")
        else:
            kinds.append("N")
    # neutrals take the surrounding direction only between two L chars; otherwise RTL
    n = len(text)
    for i in range(n):
        if kinds[i] == "N":
            a = i - 1
            while a >= 0 and kinds[a] == "N":
                a -= 1
            b = i + 1
            while b < n and kinds[b] == "N":
                b += 1
            both_l = a >= 0 and b < n and kinds[a] == "L" and kinds[b] == "L"
            kinds[i] = "L" if both_l and text[i] in ".,:/-'’%" else "R"
    runs, cur, ck = [], "", None
    for c, k in zip(text, kinds):
        if k != ck and cur:
            runs.append((ck, cur))
            cur = ""
        cur += c
        ck = k
    if cur:
        runs.append((ck, cur))
    out = []
    for k, s in reversed(runs):
        out.append(s[::-1].translate(_MIRROR) if k == "R" else s)
    return "".join(out)


def shape_plain(text):
    return _reorder(join_letters(text)) if any(_is_rtl(c) for c in text) else text


def shape(text):
    """Shape a translated line that may contain game codes; codes stay intact (atomic, left-to-right)
    and real/escaped line breaks split the text into separately shaped visual lines."""
    pieces, last = [], 0
    for m in CODE_RE.finditer(text):
        if m.start() > last:
            pieces.append(("t", text[last:m.start()]))
        for part in _NL_SPLIT.split(m.group(0)):
            if part:
                pieces.append(("n" if NEWLINE_ONLY_RE.match(part) else "c", part))
        last = m.end()
    if last < len(text):
        pieces.append(("t", text[last:]))
    lines, cur = [], []
    for k, s in pieces:
        if k == "n":
            lines.append(cur)
            lines.append(s)
            cur = []
        else:
            cur.append((k, s))
    lines.append(cur)
    out = []
    for ln in lines:
        if isinstance(ln, str):
            out.append(ln)
            continue
        # one visual line: codes become single private-use chars (left-to-right units)
        holders, buf = {}, ""
        for k, s in ln:
            if k == "c" and len(holders) < 6000:
                ch = chr(0xE000 + len(holders))
                holders[ch] = s
                buf += ch
            else:
                buf += s
        res = shape_plain(buf)
        out.append("".join(holders.get(ch, ch) for ch in res))
    return "".join(out)
