"""sqlite storage: global translation cache + per-file project (resume support)."""
import hashlib
import json
import sqlite3
import threading
from pathlib import Path

from . import formats


class _DB:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.cx = sqlite3.connect(str(path), check_same_thread=False)
        self.cx.row_factory = sqlite3.Row

    def run(self, sql, args=()):
        with self.lock:
            cur = self.cx.execute(sql, args)
            self.cx.commit()
            return cur

    def many(self, sql, rows):
        with self.lock:
            self.cx.executemany(sql, rows)
            self.cx.commit()

    def all(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.cx.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        r = self.all(sql, args)
        return r[0] if r else None

    def close(self):
        with self.lock:
            self.cx.close()


def _h(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


class Cache(_DB):
    """Same source line + same target language is never translated twice."""

    def __init__(self, path):
        super().__init__(path)
        self.run("CREATE TABLE IF NOT EXISTS cache(h TEXT, lang TEXT, tr TEXT, PRIMARY KEY(h, lang))")

    def get(self, src, lang):
        r = self.one("SELECT tr FROM cache WHERE h=? AND lang=?", (_h(src), lang))
        return r["tr"] if r else None

    def put(self, src, lang, tr):
        self.run("INSERT OR REPLACE INTO cache VALUES(?,?,?)", (_h(src), lang, tr))


class Project(_DB):
    """status: pending | done | edited | skip | flagged"""

    def __init__(self, path):
        super().__init__(path)
        self.run("CREATE TABLE IF NOT EXISTS lines(id INTEGER PRIMARY KEY, src TEXT, tr TEXT, status TEXT, note TEXT)")
        self.run("CREATE TABLE IF NOT EXISTS glossary(term TEXT PRIMARY KEY, tr TEXT, count INTEGER, user INTEGER DEFAULT 0)")
        self.run("CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT)")

    def meta(self, k, default=None):
        r = self.one("SELECT v FROM meta WHERE k=?", (k,))
        return json.loads(r["v"]) if r else default

    def set_meta(self, k, v):
        self.run("INSERT OR REPLACE INTO meta VALUES(?,?)", (k, json.dumps(v)))

    def init_lines(self, entries):
        if self.one("SELECT COUNT(*) c FROM lines")["c"]:
            return
        self.many("INSERT INTO lines VALUES(?,?,NULL,'pending','')", list(enumerate(entries)))

    def counts(self):
        out = {"total": 0, "pending": 0, "done": 0, "edited": 0, "skip": 0, "flagged": 0}
        for r in self.all("SELECT status, COUNT(*) c FROM lines GROUP BY status"):
            out[r["status"]] = r["c"]
            out["total"] += r["c"]
        return out

    def pending(self):
        return self.all("SELECT id, src FROM lines WHERE status='pending' ORDER BY id")

    def get(self, i):
        return self.one("SELECT * FROM lines WHERE id=?", (i,))

    def neighbors(self, lo, hi, n):
        before = self.all("SELECT src FROM lines WHERE id<? AND id>=? ORDER BY id", (lo, lo - n))
        after = self.all("SELECT src FROM lines WHERE id>? AND id<=? ORDER BY id", (hi, hi + n))
        return [r["src"] for r in before], [r["src"] for r in after]

    def set_tr(self, ids, tr, status, note=""):
        self.many("UPDATE lines SET tr=?, status=?, note=? WHERE id=?", [(tr, status, note, i) for i in ids])

    def reset(self, ids):
        self.many("UPDATE lines SET tr=NULL, status='pending', note='' WHERE id=?", [(i,) for i in ids])

    def reset_flagged(self):
        self.run("UPDATE lines SET tr=NULL, status='pending', note='' WHERE status='flagged'")

    def page(self, flt="all", q="", offset=0, limit=100):
        where, args = [], []
        if flt in ("flagged", "pending", "edited"):
            where.append("status='%s'" % flt)
        if q:
            where.append("(src LIKE ? OR tr LIKE ?)")
            args += ["%" + q + "%"] * 2
        w = (" WHERE " + " AND ".join(where)) if where else ""
        total = self.one("SELECT COUNT(*) c FROM lines" + w, args)["c"]
        rows = self.all("SELECT * FROM lines%s ORDER BY id LIMIT ? OFFSET ?" % w, args + [limit, offset])
        return total, rows

    def translations(self):
        return {r["id"]: r["tr"] for r in self.all(
            "SELECT id, tr FROM lines WHERE status IN ('done','edited') AND tr IS NOT NULL")}

    # glossary
    def gl_all(self):
        return self.all("SELECT term, tr, count, user FROM glossary ORDER BY count DESC, term")

    def gl_set(self, term, tr, count=None, user=0):
        old = self.one("SELECT count FROM glossary WHERE term=?", (term,))
        c = count if count is not None else (old["count"] if old else 0)
        self.run("INSERT OR REPLACE INTO glossary VALUES(?,?,?,?)", (term, tr, c, user))

    def gl_del(self, term):
        self.run("DELETE FROM glossary WHERE term=?", (term,))

    def gl_dict(self):
        return {g["term"]: g["tr"] for g in self.gl_all() if g["tr"]}


def open_project(path, target="fa", fmt=None, workdir=".homitranslate", data=None, **opt):
    path = Path(path)
    if data is None:
        data = path.read_bytes()
    fmt = fmt or formats.detect_format(path)
    doc = formats.load(path, data, fmt, target=target, **opt)
    key = _h(json.dumps([fmt, target, sorted(opt.items(), key=str)]) + hashlib.sha1(data).hexdigest())[:10]
    proj = Project(Path(workdir) / ("%s-%s.db" % (path.stem, key)))
    proj.init_lines(doc.entries)
    proj.set_meta("path", str(path))
    proj.set_meta("target", target)
    proj.set_meta("fmt", fmt)
    return proj, doc
