"""bot/meta_fix.py：本地 .meta 改动的检查、导入设置提交、guid 替换提交。

运行：.venv/bin/python -m unittest backend.test_meta_fix
"""
import os
import subprocess
import tempfile
import unittest
from unittest import mock

from bot import meta_fix
from console import server as console

OLD = "a" * 32
NEW = "b" * 32
TEX = "c" * 32


def meta(guid: str, extra: str = "") -> str:
    return f"fileFormatVersion: 2\nguid: {guid}\nTextureImporter:\n  maxTextureSize: 2048\n{extra}"


class MetaFixTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.run_git("init", "-q", "-b", "main")
        self.run_git("config", "user.name", "tester")
        self.run_git("config", "user.email", "tester@example.com")
        self.write("Assets/old.prefab", f"m_Material: {{fileID: 2100000, guid: {OLD}, type: 2}}\n")
        self.write("Assets/tex.png.meta", meta(TEX))
        self.commit("旧的")
        # 别人提交了同名资源，guid 是 OLD，并且有材质引用它
        self.write("Assets/hero.mat.meta", meta(OLD))
        self.write("Assets/hero_a.prefab", f"a: {{guid: {OLD}}}\nb: {{guid: {OLD.upper()}}}\n")
        self.write("Assets/hero_b.prefab", f"c: {{guid: {TEX}}}\n")
        self.commit("同名资源")
        self.write("Assets/later.txt", "later\n")
        self.commit("后来的提交")
        # 本地：hero.mat.meta 的 guid 是 NEW，tex.png.meta 只改导入设置，还有一个非 meta 的本地修改
        self.write("Assets/hero.mat.meta", meta(NEW))
        self.write("Assets/tex.png.meta", meta(TEX).replace("2048", "1024"))
        self.write("Assets/later.txt", "dirty\n")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_git(self, *args) -> str:
        return subprocess.run(["git", "-C", self.root, *args], check=True, capture_output=True, text=True).stdout

    def write(self, path: str, text: str) -> None:
        absolute = os.path.join(self.root, path)
        os.makedirs(os.path.dirname(absolute), exist_ok=True)
        with open(absolute, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)

    def read(self, path: str) -> str:
        with open(os.path.join(self.root, path), encoding="utf-8") as handle:
            return handle.read()

    def commit(self, message: str) -> None:
        self.run_git("add", "-A")
        self.run_git("commit", "-q", "-m", message)

    def test_scan(self) -> None:
        items = {item["path"]: item for item in meta_fix.scan(self.root)["items"]}
        self.assertEqual(set(items), {"Assets/hero.mat.meta", "Assets/tex.png.meta"})
        self.assertEqual(items["Assets/tex.png.meta"]["kind"], "import")
        guid = items["Assets/hero.mat.meta"]
        self.assertEqual(guid["kind"], "guid")
        self.assertEqual((guid["old_guid"], guid["new_guid"]), (OLD, NEW))
        self.assertEqual(guid["commit"]["subject"], "同名资源")
        self.assertEqual(guid["refs"], [{"path": "Assets/hero_a.prefab", "count": 2, "dirty": False}])
        self.assertEqual(guid["others"], {"done": True, "paths": ["Assets/old.prefab"]})

    def test_commit_imports_only_those(self) -> None:
        result = meta_fix.commit_imports(self.root)
        self.assertEqual(result["paths"], ["Assets/tex.png.meta"])
        self.assertEqual(self.run_git("show", "--name-only", "--format=", "HEAD").split(), ["Assets/tex.png.meta"])
        status = self.run_git("status", "--porcelain")
        self.assertIn("Assets/later.txt", status)
        self.assertIn("Assets/hero.mat.meta", status)
        with self.assertRaises(ValueError):
            meta_fix.commit_imports(self.root, ["Assets/hero.mat.meta"])

    def test_fix_guid(self) -> None:
        result = meta_fix.fix_guid(self.root, "Assets/hero.mat.meta")
        self.assertEqual(sorted(self.run_git("show", "--name-only", "--format=", "HEAD").split()),
                         ["Assets/hero.mat.meta", "Assets/hero_a.prefab"])
        self.assertEqual(self.read("Assets/hero_a.prefab"), f"a: {{guid: {NEW}}}\nb: {{guid: {NEW}}}\n")
        self.assertIn(OLD, self.read("Assets/old.prefab"))  # 不在那次提交里，只报不改
        self.assertIn("还有 1 个", result["message"])
        status = self.run_git("status", "--porcelain")
        self.assertIn("Assets/later.txt", status)
        self.assertIn("Assets/tex.png.meta", status)
        with self.assertRaises(ValueError):
            meta_fix.fix_guid(self.root, "Assets/tex.png.meta")

    def test_console_actions(self) -> None:
        with mock.patch("bot.echo_bot.get_git_root", return_value=self.root):
            status, payload = console.admin_get("/api/meta", {})
            self.assertEqual(status, 200)
            self.assertEqual(len(payload["items"]), 2)
            status, payload = console.admin_post("/api/meta", {"action": "commit_imports"}, {})
            self.assertEqual(status, 200, payload)
            self.assertEqual([item["path"] for item in payload["scan"]["items"]], ["Assets/hero.mat.meta"])
            status, payload = console.admin_post("/api/meta", {"action": "fix_guid", "path": "Assets/nope.meta"}, {})
            self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
