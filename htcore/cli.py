"""Command line interface. Everything the web UI does is available here."""
import argparse
import json
import sys
import threading
from pathlib import Path

from . import advisor, analyze, config, engine, export, glossary, manual, __version__
from .backends import BackendError, make_backend
from .store import Cache, open_project


def _out(*a):
    print(*a, flush=True)


def _common(p):
    p.add_argument("file")
    p.add_argument("--format", choices=["json", "csv", "po", "txt", "kv"], help="default: guessed from extension")
    p.add_argument("--target", help="target language code (default from config, fa)")
    p.add_argument("--src-col", help="CSV: source column (name or number)")
    p.add_argument("--dst-col", help="CSV: destination column (name or number); created if missing")
    p.add_argument("--delimiter", help="CSV delimiter")
    p.add_argument("--out", default="out", help="output folder (default: out)")
    p.add_argument("--workdir", default=".homitranslate")


def _backend_opts(p):
    p.add_argument("--backend", choices=["ollama", "openai", "mock"], help="override config backend")
    p.add_argument("--model", help="override model name")
    p.add_argument("--batch", type=int)
    p.add_argument("--shape", action="store_true", help="join Arabic letters + visual RTL order (for engines without RTL)")


def _setup(a, cfg):
    target = a.target or cfg["target"]
    opt = {}
    for k in ("src_col", "dst_col", "delimiter"):
        v = getattr(a, k, None)
        if v:
            opt[{"src_col": "src", "dst_col": "dst"}.get(k, k)] = v
    proj, doc = open_project(a.file, target, a.format, a.workdir, **opt)
    return proj, doc, target


def _translator(a, cfg):
    if getattr(a, "backend", None):
        cfg["backend"] = a.backend
    if getattr(a, "model", None):
        key = "openai" if cfg["backend"] == "openai" else "ollama"
        cfg[key]["model"] = a.model
    return make_backend(cfg)


def _bar(done=0, total=0, msg="", flagged=0, **_):
    if total:
        w = 30
        n = int(w * done / total)
        sys.stdout.write("\r[%s%s] %d/%d  flagged:%d %s" % ("#" * n, "." * (w - n), done, total, flagged, msg[:20]))
        sys.stdout.flush()


def cmd_analyze(a, cfg):
    proj, doc, target = _setup(a, cfg)
    text = Path(a.file).read_text(encoding="utf-8", errors="ignore")
    info = analyze.analyze(a.file, doc, text)
    _out(json.dumps(info, ensure_ascii=False, indent=2))


def cmd_recommend(a, cfg):
    n = 0
    if a.file:
        proj, doc, _ = _setup(a, cfg)
        n = len(doc.entries)
    _out(json.dumps(advisor.recommend(n, cfg), ensure_ascii=False, indent=2))


def cmd_pull(a, cfg):
    _out("Downloading %s (you approved this by running the command) ..." % a.model)
    advisor.pull_model(a.model, lambda status="", done=0, total=0: _bar(done, total, status))
    _out("\ndone")


def cmd_glossary(a, cfg):
    proj, doc, target = _setup(a, cfg)
    if a.import_file:
        data = json.loads(Path(a.import_file).read_text(encoding="utf-8"))
        for k, v in data.items():
            proj.gl_set(k, v or "", user=1 if v else 0)
    if a.extract or not proj.gl_all():
        for term, c in glossary.extract(doc.entries):
            if not proj.one("SELECT 1 FROM glossary WHERE term=?", (term,)):
                proj.gl_set(term, "", count=c)
    if a.translate:
        engine.translate_glossary(proj, _translator(a, cfg), target, lambda **k: _bar(**k))
        _out("")
    for g in proj.gl_all():
        _out("%4d  %-40s => %s" % (g["count"], g["term"], g["tr"]))
    if a.export:
        _out("saved", export.export_glossary(proj, a.out))


def cmd_translate(a, cfg):
    proj, doc, target = _setup(a, cfg)
    tr = _translator(a, cfg)
    cache = Cache(Path(a.workdir) / ("mock-cache.db" if cfg["backend"] == "mock" else "cache.db"))
    if not proj.gl_all() and not a.no_glossary:
        for term, c in glossary.extract(doc.entries):
            proj.gl_set(term, "", count=c)
    stop = threading.Event()
    try:
        if not a.no_glossary and any(not g["tr"] and not g["user"] for g in proj.gl_all()):
            _out("Translating glossary first ...")
            engine.translate_glossary(proj, tr, target, lambda **k: _bar(**k), stop)
            _out("")
        eng = engine.Engine(proj, tr, cache, target, batch=a.batch or cfg["batch"], ctx=cfg["context"],
                            progress=_bar, stop=stop)
        stats = eng.run()
    except KeyboardInterrupt:
        stop.set()
        _out("\nStopped. Run the same command again to resume.")
        return 1
    except BackendError as e:
        _out("\nBackend error: %s\nProgress is saved; run the same command again to resume." % e)
        return 1
    _out("\n%s" % json.dumps(stats))
    dest = export.export_file(proj, doc, a.out, a.shape or cfg["shape"])
    _out("saved", dest)
    c = proj.counts()
    _out("report: done=%d cached=%d skipped=%d flagged=%d" % (c["done"], stats["cached"], c["skip"], c["flagged"]))


