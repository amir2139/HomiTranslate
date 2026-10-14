"""Local web UI server: 127.0.0.1 only, single HTML page, JSON API."""
import base64
import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import advisor, analyze, config, engine, export, glossary, manual, __version__
from .backends import BackendError, make_backend
from .lang import LANGS
from .protect import protect
from .store import Cache, open_project

UI_PATH = Path(__file__).with_name("ui.html")
WORKDIR = Path(".homitranslate")
READ_ONLY = {"/api/state", "/api/job", "/api/glossary", "/api/lines", "/api/recommend"}


class App:
    def __init__(self):
        self.cfg = config.load()
        self.proj = None
        self.doc = None
        self.info = None
        self.lock = threading.RLock()
        self.job = {"kind": None, "state": "idle", "done": 0, "total": 0, "flagged": 0, "msg": "", "error": ""}
        self.stop = threading.Event()
        self.thread = None

    # -------------------------------------------------------------- helpers
    def cache(self):
        name = "mock-cache.db" if self.cfg["backend"] == "mock" else "cache.db"
        if not hasattr(self, "_cache") or self._cache_name != name:
            self._cache, self._cache_name = Cache(WORKDIR / name), name
        return self._cache

    def translator(self):
        return make_backend(self.cfg)

    def need_project(self):
        if not self.proj:
            raise ValueError("no file opened")
        return self.proj

    def start_job(self, kind, fn):
        with self.lock:
            if self.thread and self.thread.is_alive():
                raise ValueError("a job is already running")
            self.stop.clear()
            self.job = {"kind": kind, "state": "running", "done": 0, "total": 0, "flagged": 0, "msg": "", "error": ""}

            def run():
                try:
                    fn()
                    self.job["state"] = "done"
                except engine.Stopped:
                    self.job["state"] = "stopped"
                except Exception as e:  # shown in the UI; progress stays saved
                    self.job["state"] = "error"
                    self.job["error"] = str(e)

            self.thread = threading.Thread(target=run, daemon=True)
            self.thread.start()

    def progress(self, **k):
        for key in ("done", "total", "flagged", "msg"):
            if key in k:
                self.job[key] = k[key]

    # -------------------------------------------------------------- actions
    def open(self, body):
        target = body.get("target") or self.cfg["target"]
        opt = {k: body[k] for k in ("src", "dst", "delimiter") if body.get(k)}
        if body.get("content_b64"):
            name = Path(body.get("name") or "upload.txt").name
            up = WORKDIR / "uploads"
            up.mkdir(parents=True, exist_ok=True)
            path = up / name
            path.write_bytes(base64.b64decode(body["content_b64"]))
        else:
            path = Path(body["path"].strip().strip('"'))
            if not path.is_file():
                raise ValueError("file not found: %s" % path)
        proj, doc = open_project(path, target, body.get("fmt") or None, str(WORKDIR), **opt)
        self.proj, self.doc = proj, doc
        self.cfg["target"] = target
        self.info = analyze.analyze(path, doc)
        self.info["target"] = target
        self.info["font_warn"] = analyze.needs_font_warning(target)
        self.info["counts"] = proj.counts()
        self.info["glossary_size"] = len(proj.gl_all())
        return self.info

    def state(self):
        out = {"version": __version__, "cfg": config.public(self.cfg), "langs": {k: v for k, v in LANGS.items()},
               "job": self.job, "info": self.info}
        if self.proj:
            out["counts"] = self.proj.counts()
        return out

    def translate(self, body):
        p = self.need_project()
        target = p.meta("target")
        tr = self.translator()
        cache = self.cache()
        cfg = self.cfg

        def fn():
            if body.get("flagged_again"):
                p.reset_flagged()
            if any(not g["tr"] and not g["user"] for g in p.gl_all()) and not body.get("skip_glossary"):
                self.job["msg"] = "glossary"
                engine.translate_glossary(p, tr, target, self.progress, self.stop)
            eng = engine.Engine(p, tr, cache, target, batch=cfg["batch"], ctx=cfg["context"],
                                progress=self.progress, stop=self.stop)
            eng.run()

        self.start_job("translate", fn)

    def glossary_translate(self):
        p = self.need_project()
        tr = self.translator()
        target = p.meta("target")
        self.start_job("glossary", lambda: engine.translate_glossary(p, tr, target, self.progress, self.stop))

    def speedtest(self):
        p = self.need_project()
        sample = [r["src"] for r in p.pending()]
        sample = [s for s in sample if s.strip()][:5]
        return advisor.speed_test(self.translator(), p.meta("target"), sample, len(p.pending()))

    def pull(self, body):
        if body.get("confirm") is not True:
            raise ValueError("download needs explicit confirmation")
        model = body["model"]
        self.start_job("pull", lambda: advisor.pull_model(model, lambda status="", done=0, total=0: self.progress(
            done=done, total=total, msg=status)))

    def export(self, body):
        p = self.need_project()
        kind = body.get("kind", "file")
        out = body.get("out") or "out"
        if kind == "file":
            dest = export.export_file(p, self.doc, out, bool(body.get("shape", self.cfg["shape"])))
        elif kind == "texts":
            dest = export.export_texts_csv(p, out)
        elif kind == "glossary":
            dest = export.export_glossary(p, out)
        elif kind == "prompt":
            dest = export.export_prompt(p, p.meta("target"), out)
        else:
            raise ValueError("unknown export kind")
        return {"path": str(Path(dest).resolve())}


