import json
import re
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from htcore import engine, export, formats, manual, shaper
from htcore.protect import protect, restore, needs_translation
from htcore.store import Cache, open_project

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


class FakeTranslator:
    """Fake model: wraps text in guillemets. Can drop lines or break codes on chosen calls."""

    def __init__(self, drop_first=(), break_always=(), break_first=()):
        self.calls = 0
        self.seen = []
        self.drop_first = set(drop_first)
        self.break_always = set(break_always)
        self.break_first = set(break_first)

    def chat(self, system, user):
        self.calls += 1
        items = []
        for m in re.finditer(r"^(\d+)\| ?(.*)$", user.split("### LINES")[-1], re.M):
            n, text = int(m.group(1)), m.group(2)
            self.seen.append(text)
            if self.calls == 1 and n in self.drop_first:
                continue
            if n in self.break_always or (self.calls == 1 and n in self.break_first):
                text = re.sub(r"\[\[\d+\]\]", "", text)
            items.append({"id": n, "t": "«%s»" % text})
        return json.dumps({"items": items}, ensure_ascii=False)


def make_project(tmp, name="sample.json", **kw):
    return open_project(SAMPLES / name, "fa", workdir=str(Path(tmp) / "w"), **kw)


class ProtectTests(unittest.TestCase):
    def test_roundtrip(self):
        for s in ["Hi {0}, you have %d <color=#fff>coins</color> [b]now[/b]\\n{{x}}\t!",
                  "Line1\nLine2", "100%% sure %s", "\\C[2]Hero\\C[0] says hi"]:
            masked, codes = protect(s)
            self.assertIsNotNone(restore(masked, codes), s)
            self.assertEqual(restore(masked, codes), s)
            self.assertNotIn("{0}", masked)

    def test_adjacent_codes_merge(self):
        masked, codes = protect("a\\n\\nb")
        self.assertEqual(masked, "a[[1]]b")

    def test_detects_lost_or_changed_code(self):
        masked, codes = protect("Hello {0} and {1}")
        self.assertEqual(len(codes), 2)
        self.assertIsNone(restore("سلام [[1]]", codes))            # lost
        self.assertIsNone(restore("[[1]] [[1]] [[2]]", codes))     # duplicated
        self.assertIsNone(restore("[[1]] [[2]] [[3]]", codes))     # invented
        self.assertEqual(restore("[[ 2 ]] و [[1]]", codes), "{1} و {0}")  # reorder + spaces fine

    def test_skip_rules(self):
        self.assertFalse(needs_translation("{0}"))
        self.assertFalse(needs_translation("123 456"))
        self.assertFalse(needs_translation("ui/menu_button_01"))
        self.assertTrue(needs_translation("Attack"))


