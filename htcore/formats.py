"""Readers/writers that keep the original file structure and only swap the text."""
import copy
import csv
import io
import json
import re
from pathlib import Path


class Doc:
    """entries: list of source strings (index = line id). render(trans) -> bytes."""

    def __init__(self, fmt, entries, render_fn, encoding="utf-8", info=None):
        self.fmt = fmt
        self.entries = entries
        self._render = render_fn
        self.encoding = encoding
        self.info = info or {}

    def render(self, trans):
        text = self._render(trans)
        enc = self.encoding
        try:
            return text.encode(enc)
        except (UnicodeEncodeError, LookupError):
            return text.encode("utf-8")


def decode(data):
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        return data.decode("utf-16"), "utf-16"
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8"), "utf-8-sig"
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace"), "cp1252"


def _nl(text):
    return "\r\n" if "\r\n" in text else "\n"


# ---------------------------------------------------------------- JSON
def load_json(text, enc, **opt):
    data = json.loads(text)
    entries, paths = [], []

    def walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, path + [k])
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, path + [i])
        elif isinstance(node, str) and node.strip():
            entries.append(node)
            paths.append(path)

    walk(data, [])
    m = re.search(r"\n([ \t]+)\S", text)
    indent = m.group(1) if m else None
    compact = "\n" not in text.strip()
    ascii_ = "\\u" in text
    trailing = text.endswith("\n")

    def render(trans):
        d = copy.deepcopy(data)
        for i, tr in trans.items():
            p = paths[i]
            node = d
            for k in p[:-1]:
                node = node[k]
            node[p[-1]] = tr
        kw = {"ensure_ascii": ascii_}
        if indent is not None:
            kw["indent"] = indent
        elif compact:
            kw["separators"] = (",", ":")
        out = json.dumps(d, **kw)
        return out + ("\n" if trailing else "")

    return Doc("json", entries, render, enc)


# ---------------------------------------------------------------- CSV
_SRC_NAMES = ("text", "source", "original", "english", "en", "sourcestring", "string", "message", "msgid")


def _col(header, spec, default=None):
    if spec is None or spec == "":
        return default
    if isinstance(spec, int) or str(spec).isdigit():
        return int(spec)
    for i, h in enumerate(header):
        if h.strip().lower() == str(spec).strip().lower():
            return i
    raise ValueError("column not found: %s" % spec)


def load_csv(text, enc, delimiter=None, src=None, dst=None, target="fa", **opt):
    nl = _nl(text)
    if delimiter is None:
        try:
            delimiter = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|").delimiter
        except csv.Error:
            delimiter = ","
    rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter))
    if not rows:
        return Doc("csv", [], lambda t: text, enc)
    header = rows[0]
    body = rows[1:]
    sc = _col(header, src)
    if sc is None:
        for i, h in enumerate(header):
            if h.strip().lower() in _SRC_NAMES:
                sc = i
                break
    if sc is None:
        width = max(len(header), 1)
        avg = [sum(len(r[i]) for r in body if i < len(r)) for i in range(width)]
        sc = avg.index(max(avg))
    new_name = str(target)
    try:
        dc = _col(header, dst)
    except ValueError:
        dc, new_name = None, str(dst)
    if dc is None and not dst:
        for i, h in enumerate(header):
            if h.strip().lower() == str(target).lower():
                dc = i
    new_col = dc is None
    if new_col:
        dc = len(header)
    entries, rowidx = [], []
    for ri, r in enumerate(body):
        if sc < len(r) and r[sc].strip():
            entries.append(r[sc])
            rowidx.append(ri)

    def render(trans):
        out = [list(header) + ([new_name] if new_col else [])]
        for r in body:
            out.append(list(r))
        for i, tr in trans.items():
            r = out[rowidx[i] + 1]
            while len(r) <= dc:
                r.append("")
            r[dc] = tr
        if new_col:
            for r in out[1:]:
                while len(r) <= dc:
                    r.append("")
        buf = io.StringIO(newline="")
        w = csv.writer(buf, delimiter=delimiter, lineterminator=nl)
        w.writerows(out)
        return buf.getvalue()

    return Doc("csv", entries, render, enc, {"columns": header, "src": sc, "dst": dc, "delimiter": delimiter})


# ---------------------------------------------------------------- PO / POT
_PO_KW = re.compile(r'^(msgctxt|msgid_plural|msgid|msgstr\[(\d+)\]|msgstr)\s*(".*)?$')
_UNESC = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}


def _po_unquote(s):
    s = s.strip()
    if s.startswith('"') and s.endswith('"') and len(s) >= 2:
        s = s[1:-1]
    return re.sub(r"\\(.)", lambda m: _UNESC.get(m.group(1), "\\" + m.group(1)), s)


def _po_quote(text, nl):
    esc = text.replace("\\", "\\\\").replace('"', '\\"').replace("\t", "\\t").replace("\r", "\\r")
    parts = esc.split("\n")
    pieces = [p + "\\n" for p in parts[:-1]] + ([parts[-1]] if parts[-1] else [])
    if len(pieces) <= 1:
        return ['"%s"' % (pieces[0] if pieces else "")]
    return ['""'] + ['"%s"' % p for p in pieces]


