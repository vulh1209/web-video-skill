"""Unit tests for the pure parts of the web-video scripts. Run: python3 -m unittest discover -s tests"""
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import edl  # noqa: E402
import overlay  # noqa: E402
from common import FPS, snap, validate_storyboard  # noqa: E402
from record import is_secret, parse_action, step_actions  # noqa: E402
from redact import MASK, Redactor, literal_pass  # noqa: E402


def on_grid(x):
    return abs(x * FPS - round(x * FPS)) < 1e-4   # snap() keeps 6 decimals


class TimeMapping(unittest.TestCase):
    def setUp(self):
        self.segs = edl.build_ranges(1.0, 10.0, windows=[(1.0, 3.0), (6.0, 7.0)], cuts=[], idle_speed=6.0)

    def test_classifies_keep_and_idle(self):
        self.assertEqual([s["speed"] == 1.0 for s in self.segs], [True, False, True, False])
        idle = self.segs[1]
        self.assertGreaterEqual(idle["speed"], 6.0)
        self.assertLessEqual((idle["b"] - idle["a"]) / idle["speed"], edl.MAX_IDLE_OUT + 1e-6)

    def test_boundaries_snap_to_frame_grid(self):
        segs = edl.build_ranges(0.513, 4.271, [(0.9, 2.017)], [], 6.0)
        self.assertTrue(all(on_grid(s["a"]) and on_grid(s["b"]) for s in segs))

    def test_cuts_are_removed(self):
        segs = edl.build_ranges(0.0, 6.0, [(0.0, 6.0)], cuts=[(2.0, 4.0)], idle_speed=6.0)
        self.assertEqual([(s["a"], s["b"]) for s in segs], [(0.0, 2.0), (4.0, 6.0)])
        self.assertAlmostEqual(edl.newt(segs, 3.0), 2.0)      # inside a cut maps to the next kept frame
        self.assertAlmostEqual(edl.body_duration(segs), 4.0)

    def test_short_idle_gap_stays_1x(self):
        segs = edl.build_ranges(0.0, 3.0, [(0.0, 1.4), (1.5, 3.0)], [], 6.0)
        self.assertEqual(len(segs), 1)

    def test_newt_monotonic_and_rawt_inverse(self):
        segs = edl.insert_freeze(self.segs, 2.0, 1.0)
        prev = -1
        for k in range(0, 101):
            t = 1.0 + k * 0.09
            nt = edl.newt(segs, t)
            self.assertGreaterEqual(nt, prev - 1e-9)
            prev = nt
        for t in (1.2, 2.5, 6.5):                         # 1x regions round-trip exactly
            self.assertAlmostEqual(edl.rawt(segs, edl.newt(segs, t)), t, places=6)

    def test_freeze_adds_duration_once(self):
        before = edl.body_duration(self.segs)
        segs = edl.insert_freeze(self.segs, 2.0, 1.0)
        self.assertAlmostEqual(edl.body_duration(segs), before + 1.0, places=6)
        self.assertEqual(sum(1 for s in segs if "freeze" in s), 1)
        self.assertAlmostEqual(edl.newt(segs, 2.0 + 1 / FPS) - edl.newt(segs, 2.0), 1.0 + 1 / FPS, places=6)


def fake_doc(off=0.1):
    ev = [{"t": 0.5, "kind": "flash"}, {"t": 1.0, "kind": "flash_end"},
          {"t": 1.0, "kind": "step", "i": 0, "caption": "Login", "hidden": True, "bug": False},
          {"t": 3.0, "kind": "step_end", "i": 0},
          {"t": 3.0, "kind": "step", "i": 1, "caption": "Open the list", "hidden": False, "bug": False},
          {"t": 5.0, "kind": "step_end", "i": 1},
          {"t": 5.0, "kind": "step", "i": 2, "caption": "Click save", "hidden": False, "bug": True},
          {"t": 6.0, "kind": "click", "x": 640, "y": 650, "w": 1280, "h": 720, "target": "#save",
           "bbox": {"x": 600, "y": 630, "width": 80, "height": 40}},
          {"t": 6.1, "kind": "error", "source": "http", "status": 500, "method": "POST", "url": "http://x/api"},
          {"t": 9.0, "kind": "bug", "i": 2, "bbox": {"x": 600, "y": 630, "width": 80, "height": 40}},
          {"t": 9.0, "kind": "step_end", "i": 2}, {"t": 9.0, "kind": "end"}]
    return {"viewport": [1280, 720], "video_offset": off, "events": ev}


