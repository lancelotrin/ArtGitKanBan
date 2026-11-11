"""bot/meta_fix.py：本地 .meta 改动的检查、导入设置提交、guid 替换提交。

运行：.venv/bin/python -m unittest backend.test_meta_fix
"""
import os
import subprocess
import tempfile
import unittest
from unittest import mock

from bot import meta_fix
import http.server
import json
import socket
import struct
import threading

from boards import meta_board
from bot import unity_bridge

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
        self.assertIn("有 2 个引用旧 guid 的文件没改", result["message"])
        self.assertIn("    不动 Assets/hero_b.prefab：只在本地引用，没提交过", result["details"])
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

    def test_skips_pre_commit_hook(self) -> None:
        hook = os.path.join(self.root, ".git", "hooks", "pre-commit")
        with open(hook, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("#!/bin/sh\necho hook ran\nexit 1\n")
        os.chmod(hook, 0o755)
        result = meta_fix.apply_all(self.root)
        self.assertTrue(result["ok"])
        self.assertIn("Assets/hero.mat.meta", self.run_git("show", "--name-only", "--format=", "HEAD").split())

    def test_restores_files_when_commit_fails(self) -> None:
        before = self.read("Assets/hero_a.prefab")
        with mock.patch.object(meta_fix, "commit", side_effect=RuntimeError("提交失败：钩子")):
            with self.assertRaises(RuntimeError) as caught:
                meta_fix.apply_all(self.root)
        self.assertIn("已改回原样", str(caught.exception))
        self.assertEqual(self.read("Assets/hero_a.prefab"), before)
        self.assertEqual(self.read("Assets/later.prefab"), f"d: {{guid: {OLD}}}\n")

    def test_guid_changed_without_duplicate(self) -> None:
        self.write("Assets/tex.png.meta", meta(NEW))
        item = next(row for row in meta_fix.scan(self.root)["items"] if row["path"] == "Assets/tex.png.meta")
        self.assertEqual(item["kind"], "guid")
        self.assertEqual(item["commit"], {})
        result = meta_fix.apply_all(self.root)
        self.assertIn("1 个 meta 没处理", result["message"])
        self.assertTrue(any(line.startswith("  没处理 Assets/tex.png.meta：找不到") for line in result["details"]))
        self.assertNotIn("Assets/tex.png.meta", self.run_git("show", "--name-only", "--format=", "HEAD").split())

    def board_paths(self):
        return (mock.patch.object(meta_board, "LOG_PATH", os.path.join(self.root, "meta_fix.log")),
                mock.patch.object(meta_board, "PUSH_PATH", os.path.join(self.root, "meta_pushes.json")),
                mock.patch.object(meta_board, "SCAN_PATH", os.path.join(self.root, "meta_last_scan.json")),
                mock.patch("bot.echo_bot.get_git_root", return_value=self.root),
                mock.patch.object(unity_bridge, "refresh", return_value={"ok": True, "message": "Unity 已刷新资源（测试）。"}))

    def add_remote(self) -> str:
        remote = os.path.join(self.root, ".remote.git")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", remote], check=True, capture_output=True)
        self.run_git("remote", "add", "origin", remote)
        self.run_git("push", "-q", "origin", "main")
        with open(os.path.join(self.root, ".git", "info", "exclude"), "a", encoding="utf-8") as handle:
            handle.write(".remote.git/\nmeta_*\n")
        return remote

    def test_board_check_apply_push(self) -> None:
        remote = self.add_remote()
        patches = self.board_paths()
        for patch in patches:
            patch.start()
        try:
            meta_board.run("check", "同事")
            scan = meta_board.view()["scan"]
            self.assertEqual(len(scan["items"]), 2)
            self.assertEqual(scan["who"], "同事")
            self.assertIn("已是最新", scan["pull"]["message"])
            self.assertTrue(meta_board.JOB["ok"], meta_board.JOB["message"])
            meta_board.run("apply", "同事")
            self.assertTrue(meta_board.JOB["ok"], meta_board.JOB["message"])
            self.assertIn("已推送到 origin/main", meta_board.JOB["message"])
            self.assertEqual(meta_board.view()["scan"]["items"], [])
            head = self.run_git("rev-parse", "HEAD").strip()
            pushed = subprocess.run(["git", "-C", remote, "rev-parse", "main"], capture_output=True, text=True).stdout.strip()
            self.assertEqual(pushed, head)
            entry = meta_board.pushes()[0]
            self.assertTrue(entry["pushed"])
            self.assertEqual(entry["hash"], head)
            self.assertEqual(entry["who"], "同事")
            self.assertIn("Assets/hero_a.prefab", entry["files"])
            self.assertTrue(entry["message"].startswith("meta 处理："))
            log = meta_board.log_text()
            self.assertIn("开始：全部处理、提交并推送（同事 点的）", log)
            self.assertIn("已换 Assets/hero_a.prefab（2 处）", log)
            self.assertIn("推送 ", log)
            # 没东西可处理：记失败，不卡在进行中
            meta_board.run("apply", "同事")
            self.assertFalse(meta_board.JOB["ok"])
            self.assertFalse(meta_board.JOB["running"])
            self.assertIn("没有能处理的 meta", meta_board.JOB["message"])
        finally:
            for patch in patches:
                patch.stop()

    def test_board_push_failure_and_retry(self) -> None:
        patches = self.board_paths()
        for patch in patches:
            patch.start()
        try:
            meta_board.run("apply", "同事")  # 没有 origin，推送失败，提交留在本地
            self.assertFalse(meta_board.JOB["ok"])
            self.assertIn("推送到 origin/main 失败", meta_board.JOB["message"])
            self.assertFalse(meta_board.pushes()[0]["pushed"])
            self.add_remote()
            meta_board.run("push", "同事")
            self.assertTrue(meta_board.JOB["ok"], meta_board.JOB["message"])
            self.assertEqual([entry["pushed"] for entry in meta_board.pushes()], [True, False])
        finally:
            for patch in patches:
                patch.stop()

    def test_pull_fast_forwards_and_keeps_local_changes(self) -> None:
        remote = self.add_remote()
        other = os.path.join(self.root, ".other")
        subprocess.run(["git", "clone", "-q", remote, other], check=True, capture_output=True)
        with open(os.path.join(other, "Assets", "new.txt"), "w", encoding="utf-8") as handle:
            handle.write("new\n")
        for args in (["add", "-A"], ["-c", "user.name=x", "-c", "user.email=x@x", "commit", "-q", "-m", "别人的"], ["push", "-q"]):
            subprocess.run(["git", "-C", other, *args], check=True, capture_output=True)
        result = meta_fix.pull(self.root, "origin", ["main"])
        self.assertTrue(result["ok"], result)
        self.assertIn("1 个提交", result["message"])
        self.assertTrue(os.path.exists(os.path.join(self.root, "Assets", "new.txt")))
        self.assertEqual(meta_fix.guid_of(self.read("Assets/hero.mat.meta")), NEW)  # 本地修改还在
        self.assertFalse(meta_fix.pull(self.root, "origin", ["dev"])["ok"])

    def test_unity_bridge_refresh(self) -> None:
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        seen = {}

        def serve() -> None:
            conn, _ = server.accept()
            with conn:
                conn.sendall(b"WELCOME UNITY-MCP 1 FRAMING=1\n")
                size = struct.unpack(">Q", conn.recv(8))[0]
                seen.update(json.loads(conn.recv(size)))
                reply = json.dumps({"status": "success", "result": {"success": True, "message": "Refresh requested.",
                                                                    "data": {"resulting_state": "idle"}}}).encode()
                conn.sendall(struct.pack(">Q", len(reply)) + reply)

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        status_dir = os.path.join(self.root, ".unity-mcp")
        os.makedirs(status_dir)
        with open(os.path.join(status_dir, "unity-mcp-status-whatever.json"), "w", encoding="utf-8") as handle:
            json.dump({"unity_port": port, "project_path": unity_bridge.data_path(self.root), "reloading": False}, handle)
        with mock.patch.dict(os.environ, {"UNITY_MCP_STATUS_DIR": status_dir}), \
                mock.patch.object(unity_bridge, "DEFAULT_HTTP_PORT", 1):
            result = unity_bridge.refresh(self.root)
        thread.join(5)
        server.close()
        self.assertTrue(result["ok"], result)
        self.assertIn(f"端口 {port}", result["message"])
        self.assertEqual(seen["type"], "refresh_unity")
        self.assertEqual(seen["params"]["mode"], "force")

    def test_unity_bridge_not_running(self) -> None:
        with mock.patch.dict(os.environ, {"UNITY_MCP_STATUS_DIR": os.path.join(self.root, "none")}), \
                mock.patch.object(unity_bridge, "DEFAULT_PORT", 1), \
                mock.patch.object(unity_bridge, "DEFAULT_HTTP_PORT", 1):
            result = unity_bridge.refresh(self.root)
        self.assertFalse(result["ok"])
        self.assertIn("连不上 Unity", result["message"])

    def fake_http(self, instances: list) -> tuple:
        """假的 mcp-for-unity HTTP 服务，端口写进工程 RunState 的 pid 文件名。"""
        seen = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def reply(self, code: int, data: dict) -> None:
                body = json.dumps(data).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                if self.path == "/health":
                    self.reply(200, {"status": "healthy", "message": "MCP for Unity server is running"})
                else:
                    self.reply(200, {"success": True, "instances": instances})

            def do_POST(self) -> None:
                seen.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self.reply(200, {"success": True, "message": "Refresh requested.", "data": {"resulting_state": "idle"}})

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = server.server_address[1]
        run_state = os.path.join(self.root, "Library", "MCPForUnity", "RunState")
        os.makedirs(run_state, exist_ok=True)
        open(os.path.join(run_state, f"mcp_http_{port}.pid"), "w").close()
        return port, seen

    def test_unity_bridge_http(self) -> None:
        name = os.path.basename(self.root)
        port, seen = self.fake_http([{"project": "Other", "hash": "x"}, {"project": name, "hash": "abc"}])
        with mock.patch.object(unity_bridge, "DEFAULT_HTTP_PORT", 1):
            result = unity_bridge.refresh(self.root)
        self.assertTrue(result["ok"], result)
        self.assertIn(f"端口 {port}", result["message"])
        self.assertEqual(seen[0]["type"], "refresh_unity")
        self.assertEqual(seen[0]["unity_instance"], f"{name}@abc")
        self.assertEqual(seen[0]["params"]["mode"], "force")

    def test_unity_bridge_http_without_unity(self) -> None:
        self.fake_http([])
        with mock.patch.object(unity_bridge, "DEFAULT_HTTP_PORT", 1):
            result = unity_bridge.refresh(self.root)
        self.assertFalse(result["ok"])
        self.assertIn("没有 Unity 连上来", result["message"])

if __name__ == "__main__":
    unittest.main()