def load_po(text, enc, **opt):
    nl = _nl(text)
    lines = text.splitlines()
    fields = []  # dict(kw, idx, start, end, value)
    cur = None
    for i, ln in enumerate(lines):
        s = ln.strip()
        m = _PO_KW.match(s)
        if m and not s.startswith("#"):
            kw = m.group(1) if not m.group(2) else "msgstr"
            cur = {"kw": kw, "n": int(m.group(2)) if m.group(2) else 0, "start": i, "end": i,
                   "value": _po_unquote(m.group(3)) if m.group(3) else ""}
            fields.append(cur)
        elif s.startswith('"') and cur:
            cur["value"] += _po_unquote(s)
            cur["end"] = i
        else:
            cur = None
    pos = []  # grouped entries
    ent = None
    for f in fields:
        if f["kw"] == "msgctxt":
            ent = {"ctx": f, "id": None, "plural": None, "str": []}
            pos.append(ent)
        elif f["kw"] == "msgid":
            if ent is None or ent["id"] is not None:
                ent = {"ctx": None, "id": None, "plural": None, "str": []}
                pos.append(ent)
            ent["id"] = f
        elif f["kw"] == "msgid_plural" and ent:
            ent["plural"] = f
        elif f["kw"] == "msgstr" and ent:
            ent["str"].append(f)
    entries, targets = [], []  # targets[i] = msgstr field to replace
    for e in pos:
        if not e["id"] or not e["id"]["value"].strip():
            continue
        for f in e["str"]:
            src = e["plural"]["value"] if (e["plural"] and f["n"] >= 1) else e["id"]["value"]
            entries.append(src)
            targets.append(f)

    def render(trans):
        repl = {}
        for i, tr in trans.items():
            f = targets[i]
            kw = "msgstr[%d]" % f["n"] if lines[f["start"]].lstrip().startswith("msgstr[") else "msgstr"
            q = _po_quote(tr, nl)
            new = ["%s %s" % (kw, q[0])] + q[1:]
            repl[f["start"]] = (f["end"], new)
        out, i = [], 0
        while i < len(lines):
            if i in repl:
                end, new = repl[i]
                out.extend(new)
                i = end + 1
            else:
                out.append(lines[i])
                i += 1
        return nl.join(out) + (nl if text.endswith(("\n", "\r")) else "")

    return Doc("po", entries, render, enc)


# ---------------------------------------------------------------- TXT
def load_txt(text, enc, **opt):
    raw = text.splitlines(keepends=True)
    entries, idx, parts = [], [], []
    for n, ln in enumerate(raw):
        body = ln.rstrip("\r\n")
        eol = ln[len(body):]
        m = re.match(r"^(\s*)(.*?)(\s*)$", body, re.S)
        parts.append((m.group(1), m.group(2), m.group(3) + eol))
        if m.group(2):
            entries.append(m.group(2))
            idx.append(n)

    def render(trans):
        out = [a + b + c for a, b, c in parts]
        for i, tr in trans.items():
            n = idx[i]
            out[n] = parts[n][0] + tr.replace("\r", "").replace("\n", " ") + parts[n][2]
        return "".join(out)

    return Doc("txt", entries, render, enc)


# ---------------------------------------------------------------- key=value (XUnity.AutoTranslator, .ini, .properties)
def _find_eq(s):
    i = 0
    while i < len(s):
        if s[i] == "\\":
            i += 2
            continue
        if s[i] == "=":
            return i
        i += 1
    return -1


def load_kv(text, enc, **opt):
    raw = text.splitlines(keepends=True)
    entries, idx, parts = [], [], []
    for n, ln in enumerate(raw):
        body = ln.rstrip("\r\n")
        eol = ln[len(body):]
        s = body.lstrip()
        eq = _find_eq(body)
        if not s or s[0] in "#;" or s.startswith("//") or (s[0] == "[" and s.endswith("]")) or eq < 0:
            parts.append(None)
            continue
        key, val = body[:eq], body[eq + 1:]
        lead = val[:len(val) - len(val.lstrip())]
        trail = val[len(val.rstrip()):] if val.strip() else ""
        v = val.strip()
        src = v if v else key.strip()
        parts.append((key + "=" + lead, trail + eol))
        if src:
            entries.append(src)
            idx.append(n)

    def render(trans):
        out = list(raw)
        for i, tr in trans.items():
            n = idx[i]
            a, b = parts[n]
            out[n] = a + tr.replace("\r", "").replace("\n", "\\n") + b
        return "".join(out)

    return Doc("kv", entries, render, enc)


LOADERS = {"json": load_json, "csv": load_csv, "po": load_po, "txt": load_txt, "kv": load_kv}
EXT = {".json": "json", ".csv": "csv", ".tsv": "csv", ".po": "po", ".pot": "po", ".txt": "txt",
       ".ini": "kv", ".kv": "kv", ".properties": "kv", ".lang": "kv", ".cfg": "kv"}


def detect_format(path):
    return EXT.get(Path(path).suffix.lower(), "txt")


def load(path, data=None, fmt=None, **opt):
    if data is None:
        data = Path(path).read_bytes()
    fmt = fmt or detect_format(path)
    if fmt not in LOADERS:
        raise ValueError("unknown format: %s" % fmt)
    text, enc = decode(data)
    if Path(path).suffix.lower() == ".tsv" and "delimiter" not in opt:
        opt["delimiter"] = "\t"
    return LOADERS[fmt](text, enc, **opt)