APP = App()


class Handler(BaseHTTPRequestHandler):
    server_version = "HomiTranslate"

    def log_message(self, *a):
        pass

    def _send(self, code, data, ctype="application/json; charset=utf-8"):
        body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _host_ok(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost"):
            return False
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).hostname not in ("127.0.0.1", "localhost"):
            return False
        return True

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, {"error": "forbidden"})
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            return self._send(200, UI_PATH.read_bytes(), "text/html; charset=utf-8")
        if u.path not in READ_ONLY:
            return self._send(405, {"error": "use POST"})
        self._api(u, {})

    def do_POST(self):
        if not self._host_ok() or self.headers.get("X-HT") != "1":
            return self._send(403, {"error": "forbidden"})
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except ValueError:
            return self._send(400, {"error": "bad json"})
        self._api(urlparse(self.path), body)

    def _api(self, u, body):
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            res = self.route(u.path, q, body)
            self._send(200, res if res is not None else {"ok": True})
        except KeyError as e:
            self._send(400, {"error": "missing field: %s" % e})
        except (ValueError, BackendError, OSError) as e:
            self._send(400, {"error": str(e)})
        except Exception as e:  # never kill the server on a bad request
            self._send(500, {"error": "%s: %s" % (type(e).__name__, e)})

    def route(self, path, q, body):
        A = APP
        if path == "/api/state":
            return A.state()
        if path == "/api/job":
            r = dict(A.job)
            if A.proj:
                r["counts"] = A.proj.counts()
            return r
        if path == "/api/config":
            config.update(A.cfg, body)
            return config.public(A.cfg)
        if path == "/api/open":
            return A.open(body)
        if path == "/api/recommend":
            n = len(A.proj.pending()) if A.proj else 0
            return advisor.recommend(n, A.cfg)
        if path == "/api/speedtest":
            return A.speedtest()
        if path == "/api/pull":
            return A.pull(body)
        if path == "/api/stop":
            A.stop.set()
            return None
        p = A.need_project()
        if path == "/api/glossary":
            return {"items": p.gl_all()}
        if path == "/api/glossary/extract":
            added = 0
            for term, c in glossary.extract(A.doc.entries):
                if not p.one("SELECT 1 FROM glossary WHERE term=?", (term,)):
                    p.gl_set(term, "", count=c)
                    added += 1
            return {"added": added}
        if path == "/api/glossary/save":
            p.gl_set(body["term"], body.get("tr", ""), user=1 if body.get("tr") else 0)
            return None
        if path == "/api/glossary/delete":
            p.gl_del(body["term"])
            return None
        if path == "/api/glossary/translate":
            return A.glossary_translate()
        if path == "/api/translate":
            return A.translate(body)
        if path == "/api/lines":
            total, rows = p.page(q.get("filter", "all"), q.get("q", ""), int(q.get("offset", 0)), int(q.get("limit", 100)))
            return {"total": total, "rows": rows}
        if path == "/api/line/save":
            p.set_tr([int(body["id"])], body["tr"], "edited")
            return None
        if path == "/api/line/retranslate":
            eng = engine.Engine(p, A.translator(), A.cache(), p.meta("target"), ctx=A.cfg["context"])
            eng.retranslate(int(body["id"]))
            return p.get(int(body["id"]))
        if path == "/api/manual/packets":
            return {"packets": manual.make_packets(p, p.meta("target"), int(body.get("max_chars", 7000)))}
        if path == "/api/manual/merge":
            return manual.merge(p, A.cache(), p.meta("target"), body["text"])
        if path == "/api/export":
            return A.export(body)
        raise ValueError("unknown endpoint: %s" % path)


def serve(port=8765, open_browser=True):
    srv = None
    for pt in range(port, port + 20):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", pt), Handler)
            break
        except OSError:
            continue
    if srv is None:
        raise SystemExit("no free port near %d" % port)
    url = "http://127.0.0.1:%d/" % srv.server_address[1]
    print("HomiTranslate running at", url, "(Ctrl+C to quit)", flush=True)
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
