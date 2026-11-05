"""炸线、复盘、合并确认、后台管理、登录页和路由表的测试。数据全部指到临时目录。

运行：.venv/bin/python -m unittest backend.test_boards_api
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from common import auth
from boards import bomb_board
from console import server as console
from boards import merge_web
from boards import review_board
from backend import app as board_app
from backend.app import app

REPORT = """**概览**
共 2 条提交，改了 Shader。
**需要留意**
改动：`Foo.shader` 去掉了 **fallback**
"""


class BoardsApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.saved = {
            (auth, "USERS_PATH"): auth.USERS_PATH, (auth, "SESSIONS_PATH"): auth.SESSIONS_PATH,
            (bomb_board, "HISTORY_PATH"): bomb_board.HISTORY_PATH, (review_board, "REVIEW_DIR"): review_board.REVIEW_DIR,
            (merge_web, "SESSIONS_DIR"): merge_web.SESSIONS_DIR, (merge_web, "PROJECTS_PATH"): merge_web.PROJECTS_PATH,
            (merge_web, "OPEN_PATH"): merge_web.OPEN_PATH,
            (console, "NOTES_PATH"): console.NOTES_PATH, (console, "PEOPLE_PATH"): console.PEOPLE_PATH,
        }
        auth.USERS_PATH = os.path.join(root, "users.json")
        auth.SESSIONS_PATH = os.path.join(root, "sessions.json")
        bomb_board.HISTORY_PATH = os.path.join(root, "bomb_history.json")
        review_board.REVIEW_DIR = os.path.join(root, "reviews")
        merge_web.SESSIONS_DIR = Path(root) / "sessions"
        merge_web.PROJECTS_PATH = os.path.join(root, "merge_projects.json")
        merge_web.OPEN_PATH = os.path.join(root, "merge_open.json")
        console.NOTES_PATH = os.path.join(root, "notes.json")
        console.PEOPLE_PATH = os.path.join(root, "people.json")
        users = {"ou_admin": {"open_id": "ou_admin", "name": "管理员", "role": "admin"},
                 "ou_user": {"open_id": "ou_user", "name": "同事", "role": "user"}}
        self.write(auth.USERS_PATH, users)
        self.admin = self.client("ou_admin")
        self.user = self.client("ou_user")

    def tearDown(self) -> None:
        for (module, name), value in self.saved.items():
            setattr(module, name, value)
        self.tmp.cleanup()

    @staticmethod
    def write(path: str, data) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False)

    def client(self, open_id: str) -> TestClient:
        client = TestClient(app, base_url="http://onepiece.local")
        client.cookies.set(auth.COOKIE, auth.create_session(open_id))
        return client

    # ---------- 登录页、路由 ----------

    def test_login_page_is_public_and_home_needs_login(self) -> None:
        anonymous = TestClient(app)
        info = anonymous.get("/api/v1/auth/login-info", params={"next": "/wish/", "error": "过期了"}).json()
        self.assertIn("next=%2Fwish%2F", info["href"])
        self.assertEqual(info["error"], "过期了")
        self.assertEqual(anonymous.get("/api/v1/me").status_code, 401)
        home = anonymous.get("/?x=1", follow_redirects=False)
        self.assertEqual(home.status_code, 302)
        self.assertEqual(home.headers["location"], "/login?next=%2F%3Fx%3D1")
        self.assertNotEqual(anonymous.get("/login", follow_redirects=False).status_code, 302)

    def test_console_route_rules(self) -> None:
        routes, exclude, public = board_app.PROXY_ROUTES, board_app.PROXY_EXCLUDE, board_app.PUBLIC_ROUTES
        for path in ("/", "/login", "/wish", "/wish/", "/review/r/2026-11-04/zhuhui", "/merge/", "/api/v1/wishes", "/static/assets/a.js"):
            self.assertTrue(console.board_route(path, routes), path)
        for path in ("/api/status", "/auth/feishu", "/wishlist", "/loginx"):
            self.assertFalse(console.board_route(path, routes), path)
        self.assertTrue(console.board_route("/merge/s/abc/", exclude))
        self.assertFalse(console.board_route("/merge/", exclude))
        self.assertTrue(console.board_route("/login", public))
        self.assertTrue(console.board_route("/api/v1/auth/login-info", public))
        self.assertFalse(console.board_route("/", public))
        self.assertFalse(console.board_route("/api/v1/wishes", public))

    def test_me_and_sites(self) -> None:
        self.assertEqual(self.user.get("/api/v1/me").json()["role"], "user")
        sites = self.user.get("/api/v1/sites").json()["sites"]
        self.assertEqual([s["port"] for s in sites], ["/review", "/wish", "/bomb", "/merge", "/chat", "/help"])

    # ---------- 炸线 ----------

    def test_bombs(self) -> None:
        record = {"key": "m1", "kind": "merge", "merge": "abc", "status": "还丢着", "at": "2026-11-04T10:00:00+08:00",
                  "files": [{"path": "a/b.cs", "state": "还丢着", "commits": []}], "branches": ["main"]}
        self.write(bomb_board.HISTORY_PATH, {"records": {"m1": record}})
        data = self.user.get("/api/v1/bombs").json()
        self.assertEqual(data["records"][0]["merge"], "abc")
        self.assertIn("enabled", data["scan"])
        self.assertTrue(data["note"].startswith("判断依据"))

    # ---------- 复盘 ----------

    def seed_reviews(self) -> None:
        day = os.path.join(review_board.REVIEW_DIR, "2026-11-04")
        self.write(os.path.join(day, "zhuhui.json"), {
            "name": "朱辉", "git_author": "zhuhui", "status": "ok", "report_md": REPORT, "bomb_out_md": "",
            "commits": [{"hash": "abc1234", "branch": "main", "line": "abc|zhuhui|11-04 10:00|改 <Shader>", "diff": "+a\n-b"}]})
        self.write(os.path.join(review_board.REVIEW_DIR, "2026-11-03", "zhuhui.json"),
                   {"name": "朱辉", "git_author": "zhuhui", "status": "empty", "commits": []})

    def test_review_day_person_report(self) -> None:
        self.seed_reviews()
        day = self.user.get("/api/v1/reviews/days").json()
        self.assertEqual((day["date"], day["older"], day["newer"]), ("2026-11-04", "2026-11-03", ""))
        person = day["people"][0]
        self.assertEqual((person["name"], person["risks"], person["pill"]["cls"]), ("朱辉", 1, "p-risk"))
        self.assertEqual(day["stats"]["risk_people"], 1)
        history = self.user.get("/api/v1/reviews/people/zhuhui").json()
        self.assertEqual([row["date"] for row in history["rows"]], ["2026-11-04", "2026-11-03"])
        report = self.user.get("/api/v1/reviews/days/2026-11-04/zhuhui").json()
        self.assertIn('<span class="lbl l-change">改动</span>', report["report_html"])
        self.assertIn("&lt;Shader&gt;", report["commits"][0]["subject_html"])  # 先转义
        self.assertIn("d-add", report["commits"][0]["diff_html"])
        self.assertEqual(report["older"], "2026-11-03")
        self.assertEqual(self.user.get("/api/v1/reviews/days/2026-11-04/nobody").status_code, 404)
        self.assertEqual(self.user.get("/api/v1/reviews/people/..%2Fetc").status_code, 404)

    # ---------- 合并确认 ----------

    def test_merge_permissions_and_projects(self) -> None:
        listing = self.user.get("/api/v1/merge/sessions").json()
        self.assertEqual((listing["sessions"], listing["can_manage"]), ([], False))
        self.assertEqual(self.user.get("/api/v1/merge/projects").status_code, 403)
        self.assertEqual(self.user.post("/api/v1/merge/projects", json={"repo": self.tmp.name}).status_code, 403)
        added = self.admin.post("/api/v1/merge/projects", json={"repo": self.tmp.name}).json()
        self.assertEqual(added["projects"], [os.path.abspath(self.tmp.name)])
        status = self.admin.get("/api/v1/merge/projects").json()["projects"][0]
        self.assertEqual(status["error"], "不是 Git 仓库")
        missing = self.admin.post("/api/v1/merge/projects/capture", json={"repo": "/nope"})
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(self.admin.post("/api/v1/merge/projects/remove", json={"repo": self.tmp.name}).json()["projects"], [])

    def test_merge_upload_rejects_garbage(self) -> None:
        self.assertEqual(self.user.post("/api/v1/merge/sessions/upload", content=b"x").status_code, 403)
        bad = self.admin.post("/api/v1/merge/sessions/upload", content=b"not a zip", headers={"Content-Type": "application/zip"})
        self.assertEqual(bad.status_code, 400)
        self.assertIn("snapshot", bad.json()["message"])
        self.assertEqual(self.admin.delete("/api/v1/merge/sessions/nope").status_code, 404)

    def test_merge_only_open_sessions_for_users(self) -> None:
        for name in ("s1", "s2"):
            path = merge_web.SESSIONS_DIR / name
            path.mkdir(parents=True)
            (path / "snapshot.json").write_text(json.dumps({"id": name, "files": []}), encoding="utf-8")
        ids = lambda client: [row["id"] for row in client.get("/api/v1/merge/sessions").json()["sessions"]]
        self.assertEqual(ids(self.user), [])
        self.assertEqual(sorted(ids(self.admin)), ["s1", "s2"])
        self.assertEqual(self.user.post("/api/v1/merge/sessions/s1/open", json={"open": True}).status_code, 403)
        self.assertEqual(self.admin.post("/api/v1/merge/sessions/s1/open", json={"open": True}).json()["open"], ["s1"])
        self.assertEqual(ids(self.user), ["s1"])
        self.assertEqual(self.admin.post("/api/v1/merge/sessions/nope/open", json={"open": True}).status_code, 404)
        self.assertTrue(merge_web.can_view({"role": "user"}, "s1"))
        self.assertFalse(merge_web.can_view({"role": "user"}, "s2"))
        self.assertTrue(merge_web.can_view({"role": "admin"}, "s2"))
        self.assertFalse(merge_web.can_view(None, "s1"))
        self.admin.delete("/api/v1/merge/sessions/s1")
        self.assertEqual(merge_web.load_open(), [])

    # ---------- 后台管理 ----------

    def test_admin_requires_admin(self) -> None:
        self.assertEqual(self.user.get("/api/v1/admin/users").status_code, 403)
        self.assertEqual(self.user.post("/api/v1/admin/stop").status_code, 403)
        users = self.admin.get("/api/v1/admin/users").json()["items"]
        self.assertEqual({u["name"] for u in users}, {"管理员", "同事"})

    def test_admin_writes_and_errors(self) -> None:
        self.write(console.NOTES_PATH, {"items": [{"question": "q", "fact": "f"}]})
        self.write(console.PEOPLE_PATH, {"美术": "张三"})
        self.assertEqual(self.admin.get("/api/v1/admin/notes").json()["items"][0]["fact"], "f")
        self.assertTrue(self.admin.post("/api/v1/admin/notes/delete", json={"index": 0}).json()["ok"])
        self.assertEqual(self.admin.get("/api/v1/admin/notes").json()["items"], [])
        missing = self.admin.post("/api/v1/admin/people/delete", json={"role": "程序"})
        self.assertEqual((missing.status_code, missing.json()["message"]), (400, "没有这个职务。"))
        self.assertTrue(self.admin.post("/api/v1/admin/people/delete", json={"role": "美术"}).json()["ok"])
        promoted = self.admin.post("/api/v1/admin/users", json={"action": "admin", "open_id": "ou_user"}).json()
        self.assertIn("admin", {u["role"] for u in promoted["items"] if u["open_id"] == "ou_user"})
        cross = self.admin.post("/api/v1/admin/stop", headers={"Origin": "http://evil.example"})
        self.assertEqual(cross.status_code, 403)

    def test_admin_wish_view(self) -> None:
        status, view = console.admin_get("/api/wish", {})
        self.assertEqual(status, 200)
        self.assertEqual(self.admin.get("/api/v1/admin/wish").json()["total"], view["total"])


if __name__ == "__main__":
    unittest.main()
