import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from htcore import advisor, analyze, server
from htcore.store import open_project

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


class AdvisorTests(unittest.TestCase):
    def test_model_by_vram(self):
        self.assertEqual(advisor.suggest_model(4), "gemma3:4b")
        self.assertEqual(advisor.suggest_model(8), "aya-expanse:8b")
        self.assertEqual(advisor.suggest_model(12), "gemma3:12b")
        self.assertEqual(advisor.suggest_model(16), "gemma3:12b")
        self.assertEqual(advisor.suggest_model(24), "gemma3:27b")

    def test_unsuitable_models_filtered(self):
        for bad in ("qwen2.5-coder:14b", "llama3.2:3b", "qwen3-vl:2b", "gemma3:1b", "x:270m", "k2:cloud"):
            self.assertFalse(advisor.usable_for_translation(bad), bad)
        for good in ("gemma3:12b", "aya-expanse:8b", "llama3:latest", "gemma3:4b"):
            self.assertTrue(advisor.usable_for_translation(good), good)

    def test_small_or_no_gpu_prefers_manual(self):
        r = advisor.recommend(50, {}, gpu={"name": "X", "vram_gb": 12.0}, ollama={"running": True, "models": []})
        self.assertEqual(r["primary"], "C")
        r = advisor.recommend(5000, {}, gpu=None, ollama={"running": False, "models": []})
        self.assertEqual(r["primary"], "C")
        r = advisor.recommend(5000, {}, gpu={"name": "X", "vram_gb": 12.0}, ollama={"running": True, "models": ["gemma3:12b"]})
        self.assertEqual(r["primary"], "A")
        self.assertEqual(r["use_installed"], "gemma3:12b")
        self.assertFalse(r["needs_pull"])


class AnalyzeTests(unittest.TestCase):
    def test_engine_guess_and_stats(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
            _, doc = open_project(SAMPLES / "sample.po", "fa", workdir=t)
            info = analyze.analyze(SAMPLES / "sample.po", doc)
            self.assertEqual(info["engine"], "gettext")
            self.assertEqual(info["lines"], 4)
            self.assertGreater(info["code_percent"], 0)
            self.assertEqual(info["source_lang"], "en")
            _, doc = open_project(SAMPLES / "sample.kv", "fa", workdir=t)
            self.assertEqual(analyze.analyze(SAMPLES / "sample.kv", doc)["engine"], "unity")


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.old = os.getcwd()
        os.chdir(cls.tmp.name)
        server.WORKDIR = Path(cls.tmp.name) / "w"
        server.APP.cfg["backend"] = "mock"
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        os.chdir(cls.old)

    def call(self, path, body=None, headers=None):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path),
                                     data=None if body is None else json.dumps(body).encode())
        if body is not None:
            req.add_header("X-HT", "1")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_security_and_flow(self):
        self.assertEqual(self.call("/api/state", headers={"Host": "evil.example"})[0], 403)
        req = urllib.request.Request("http://127.0.0.1:%d/api/config" % self.port, data=b"{}", method="POST")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req)  # missing X-HT header (cross-site form post)
        self.assertEqual(cm.exception.code, 403)
        self.assertEqual(self.call("/api/stop")[0], 405)  # side effects never on GET
        with urllib.request.urlopen("http://127.0.0.1:%d/" % self.port) as r:
            self.assertIn(b"HomiTranslate", r.read())
        st, info = self.call("/api/open", {"path": str(SAMPLES / "sample.txt"), "target": "fa"})
        self.assertEqual(st, 200)
        self.assertEqual(info["lines"], 4)
        self.assertEqual(self.call("/api/pull", {"model": "gemma3:4b"})[0], 400)  # no download without confirm
        self.call("/api/translate", {})
        for _ in range(50):
            _, job = self.call("/api/job")
            if job["state"] != "running":
                break
            threading.Event().wait(0.1)
        self.assertEqual(job["state"], "done")
        _, rows = self.call("/api/lines?filter=all")
        self.assertEqual(rows["total"], 4)
        self.call("/api/line/save", {"id": 0, "tr": "ویرایش"})
        _, res = self.call("/api/export", {"kind": "file"})
        out = Path(res["path"])
        self.assertEqual(out.parent.name, "out")
        self.assertIn("ویرایش", out.read_text(encoding="utf-8"))
        self.assertNotEqual(out.resolve(), (SAMPLES / "sample.txt").resolve())


if __name__ == "__main__":
    unittest.main()