def cmd_manual(a, cfg):
    proj, doc, target = _setup(a, cfg)
    cache = Cache(Path(a.workdir) / "cache.db")
    if a.merge:
        rep = manual.merge(proj, cache, target, Path(a.merge).read_text(encoding="utf-8"))
        _out(json.dumps(rep, ensure_ascii=False))
        if rep["remaining"]:
            _out("%d lines still need translation: run --packets again." % rep["remaining"])
        else:
            _out("saved", export.export_file(proj, doc, a.out, a.shape or cfg["shape"]))
        return
    outdir = Path(a.out) / "packets"
    outdir.mkdir(parents=True, exist_ok=True)
    pk = manual.make_packets(proj, target, a.max_chars)
    for n, p in enumerate(pk, 1):
        (outdir / ("packet_%03d.txt" % n)).write_text(p["prompt"], encoding="utf-8")
    _out("%d packets written to %s" % (len(pk), outdir))


def cmd_export(a, cfg):
    proj, doc, target = _setup(a, cfg)
    kind = a.kind
    if kind == "file":
        _out("saved", export.export_file(proj, doc, a.out, a.shape or cfg["shape"]))
    elif kind == "texts":
        _out("saved", export.export_texts_csv(proj, a.out))
    elif kind == "glossary":
        _out("saved", export.export_glossary(proj, a.out))
    elif kind == "prompt":
        _out("saved", export.export_prompt(proj, target, a.out))


def cmd_serve(a, cfg):
    from . import server
    server.serve(port=a.port, open_browser=not a.no_browser)


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="homitranslate", description="HomiTranslate %s - smart game text translator" % __version__)
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("serve", help="start the local web UI (default)")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("analyze", help="file statistics + engine guess")
    _common(p)
    p.set_defaults(fn=cmd_analyze)

    p = sub.add_parser("recommend", help="GPU/Ollama check and method suggestion")
    p.add_argument("file", nargs="?")
    p.add_argument("--format")
    p.add_argument("--target")
    p.add_argument("--src-col")
    p.add_argument("--dst-col")
    p.add_argument("--delimiter")
    p.add_argument("--out", default="out")
    p.add_argument("--workdir", default=".homitranslate")
    p.set_defaults(fn=cmd_recommend)

    p = sub.add_parser("pull", help="download an Ollama model (explicit command = your approval)")
    p.add_argument("model")
    p.set_defaults(fn=cmd_pull)

    p = sub.add_parser("glossary", help="extract / show / translate / import / export the glossary")
    _common(p)
    _backend_opts(p)
    p.add_argument("--extract", action="store_true")
    p.add_argument("--translate", action="store_true", help="translate empty glossary entries with the backend")
    p.add_argument("--import-file", help="JSON {term: translation} to merge in as your own values")
    p.add_argument("--export", action="store_true")
    p.set_defaults(fn=cmd_glossary)

    p = sub.add_parser("translate", help="glossary + translation + save to out/ (resumes automatically)")
    _common(p)
    _backend_opts(p)
    p.add_argument("--no-glossary", action="store_true")
    p.set_defaults(fn=cmd_translate)

    p = sub.add_parser("manual", help="method C: build packets (--packets) or merge a pasted answer (--merge FILE)")
    _common(p)
    p.add_argument("--packets", action="store_true")
    p.add_argument("--merge")
    p.add_argument("--max-chars", type=int, default=7000)
    p.add_argument("--shape", action="store_true")
    p.set_defaults(fn=cmd_manual)

    p = sub.add_parser("export", help="file | texts | glossary | prompt")
    _common(p)
    p.add_argument("kind", choices=["file", "texts", "glossary", "prompt"])
    p.add_argument("--shape", action="store_true")
    p.set_defaults(fn=cmd_export)

    a = ap.parse_args(argv)
    if not a.cmd:
        a = ap.parse_args(["serve"])
    return a.fn(a, config.load()) or 0
