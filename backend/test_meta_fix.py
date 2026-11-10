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
        # 之前的资源（另一个目录、另一个名字）用 OLD，old.prefab 引用它
        self.write("Assets/Other/copy.mat.meta", meta(OLD))
        self.write("Assets/old.prefab", f"m_Material: {{fileID: 2100000, guid: {OLD}, type: 2}}\n")
        self.write("Assets/tex.png.meta", meta(TEX))
        self.write("Assets/hero_b.prefab", f"c: {{guid: {TEX}}}\n")
        self.commit("之前的资源")
        # 后来有人拷了一份，hero.mat.meta 的 guid 也是 OLD，并有预制体引用它
        self.write("Assets/hero.mat.meta", meta(OLD))
        self.write("Assets/hero_a.prefab", f"a: {{guid: {OLD}}}\nb: {{guid: {OLD}}}\n")
        self.commit("guid 重复的提交")
        # 之后又有人引用了 OLD；old.prefab 改了别的内容，但它在重复提交前就引用了
        self.write("Assets/later.prefab", f"d: {{guid: {OLD}}}\n")
        self.write("Assets/old.prefab", f"m_Material: {{fileID: 2100000, guid: {OLD}, type: 2}}\nx: 1\n")
        self.write("Assets/later.txt", "later\n")
        self.commit("后来的提交")
        # 本地：Unity 给 hero.mat.meta 换了新 guid，tex.png.meta 只改导入设置，
        # hero_b.prefab 本地新加了对 OLD 的引用，还有一个非 meta 的本地修改
        self.write("Assets/hero.mat.meta", meta(NEW))
        self.write("Assets/tex.png.meta", meta(TEX).replace("2048", "1024"))
        self.write("Assets/hero_b.prefab", f"c: {{guid: {TEX}}}\ne: {{guid: {OLD}}}\n")
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
        self.assertEqual(guid["commit"]["subject"], "guid 重复的提交")
        self.assertEqual(guid["commit"]["dup_paths"], ["Assets/Other/copy.mat.meta"])
        self.assertTrue(guid["commit"]["mine_later"])
        self.assertEqual(guid["commit"]["other"]["subject"], "之前的资源")
        self.assertTrue(guid["grep_done"])
        refs = {ref["path"]: ref for ref in guid["refs"]}
        self.assertEqual(set(refs), {"Assets/hero_a.prefab", "Assets/later.prefab", "Assets/old.prefab",
                                     "Assets/hero_b.prefab"})
        self.assertTrue(refs["Assets/hero_a.prefab"]["replace"])
        self.assertEqual(refs["Assets/hero_a.prefab"]["count"], 2)
        self.assertTrue(refs["Assets/later.prefab"]["replace"])
        self.assertFalse(refs["Assets/old.prefab"]["replace"])
        self.assertEqual(refs["Assets/old.prefab"]["since"]["short"],
                         self.run_git("rev-parse", "--short", "HEAD~2").strip())
        self.assertFalse(refs["Assets/hero_b.prefab"]["replace"])

    def test_apply_all(self) -> None:
        result = meta_fix.apply_all(self.root)
        self.assertEqual(sorted(self.run_git("show", "--name-only", "--format=", "HEAD").split()),
                         ["Assets/hero.mat.meta", "Assets/hero_a.prefab", "Assets/later.prefab", "Assets/tex.png.meta"])
        self.assertEqual(self.read("Assets/hero_a.prefab"), f"a: {{guid: {NEW}}}\nb: {{guid: {NEW}}}\n")
        self.assertEqual(self.read("Assets/later.prefab"), f"d: {{guid: {NEW}}}\n")
        self.assertIn(OLD, self.read("Assets/old.prefab"))
        self.assertIn(OLD, self.read("Assets/hero_b.prefab"))
        self.assertIn(OLD, self.read("Assets/Other/copy.mat.meta"))  # 重复的 meta 自己不动
        self.assertIn("2 个文件在重复提交之前", result["message"])
        status = self.run_git("status", "--porcelain")
        self.assertIn("Assets/later.txt", status)
        self.assertIn("Assets/hero_b.prefab", status)
        with self.assertRaises(ValueError):
            meta_fix.apply_all(self.root)

    def test_local_meta_is_the_earlier_one(self) -> None:
        # 反过来：本地被换 guid 的是之前那个资源，之前的引用换成本地 guid，重复提交及之后的不动
        self.write("Assets/hero.mat.meta", meta(OLD))
        self.write("Assets/Other/copy.mat.meta", meta(NEW))
        item = next(row for row in meta_fix.scan(self.root)["items"] if row["kind"] == "guid")
        self.assertEqual(item["path"], "Assets/Other/copy.mat.meta")
        self.assertFalse(item["commit"]["mine_later"])
        self.assertEqual(item["commit"]["subject"], "guid 重复的提交")
        self.assertEqual(item["commit"]["dup_paths"], ["Assets/hero.mat.meta"])
        replace = {ref["path"]: ref["replace"] for ref in item["refs"]}
        self.assertEqual(replace, {"Assets/old.prefab": True, "Assets/hero_a.prefab": False,
                                   "Assets/later.prefab": False, "Assets/hero_b.prefab": False})

    def test_guid_changed_without_duplicate(self) -> None:
        self.write("Assets/tex.png.meta", meta(NEW))
        item = next(row for row in meta_fix.scan(self.root)["items"] if row["path"] == "Assets/tex.png.meta")
        self.assertEqual(item["kind"], "guid")
        self.assertEqual(item["commit"], {})
        result = meta_fix.apply_all(self.root)
        self.assertIn("tex.png.meta", result["message"])
        self.assertNotIn("Assets/tex.png.meta", self.run_git("show", "--name-only", "--format=", "HEAD").split())

    def test_console_actions(self) -> None:
        with mock.patch("bot.echo_bot.get_git_root", return_value=self.root):
            status, payload = console.admin_get("/api/meta", {})
            self.assertEqual(status, 200)
            self.assertEqual(len(payload["items"]), 2)
            status, payload = console.admin_post("/api/meta", {"action": "apply_all"}, {})
            self.assertEqual(status, 200, payload)
            self.assertEqual(payload["scan"]["items"], [])
            status, payload = console.admin_post("/api/meta", {"action": "apply_all"}, {})
            self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
