"""炸线、复盘、合并确认、后台管理、登录页和路由表的测试。数据全部指到临时目录。

运行：.venv/bin/python -m unittest backend.test_boards_api
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from unittest import mock

from fastapi.testclient import TestClient

from common import auth
from boards import board_access
from boards import bomb_board
from boards import doc_watch_board
from bot import doc_watch
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
            (board_access, "PRIVATE_PATH"): board_access.PRIVATE_PATH,
            (review_board, "REVIEW_SUBS_PATH"): review_board.REVIEW_SUBS_PATH,
            (console, "NOTES_PATH"): console.NOTES_PATH, (console, "PEOPLE_PATH"): console.PEOPLE_PATH,
            (doc_watch_board, "ROOT"): doc_watch_board.ROOT, (doc_watch, "CONFIG_PATH"): doc_watch.CONFIG_PATH,
        }
        auth.USERS_PATH = os.path.join(root, "users.json")
        auth.SESSIONS_PATH = os.path.join(root, "sessions.json")
        bomb_board.HISTORY_PATH = os.path.join(root, "bomb_history.json")
        review_board.REVIEW_DIR = os.path.join(root, "reviews")
        merge_web.SESSIONS_DIR = Path(root) / "sessions"
        merge_web.PROJECTS_PATH = os.path.join(root, "merge_projects.json")
        merge_web.OPEN_PATH = os.path.join(root, "merge_open.json")
        board_access.PRIVATE_PATH = os.path.join(root, "board_private.json")
        review_board.REVIEW_SUBS_PATH = os.path.join(root, "review_subs.json")
        console.NOTES_PATH = os.path.join(root, "notes.json")
        console.PEOPLE_PATH = os.path.join(root, "people.json")
        doc_watch_board.ROOT = os.path.join(root, "doc_watch")
        doc_watch.CONFIG_PATH = os.path.join(root, "doc_watch", "config.json")
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
        self.assertEqual([s["port"] for s in sites], ["/review", "/wish", "/bomb", "/merge", "/chat", "/docwatch", "/help", "/meta"])

    # ---------- meta 检查 ----------

    def test_meta(self) -> None:
        repo = os.path.join(self.tmp.name, "repo")
        os.makedirs(os.path.join(repo, "Assets"))
        git = lambda *args: subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True)
        git("init", "-q", "-b", "main")
        git("config", "user.name", "tester")
        git("config", "user.email", "tester@example.com")
        meta_path = os.path.join(repo, "Assets", "a.png.meta")
        with open(meta_path, "w", encoding="utf-8") as handle:
            handle.write("fileFormatVersion: 2\nguid: " + "a" * 32 + "\nmaxTextureSize: 2048\n")
        git("add", "-A")
        git("commit", "-q", "-m", "init")
        with open(meta_path, "w", encoding="utf-8") as handle:
            handle.write("fileFormatVersion: 2\nguid: " + "a" * 32 + "\nmaxTextureSize: 1024\n")
        with mock.patch("bot.echo_bot.get_git_root", return_value=repo), \
                mock.patch.object(console, "META_LOG_PATH", os.path.join(self.tmp.name, "meta_fix.log")):
            data = self.user.get("/api/v1/meta").json()
            self.assertEqual([(item["kind"], item["path"]) for item in data["items"]], [("import", "Assets/a.png.meta")])
            self.assertIn("导入设置 1 个", self.user.get("/api/v1/meta/log").json()["log"])
            self.assertEqual(self.user.post("/api/v1/meta/apply").status_code, 403)
            data = self.admin.post("/api/v1/meta/apply").json()
            self.assertTrue(data["ok"])
            self.assertEqual(data["scan"]["items"], [])
            self.assertIn("提交导入设置 Assets/a.png.meta", self.user.get("/api/v1/meta/log").json()["log"])
        board_access.set_hidden("/meta", True)
        self.assertEqual(self.user.get("/api/v1/meta").status_code, 403)

    # ---------- 炸线 ----------

    def test_bombs(self) -> None:
        record = {"key": "m1", "kind": "merge", "merge": "abc", "status": "还丢着", "at": "2026-11-04T10:00:00+08:00",
                  "files": [{"path": "a/b.cs", "state": "还丢着", "commits": []}], "branches": ["main"]}
        self.write(bomb_board.HISTORY_PATH, {"records": {"m1": record}})
        data = self.user.get("/api/v1/bombs").json()
        self.assertEqual(data["records"][0]["merge"], "abc")
        self.assertIn("enabled", data["scan"])
        self.assertTrue(data["note"].startswith("判断依据"))

    def test_resolve_bomb(self) -> None:
        record = {"key": "merge:abc", "kind": "merge", "merge": "abc", "status": "还丢着", "at": "2026-11-04T10:00:00+08:00",
                  "files": [{"path": "a/b.cs", "state": "还丢着", "commits": []}], "branches": ["main"]}
        self.write(bomb_board.HISTORY_PATH, {"records": {"merge:abc": record}})
        self.assertEqual(len(bomb_board.open_results()[0][1]), 1)
        data = self.user.post("/api/v1/bombs/resolve", json={"key": "merge:abc", "resolved": True}).json()
        self.assertTrue(data["records"][0]["resolved"])
        self.assertTrue(data["records"][0]["resolved_by"])
        self.assertEqual(bomb_board.open_results(), [])
        data = self.user.post("/api/v1/bombs/resolve", json={"key": "merge:abc", "resolved": False}).json()
        self.assertFalse(data["records"][0]["resolved"])
        self.assertEqual(self.user.post("/api/v1/bombs/resolve", json={"key": "merge:nope"}).status_code, 404)

    # ---------- 监修改动 ----------

    def test_docwatch(self) -> None:
        root = doc_watch_board.ROOT
        self.write(os.path.join(root, "config.json"), {"enabled": True, "docs": [{"name": "监修同步", "wikiToken": "TOK"}],
                                                       "mapping": "mapping.json", "clarify": {"asker": "张天佑"}})
        self.write(os.path.join(root, "mapping.json"), {
            "routes": [{"key": "battle", "name": "战斗", "owner": "殷琦", "keywords": ["技能"]}],
            "features": [{"name": "SSR 艾斯", "route": "battle", "owner": "郭彦辰", "keywords": ["艾斯"]}],
            "owners": {"殷琦": {"role": "战斗组长"}}})
        rows = [{"time": "2026-11-04 13:00:39", "doc": "监修同步", "token": "TOK", "condensed": {},
                 "items": [{"kind": "added", "section": "议题 / 艾斯", "text": "艾斯技能icon"}]},
                {"time": "2026-11-05 10:00:00", "doc": "监修同步", "token": "TOK", "condensed": {},
                 "recipients": {"殷琦": "OK", "万汩": "SKIP"}, "items": [{"kind": "changed", "section": "议题 / 布景", "text": "技能"}]}]
        with open(os.path.join(root, "changes-log.jsonl"), "w", encoding="utf-8") as handle:
            handle.write("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")
        with open(os.path.join(root, "notify-log.jsonl"), "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"time": "2026-11-04 13:00:38", "token": "TOK", "owner": "郭彦辰", "count": 1}, ensure_ascii=False) + "\n")
        self.write(os.path.join(root, "analysis.json"), {"TOK@2026-11-05 10:00:00": {
            "summary": "布景技能调整", "people": [{"name": "殷琦", "reason": "战斗", "role": "战斗组长"}],
            "sections": [{"section": "议题 / 布景", "points": ["技能改了"], "features": [], "people": []}]}})
        self.write(os.path.join(root, "pending.json"), {"议题 / 其他": {"docTitle": "监修同步", "text": "x", "link": "", "askedAt": "2026-11-05 11:00:00"}})
        data = self.user.get("/api/v1/docwatch").json()
        newest, older = data["batches"]
        self.assertEqual((newest["source"], newest["summary"], newest["recipients"]["万汩"]), ("ai", "布景技能调整", "SKIP"))
        self.assertEqual(newest["sections"][0]["points"], ["技能改了"])
        self.assertEqual(older["source"], "rule")  # 没分析过的按规则推算
        self.assertEqual({p["name"] for p in older["people"]}, {"殷琦", "郭彦辰"})
        self.assertEqual(older["features"], ["SSR 艾斯"])
        self.assertEqual(older["recipients"], {"郭彦辰": "OK"})  # 老批次按时间对上通知记录
        self.assertEqual(data["pending"][0]["section"], "议题 / 其他")
        board_access.set_hidden("/docwatch", True)
        self.assertEqual(self.user.get("/api/v1/docwatch").status_code, 403)
        self.assertEqual(self.admin.get("/api/v1/docwatch").status_code, 200)

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

    def test_review_mine_only(self) -> None:
        self.seed_reviews()
        day = os.path.join(review_board.REVIEW_DIR, "2026-11-04")
        self.write(os.path.join(day, "tongshi.json"), {"name": "同事", "git_author": "tongshi", "status": "empty", "commits": []})
        ports = lambda client: [s["port"] for s in client.get("/api/v1/sites").json()["sites"]]
        # 默认关着：所有人都看得到所有人
        self.assertEqual(len(self.user.get("/api/v1/reviews/days").json()["people"]), 2)
        self.assertEqual(self.user.post("/api/v1/admin/sites", json={"action": "private", "port": "/review", "on": True}).status_code, 403)
        sites = self.admin.post("/api/v1/admin/sites", json={"action": "private", "port": "/review", "on": True}).json()["sites"]
        self.assertTrue(sites[0]["private"])
        self.assertEqual(self.admin.post("/api/v1/admin/sites", json={"action": "private", "port": "/wish", "on": True}).status_code, 400)
        # 开了、自己不在名单里：看板不显示，接口 403
        self.assertNotIn("/review", ports(self.user))
        self.assertEqual(self.user.get("/api/v1/reviews/days").status_code, 403)
        # 进了名单：只看得到自己的
        self.write(review_board.REVIEW_SUBS_PATH, {"subs": [{"name": "同事", "git_author": "tongshi", "open_id": "ou_user"},
                                                         {"name": "朱辉", "git_author": "zhuhui", "open_id": "ou_zh"}]})
        self.assertIn("/review", ports(self.user))
        mine = self.user.get("/api/v1/reviews/days").json()
        self.assertEqual(([p["file"] for p in mine["people"]], mine["dates"], mine["mine_only"]), (["tongshi"], ["2026-11-04"], True))
        self.assertEqual(self.user.get("/api/v1/reviews/days/2026-11-04/zhuhui").status_code, 403)
        self.assertEqual(self.user.get("/api/v1/reviews/people/zhuhui").status_code, 403)
        self.assertEqual(self.user.get("/api/v1/reviews/days/2026-11-04/tongshi").status_code, 200)
        # 管理员不受影响
        self.assertEqual(len(self.admin.get("/api/v1/reviews/days").json()["people"]), 2)
        self.admin.post("/api/v1/admin/sites", json={"action": "private", "port": "/review", "on": False})
        self.assertEqual(len(self.user.get("/api/v1/reviews/days").json()["people"]), 2)

    def test_board_closed_for_users(self) -> None:
        ports = lambda client: [s["port"] for s in client.get("/api/v1/sites").json()["sites"]]
        self.assertTrue(all(s["open"] for s in self.admin.get("/api/v1/sites").json()["sites"]))
        self.assertEqual(self.user.post("/api/v1/admin/sites", json={"action": "open", "port": "/wish", "on": False}).status_code, 403)
        self.assertEqual(self.admin.post("/api/v1/admin/sites", json={"action": "open", "port": "/nope", "on": False}).status_code, 400)
        # 「只看自己的」和「开放」是两个开关，互不覆盖
        self.admin.post("/api/v1/admin/sites", json={"action": "private", "port": "/review", "on": True})
        sites = self.admin.post("/api/v1/admin/sites", json={"action": "open", "port": "/wish", "on": False}).json()["sites"]
        self.assertEqual({s["port"]: s["open"] for s in sites}["/wish"], False)
        self.assertTrue(board_access.is_private("/review"))
        # 普通用户：导航里没有，接口 403，closed 里带上
        self.assertNotIn("/wish", ports(self.user))
        self.assertEqual(self.user.get("/api/v1/sites").json()["closed"], ["/wish"])
        self.assertEqual(self.user.get("/api/v1/wishes").status_code, 403)
        self.assertEqual(self.user.post("/api/v1/wishes", json={"title": "x"}).status_code, 403)
        self.assertEqual(self.user.get("/api/v1/bombs").status_code, 200)
        # 管理员照常
        self.assertIn("/wish", ports(self.admin))
        self.assertEqual(self.admin.get("/api/v1/wishes").status_code, 200)
        self.assertEqual(self.admin.get("/api/v1/sites").json()["closed"], [])
        self.admin.post("/api/v1/admin/sites", json={"action": "open", "port": "/wish", "on": True})
        self.assertIn("/wish", ports(self.user))
        self.assertEqual(self.user.get("/api/v1/wishes").status_code, 200)

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
