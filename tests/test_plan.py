# -*- coding: utf-8 -*-
"""Unit tests for scripts/plan.py (standard library only).

    python -m unittest discover -s tests -v
"""

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("plan", REPO / "scripts" / "plan.py")
plan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(plan)


class PlanTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "workspace"
        self.root.mkdir()
        self.cli("init", "--name", "测试整理")

    def tearDown(self):
        self._tmp.cleanup()

    def cli(self, command, *extra):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = plan.main([command, "--root", str(self.root), *extra])
        return code, buf.getvalue()

    def load(self):
        return json.loads((self.root / ".fileplan" / "plan.json").read_text(encoding="utf-8"))

    def touch(self, rel, text="data"):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def build(self, *ops):
        for args in ops:
            self.cli("add", *args)
        code, out = self.cli("build")
        self.assertEqual(code, 0, out)
        return self.load()["token"], out

    def test_init_changes_no_user_files(self):
        p = self.touch("keep.txt")
        self.assertTrue(p.is_file())
        self.assertTrue((self.root / ".fileplan" / "plan.json").is_file())
        self.assertEqual(self.load()["items"], [])

    def test_safe_rel_rejects_traversal_and_plan_dir(self):
        with self.assertRaises(ValueError):
            plan.safe_rel(self.root, "../outside.txt")
        with self.assertRaises(ValueError):
            plan.safe_rel(self.root, ".fileplan/plan.json")

    def test_build_generates_plan_md_and_token(self):
        self.touch("a.txt")
        token, out = self.build(("--op", "move", "--src", "a.txt", "--dst", "归档/a.txt", "--note", "归档"))
        self.assertIn(token, out)
        text = (self.root / "PLAN.md").read_text(encoding="utf-8")
        self.assertIn("计划哈希", text)
        self.assertIn("a.txt", text)
        self.assertIn(token, text)

    def test_token_changes_when_plan_changes(self):
        self.touch("a.txt")
        self.touch("b.txt")
        token1, _ = self.build(("--op", "copy", "--src", "a.txt", "--dst", "copy_a.txt"))
        data = self.load()
        data["items"].append({"op": "copy", "src": "b.txt", "dst": "copy_b.txt", "note": "", "overwrite": False})
        plan.save_plan(self.root, data)
        self.assertNotEqual(plan.compute_token(self.load()), token1)

    def test_apply_rejects_wrong_token(self):
        self.touch("a.txt")
        token, _ = self.build(("--op", "move", "--src", "a.txt", "--dst", "归档/a.txt"))
        with self.assertRaises(SystemExit):
            self.cli("apply", "--token", "deadbeef", "--confirm", "同意")
        self.assertTrue((self.root / "a.txt").is_file(), "token 不匹配时不应改动任何文件")

    def test_apply_requires_nonempty_confirm(self):
        self.touch("a.txt")
        token, _ = self.build(("--op", "move", "--src", "a.txt", "--dst", "归档/a.txt"))
        with self.assertRaises(SystemExit):
            self.cli("apply", "--token", token, "--confirm", "   ")
        self.assertTrue((self.root / "a.txt").is_file())

    def test_full_apply_verify_with_copy_move_delete(self):
        self.touch("a.txt", "aaa")
        self.touch("b.txt", "bbb")
        self.touch("tmp.tmp", "junk")
        token, _ = self.build(
            ("--op", "copy", "--src", "a.txt", "--dst", "副本/a.txt"),
            ("--op", "move", "--src", "b.txt", "--dst", "归档/b.txt"),
            ("--op", "delete", "--src", "tmp.tmp", "--note", "确认无用"),
        )
        code, out = self.cli("apply", "--token", token, "--confirm", "我看过清单，同意执行")
        self.assertEqual(code, 0, out)
        self.assertTrue((self.root / "副本/a.txt").is_file())
        self.assertTrue((self.root / "归档/b.txt").is_file())
        self.assertFalse((self.root / "b.txt").exists())
        self.assertFalse((self.root / "tmp.tmp").exists())
        quarantined = self.root / ".fileplan" / "trash" / token[:8] / "tmp.tmp"
        self.assertTrue(quarantined.is_file(), "delete 必须移入隔离区而非真删")
        self.assertTrue((self.root / ".fileplan" / "backup" / token[:8] / "b.txt").is_file())
        applied = json.loads((self.root / ".fileplan" / "apply.json").read_text(encoding="utf-8"))
        self.assertEqual(applied["confirm"], "我看过清单，同意执行")
        code, out = self.cli("verify")
        self.assertEqual(code, 0, out)
        self.assertIn("符合预期 3", out)

    def test_duplicate_apply_rejected(self):
        self.touch("a.txt")
        token, _ = self.build(("--op", "copy", "--src", "a.txt", "--dst", "copy_a.txt"))
        self.cli("apply", "--token", token, "--confirm", "同意")
        with self.assertRaises(SystemExit):
            self.cli("apply", "--token", token, "--confirm", "再执行一次")

    def test_conflicting_target_blocks_apply(self):
        self.touch("a.txt")
        self.touch("exists.txt")
        self.cli("add", "--op", "move", "--src", "a.txt", "--dst", "exists.txt")
        code, out = self.cli("build")
        self.assertEqual(code, 0)
        self.assertIn("预检异常", out)
        data = self.load()
        with self.assertRaises(SystemExit):
            self.cli("apply", "--token", data["token"], "--confirm", "同意")
        self.assertTrue((self.root / "a.txt").is_file())

    def test_delete_directory_is_rejected(self):
        (self.root / "folder").mkdir()
        self.cli("add", "--op", "delete", "--src", "folder")
        code, out = self.cli("build")
        self.assertEqual(code, 0)
        self.assertIn("UNSAFE", out)
        with self.assertRaises(SystemExit):
            self.cli("apply", "--token", self.load()["token"], "--confirm", "同意")
        self.assertTrue((self.root / "folder").is_dir())

    def test_limit_blocks_oversized_plan(self):
        for i in range(3):
            self.touch(f"f{i}.txt")
            self.cli("add", "--op", "copy", "--src", f"f{i}.txt", "--dst", f"out/f{i}.txt")
        with self.assertRaises(SystemExit):
            self.cli("build", "--limit", "2")

    def test_status_reports_stale_token(self):
        self.touch("a.txt")
        token, _ = self.build(("--op", "copy", "--src", "a.txt", "--dst", "copy_a.txt"))
        data = self.load()
        data["items"][0]["dst"] = "changed.txt"
        plan.save_plan(self.root, data)
        code, out = self.cli("status")
        self.assertEqual(code, 0)
        self.assertIn("token 失效", out)


if __name__ == "__main__":
    unittest.main()