SB = {"mode": "bug-report", "url": "http://x", "title": "t",
      "bug": {"expected": "saved", "actual": "spins"},
      "steps": [{"do": "goto /login", "hidden": True}, {"do": "expect h1", "caption": "Open the list"},
                {"do": "click #save", "caption": "Click save", "bug": True}]}


class Plan(unittest.TestCase):
    def test_bug_report_plan(self):
        plan = edl.make_plan(fake_doc(), SB, raw_dur=14.0, narration=None, zoom=1.5)
        self.assertGreaterEqual(plan["start"], 3.1 - 1e-6)            # hidden login and flash are cut
        self.assertEqual(sum(1 for s in plan["segments"] if "freeze" in s and s["dur"] == edl.BUG_FREEZE), 1)
        self.assertEqual(plan["zooms"], [])                            # no zoom in bug-report
        self.assertAlmostEqual(plan["stop"], 9.1 + edl.TAIL["bug-report"], places=6)
        self.assertEqual([c["text"] for c in plan["captions"]], ["Open the list", "Click save"])
        self.assertEqual(len(plan["errors"]), 1)

    def test_feature_demo_caption_goes_opposite_click(self):
        sb = dict(SB, mode="feature-demo")
        plan = edl.make_plan(fake_doc(), sb, raw_dur=14.0, narration=None, zoom=1.5)
        self.assertEqual(plan["captions"][1]["pos"], "top")           # click at y=650 of 720
        self.assertEqual(len(plan["zooms"]), 1)
        self.assertIsNone(next((s for s in plan["segments"] if s.get("dur") == edl.BUG_FREEZE), None))

    def test_offset_applied_once(self):
        a = edl.make_plan(fake_doc(0.0), SB, 14.0, None, 1.5)
        b = edl.make_plan(fake_doc(0.3), SB, 14.0, None, 1.5)
        self.assertAlmostEqual(b["start"] - a["start"], 0.3, delta=1 / FPS)
        self.assertAlmostEqual(b["clicks"][0]["raw_t"] - a["clicks"][0]["raw_t"], 0.3, places=6)

    def test_narration_holds_step(self):
        sb = dict(SB, mode="feature-demo")
        narr = {"clips": [{"i": 1, "duration": 5.0, "file": "x"}]}
        plan = edl.make_plan(fake_doc(), sb, 14.0, narr, 1.5)
        cap = plan["captions"][0]
        self.assertGreaterEqual(cap["b"] - cap["a"], 5.0 + 0.35 - 1 / FPS)

    def test_first_visible_goto_skips_page_load(self):
        doc = fake_doc(0.0)
        doc["events"] = [e for e in doc["events"] if e.get("i") != 0 or e["kind"] not in ("step", "step_end")]
        doc["events"].insert(2, {"t": 1.0, "kind": "step", "i": 0, "caption": "Open", "hidden": False, "bug": False})
        doc["events"].insert(3, {"t": 2.6, "kind": "nav", "url": "http://x/"})
        doc["events"].insert(4, {"t": 3.0, "kind": "step_end", "i": 0})
        sb = dict(SB, mode="feature-demo", steps=[{"do": "goto /", "caption": "Open"}] + SB["steps"][1:])
        plan = edl.make_plan(doc, sb, 14.0, None, 1.5)
        self.assertGreaterEqual(plan["start"], 2.6)

    def test_zoom_expression_has_all_windows(self):
        z = [{"a": 1.0, "b": 2.5, "cx": 0.2, "cy": 0.3}, {"a": 3.0, "b": 4.0, "cx": 0.8, "cy": 0.6}]
        f = edl.zoom_filter(z, 1280, 720, 1.5, 2)
        self.assertIn("2560:1440", f)
        self.assertEqual(f.count("clip(min("), 2)
        self.assertIn("0.8", f)