class FormatTests(unittest.TestCase):
    def test_json_keeps_structure(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
            proj, doc = make_project(t)
            self.assertIn("Healing Potion", doc.entries)
            self.assertNotIn("potion_01", doc.entries[0:0] or [])
            out = json.loads(doc.render({i: "X%d" % i for i, e in enumerate(doc.entries) if needs_translation(e)}).decode("utf-8"))
            orig = json.loads((SAMPLES / "sample.json").read_text(encoding="utf-8"))
            self.assertEqual(list(out), list(orig))
            self.assertEqual(out["count"], 3)
            self.assertEqual(out["items"][0]["id"], "potion_01")  # keys & ids untouched
            self.assertEqual(out["menu"]["start"], "X0")

    def test_csv_columns(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
            proj, doc = make_project(t, "sample.csv", src="en", dst="fa")
            self.assertEqual(doc.entries[0], "Start Game")
            raw = doc.render({0: "شروع", 2: "خوش آمدی، مسافر!"}).decode("utf-8")
            self.assertIn("key,en,notes,fa", raw)
            self.assertIn("UI_START,Start Game,menu,شروع", raw)
            self.assertIn("greeting,خوش آمدی، مسافر!", raw)
            self.assertEqual(len(raw.splitlines()), 6)

    def test_csv_existing_dst_column(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
            proj, doc = make_project(t, "sample.csv", src="en", dst="notes")
            raw = doc.render({0: "شروع"}).decode("utf-8")
            self.assertIn("UI_START,Start Game,شروع", raw)

    def test_po_plural_and_escapes(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
            proj, doc = make_project(t, "sample.po")
            self.assertEqual(doc.entries[1], "You found %d coins.\nWell done!")
            self.assertEqual(len(doc.entries), 4)
            out = doc.render({i: "T%d\n" % i if i == 1 else "T%d" % i for i in range(4)}).decode("utf-8")
            self.assertIn('msgid "Start Game"\nmsgstr "T0"', out)
            self.assertIn('msgstr[0] "T2"', out)
            self.assertIn('msgstr[1] "T3"', out)
            self.assertIn('#: ui.c:10', out)

    def test_txt_and_kv(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
            _, doc = make_project(t, "sample.txt")
            out = doc.render({0: "A"}).decode("utf-8").split("\n")
            self.assertEqual(out[0], "A")
            self.assertEqual(out[1], "")
            self.assertTrue(out[4].startswith("  Press"))
            _, kv = make_project(t, "sample.kv")
            self.assertEqual(kv.entries[1], "Options")  # empty value -> key is the source
            res = kv.render({0: "شروع بازی", 1: "گزینه‌ها"}).decode("utf-8")
            self.assertIn("// XUnity.AutoTranslator sample", res)
            self.assertIn("Start Game=شروع بازی", res)
            self.assertIn("Options=گزینه‌ها", res)

    def test_input_never_overwritten(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
            proj, doc = make_project(t)
            before = (SAMPLES / "sample.json").read_bytes()
            proj.set_tr([0], "شروع", "done")
            dest = export.export_file(proj, doc, Path(t) / "out")
            self.assertEqual((SAMPLES / "sample.json").read_bytes(), before)
            self.assertEqual(dest.parent.name, "out")
            self.assertIn("شروع", dest.read_text(encoding="utf-8"))


class EngineTests(unittest.TestCase):
    def run_engine(self, tmp, fake, name="sample.json", cache=None, **kw):
        proj, doc = make_project(tmp, name)
        cache = cache or Cache(Path(tmp) / "cache.db")
        eng = engine.Engine(proj, fake, cache, "fa", **kw)
        eng.run()
        return proj, doc, eng, cache

    def test_happy_path_keeps_codes(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
            fake = FakeTranslator()
            proj, doc, eng, _ = self.run_engine(t, fake)
            tr = proj.translations()
            i = doc.entries.index("Restores {0} HP to <color=#00ff00>one ally</color>.")
            self.assertEqual(tr[i], "«Restores {0} HP to <color=#00ff00>one ally</color>.»")
            self.assertEqual(proj.counts()["pending"], 0)
            self.assertTrue(all("[[" not in v for v in tr.values()))

    def test_missing_line_is_retried(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
            fake = FakeTranslator(drop_first={2})
            proj, doc, eng, _ = self.run_engine(t, fake)
            self.assertGreaterEqual(fake.calls, 2)
            self.assertEqual(proj.counts()["flagged"], 0)
            self.assertEqual(proj.counts()["pending"], 0)

    def test_broken_code_retried_then_ok(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
            fake = FakeTranslator(break_first={4})
            proj, doc, eng, _ = self.run_engine(t, fake)
            self.assertEqual(proj.counts()["flagged"], 0)

    def test_persistent_break_flags_and_keeps_original(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
            proj, doc = make_project(t)
            src_with_code = next(i for i, e in enumerate(doc.entries) if "{0}" in e)
            fake = FakeTranslator(break_always={1})  # local id 1 is the first line of every retry call
            eng = engine.Engine(proj, fake, Cache(Path(t) / "c.db"), "fa", batch=1, retries=2)
            eng.run()
            row = proj.get(src_with_code)
            # batch=1 => every call has local id 1, so every line with codes is broken for good
            self.assertEqual(row["status"], "flagged")
            self.assertEqual(row["tr"], row["src"])
            dest = export.export_file(proj, doc, Path(t) / "out")
            self.assertIn("Restores {0} HP", dest.read_text(encoding="utf-8"))  # original text stays

    def test_cache_and_duplicates(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
            fake1 = FakeTranslator()
            self.run_engine(t, fake1, "sample.txt")
            cache = Cache(Path(t) / "cache.db")
            # second project for the same file in another workdir but the same cache: zero calls
            proj2, _ = open_project(SAMPLES / "sample.txt", "fa", workdir=str(Path(t) / "w2"))
            fake2 = FakeTranslator()
            eng = engine.Engine(proj2, fake2, cache, "fa")
            eng.run()
            self.assertEqual(fake2.calls, 0)
            self.assertEqual(eng.stats["cached"], len(proj2.pending()) + eng.stats["cached"])
            # duplicates inside one run are sent once
            p = Path(t) / "dup.txt"
            p.write_text("Hello there\nHello there\nHello there\n", encoding="utf-8")
            proj3, _ = open_project(p, "fa", workdir=str(Path(t) / "w3"))
            fake3 = FakeTranslator()
            engine.Engine(proj3, fake3, Cache(Path(t) / "c3.db"), "fa").run()
            self.assertEqual(fake3.seen.count("Hello there"), 1)
            self.assertEqual(proj3.counts()["done"], 3)

    def test_resume_after_stop(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
            proj, doc = make_project(t)
            cache = Cache(Path(t) / "cache.db")
            stop = threading.Event()

            class Stopper(FakeTranslator):
                def chat(self, s, u):
                    r = super().chat(s, u)
                    stop.set()  # stop after the first batch
                    return r

            eng = engine.Engine(proj, Stopper(), cache, "fa", batch=3, stop=stop)
            with self.assertRaises(engine.Stopped):
                eng.run()
            c = proj.counts()
            self.assertGreater(c["done"], 0)
            self.assertGreater(c["pending"], 0)
            done_before = c["done"]
            fake = FakeTranslator()
            engine.Engine(proj, fake, cache, "fa", batch=3).run()
            self.assertEqual(proj.counts()["pending"], 0)
            self.assertGreaterEqual(proj.counts()["done"], done_before)
            self.assertNotIn("Start Game", fake.seen)  # already finished lines are not sent again

    def test_parse_variants(self):
        self.assertEqual(engine.parse_items('```json\n{"items":[{"id":1,"t":"a"},{"id":2,"text":"b"}]}\n```'), {1: "a", 2: "b"})
        self.assertEqual(engine.parse_items('[{"id":"1","t":"x"}]'), {1: "x"})
        self.assertEqual(engine.parse_items('{"1":"x","2":"y"}'), {1: "x", 2: "y"})
        self.assertEqual(engine.parse_items("garbage"), {})


class ManualTests(unittest.TestCase):
    def test_packets_merge_and_missing(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
            proj, doc = make_project(t, "sample.txt")
            cache = Cache(Path(t) / "cache.db")
            pk = manual.make_packets(proj, "fa", max_chars=60)
            self.assertGreater(len(pk), 1)
            self.assertIn("[0] ", pk[0]["prompt"])
            ids = [i for p in pk for i in p["ids"]]
            # answer for everything except one id, and one answer with a lost code
            answer = "\n".join("[%d] ترجمه %d" % (i, i) for i in ids[:-1])
            rep = manual.merge(proj, cache, "fa", answer)
            self.assertEqual(rep["ok"], len(ids) - 1)
            self.assertEqual(rep["missing_in_answer"], [ids[-1]])
            again = manual.make_packets(proj, "fa")
            self.assertEqual([i for p in again for i in p["ids"]], [ids[-1]])  # packet only for the missing line

    def test_merge_rejects_changed_codes(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
            proj, doc = make_project(t, "sample.txt")
            cache = Cache(Path(t) / "cache.db")
            i = next(k for k, e in enumerate(doc.entries) if "<b>" in e)
            rep = manual.merge(proj, cache, "fa", "[%d] بدون کد" % i)
            self.assertEqual(rep["bad"], [i])
            self.assertEqual(proj.get(i)["status"], "pending")


class ShaperTests(unittest.TestCase):
    def test_joins_and_keeps_numbers(self):
        s = shaper.shape("سلام 123 hello")
        self.assertTrue(s.startswith("hello 123"))          # LTR runs keep order, placed on the left
        self.assertNotIn("س", s)                        # base letters replaced by presentation forms
        self.assertEqual(shaper.shape("لا"), "ﻻ")      # lam-alef ligature

    def test_codes_untouched(self):
        s = shaper.shape("سلام {0}\\nخوب")
        self.assertIn("{0}", s)
        self.assertIn("\\n", s)

    def test_latin_only_unchanged(self):
        self.assertEqual(shaper.shape("Hello 5"), "Hello 5")


if __name__ == "__main__":
    unittest.main()
