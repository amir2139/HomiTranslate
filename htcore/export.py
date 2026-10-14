"""Write translated file (never over the input) and helper exports."""
import csv
import io
import json
from pathlib import Path

from . import engine, shaper


def export_file(project, doc, outdir="out", shape=False, src_path=None):
    src_path = Path(src_path or project.meta("path"))
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    dest = outdir / src_path.name
    if dest.resolve() == src_path.resolve():
        dest = outdir / ("translated_" + src_path.name)
    trans = project.translations()
    if shape:
        trans = {i: shaper.shape(t) for i, t in trans.items()}
    dest.write_bytes(doc.render(trans))
    return dest


def export_texts_csv(project, outdir="out", name=None):
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    dest = outdir / (name or (Path(project.meta("path")).stem + ".all_texts.csv"))
    buf = io.StringIO(newline="")
    w = csv.writer(buf)
    w.writerow(["id", "source", "translation", "status"])
    _, rows = project.page(limit=10 ** 9)
    for r in rows:
        w.writerow([r["id"], r["src"], r["tr"] or "", r["status"]])
    dest.write_text(buf.getvalue(), encoding="utf-8-sig", newline="")
    return dest


def export_glossary(project, outdir="out", name=None):
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    dest = outdir / (name or (Path(project.meta("path")).stem + ".glossary.json"))
    data = {g["term"]: g["tr"] for g in project.gl_all()}
    dest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return dest


def export_prompt(project, target, outdir="out", name=None):
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    dest = outdir / (name or (Path(project.meta("path")).stem + ".prompt.txt"))
    text = engine.system_prompt(target, project.gl_dict())
    text += ("\n\n### HOW TO USE\nSend this as the system prompt (or first message), then send lines in the form:\n"
             "1| first masked line\n2| second masked line\nCodes such as {0}, %s, <b> must be replaced with [[1]], [[2]] "
             "tokens before sending and restored afterwards; HomiTranslate does this for you.\n")
    dest.write_text(text, encoding="utf-8")
    return dest