class Storyboard(unittest.TestCase):
    def test_parse_actions(self):
        self.assertEqual(parse_action("fill #email | a@b.c"), ("fill", ["#email", "a@b.c"]))
        self.assertEqual(parse_action("click text=New invoice"), ("click", ["text=New invoice"]))
        self.assertEqual(step_actions({"do": ["goto /", "wait 300"]}), [("goto", ["/"]), ("wait", ["300"])])

    def test_yaml_comment_trap_has_hint(self):
        with self.assertRaisesRegex(ValueError, "quote the whole line"):
            parse_action("fill")

    def test_bad_actions(self):
        for s in ("tap #x", "type #x", "goto"):
            with self.assertRaises(ValueError):
                parse_action(s)

    def test_validate(self):
        self.assertEqual(validate_storyboard(SB), [])
        bad = dict(SB, steps=[{"do": "click #a"}])
        self.assertTrue(any("caption" in e for e in validate_storyboard(bad)))
        self.assertTrue(any("bug" in e for e in validate_storyboard(dict(SB, bug={}))))

    def test_is_secret(self):
        self.assertTrue(is_secret("#password", None))
        self.assertTrue(is_secret("#pw", "password"))
        self.assertFalse(is_secret("#email", "email"))


class Redaction(unittest.TestCase):
    def test_har_entry(self):
        r = Redactor(literals=["hunter22"])
        entry = {"request": {"url": "http://x/api?token=abc&page=2", "headers": [
            {"name": "Authorization", "value": "Bearer abcdefghijkl"}, {"name": "Accept", "value": "json"}],
            "cookies": [{"name": "sid", "value": "s3cr3t"}],
            "postData": {"mimeType": "application/json", "text": '{"email":"a@b.c","password":"hunter22"}'}}}
        out = r.walk(entry)
        req = out["request"]
        self.assertIn(f"token={MASK.replace('[', '%5B').replace(']', '%5D')}", req["url"])
        self.assertIn("page=2", req["url"])
        self.assertEqual(req["headers"][0]["value"], MASK)
        self.assertEqual(req["headers"][1]["value"], "json")
        self.assertEqual(req["cookies"][0]["value"], MASK)
        self.assertNotIn("hunter22", req["postData"]["text"])
        self.assertIn("a@b.c", req["postData"]["text"])

    def test_literal_pass_catches_trace_params(self):
        r = Redactor(literals=["demo-pass"])
        line = json.dumps({"method": "fill", "params": {"selector": "#pw", "value": "demo-pass"}}).encode()
        self.assertNotIn(b"demo-pass", literal_pass(r, line))


class VietnameseNumbers(unittest.TestCase):
    def test_num_to_vi(self):
        from tts import num_to_vi
        cases = {0: "không", 15: "mười lăm", 21: "hai mươi mốt", 105: "một trăm linh năm",
                 1005: "một nghìn không trăm linh năm", 1500: "một nghìn năm trăm",
                 1200300: "một triệu hai trăm nghìn ba trăm", 2000000000: "hai tỷ"}
        for n, w in cases.items():
            self.assertEqual(num_to_vi(n), w)

    def test_normalize_vi(self):
        from tts import normalize_vi
        self.assertEqual(normalize_vi("Tổng 1.500 USD, giảm 10%"),
                         "Tổng một nghìn năm trăm đô la, giảm mười phần trăm")
        self.assertEqual(normalize_vi("Giá $25"), "Giá hai mươi lăm đô la")
        self.assertEqual(normalize_vi("tăng 2,5 lần"), "tăng hai phẩy năm lần")
        self.assertEqual(normalize_vi("Bấm New invoice"), "Bấm New invoice")


class FfmpegCompat(unittest.TestCase):
    def test_filter_script_flag_by_version(self):
        import common
        old = common._FF_MAJOR
        try:
            for major, flag in ((6, "-filter_complex_script"), (7, "-/filter_complex"), (8, "-/filter_complex"),
                                (0, "-/filter_complex")):
                common._FF_MAJOR = major
                self.assertEqual(common.filter_script_args("f.txt"), [flag, "f.txt"])
        finally:
            common._FF_MAJOR = old


class TextFiles(unittest.TestCase):
    def test_textfile_uses_lf_only(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            t = overlay.Texts(pathlib.Path(td))
            rel = t.file("dòng 1\ndòng 2")
            self.assertNotIn(b"\r", (pathlib.Path(td) / rel).read_bytes())


class Srt(unittest.TestCase):
    def test_srt_format(self):
        s = overlay.srt([{"a": 1.5, "b": 62.25, "text": "Xin chào"}])
        self.assertIn("00:00:01,500 --> 00:01:02,250", s)
        self.assertIn("Xin chào", s)


if __name__ == "__main__":
    unittest.main()
