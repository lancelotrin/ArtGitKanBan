"""心愿墙 API 测试：数据、用户和会话全部指到临时目录，不碰真实的 wishes.json。

运行：.venv/bin/python -m unittest backend.test_wish_api
"""
import json
import os
import tempfile
import unittest

from fastapi.testclient import TestClient

from common import auth
from boards import wish_board
from backend.app import app


class WishApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (wish_board.WISH_PATH, auth.USERS_PATH, auth.SESSIONS_PATH)
        wish_board.WISH_PATH = os.path.join(self.tmp.name, "wishes.json")
        auth.USERS_PATH = os.path.join(self.tmp.name, "users.json")
        auth.SESSIONS_PATH = os.path.join(self.tmp.name, "sessions.json")
        wish_board._create_log.clear()
        users = {"ou_a": {"open_id": "ou_a", "name": "小明", "role": "user"},
                 "ou_b": {"open_id": "ou_b", "name": "小红", "role": "user"},
                 "ou_admin": {"open_id": "ou_admin", "name": "管 理", "role": "admin"}}
        with open(auth.USERS_PATH, "w", encoding="utf-8") as handle:
            json.dump(users, handle, ensure_ascii=False)
        self.clients = {}
        for key in users:
            client = TestClient(app, base_url="http://onepiece.local")
            client.cookies.set(auth.COOKIE, auth.create_session(key))
            self.clients[key] = client

    def tearDown(self) -> None:
        wish_board.WISH_PATH, auth.USERS_PATH, auth.SESSIONS_PATH = self.saved
        self.tmp.cleanup()

    def create(self, who: str = "ou_a", title: str = "复盘导出 Excel") -> dict:
        response = self.clients[who].post("/api/v1/wishes", json={"title": title, "detail": "说明", "role": "程序"})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["wish"]

    def test_requires_login(self) -> None:
        anonymous = TestClient(app)
        self.assertEqual(anonymous.get("/api/v1/wishes").status_code, 401)
        page = anonymous.get("/wish/", follow_redirects=False)
        self.assertEqual(page.status_code, 302)
        self.assertTrue(page.headers["location"].startswith("/login?"))

    def test_create_uses_login_name(self) -> None:
        wish = self.create()
        self.assertEqual(wish["author"], "小明")
        self.assertEqual(wish["role"], "程序")
        listing = self.clients["ou_b"].get("/api/v1/wishes").json()
        self.assertEqual([w["id"] for w in listing["wishes"]], [wish["id"]])
        self.assertIn("美术", listing["roles"])
        self.assertEqual(listing["me"], "小红")

    def test_vote_take_done_flow(self) -> None:
        wish = self.create()
        own_vote = self.clients["ou_a"].post(f"/api/v1/wishes/{wish['id']}/vote")
        self.assertEqual(own_vote.status_code, 400)
        self.assertIn("message", own_vote.json())
        voted = self.clients["ou_b"].post(f"/api/v1/wishes/{wish['id']}/vote").json()["wish"]
        self.assertEqual(voted["votes"], ["小红"])
        taken = self.clients["ou_b"].post(f"/api/v1/wishes/{wish['id']}/take").json()["wish"]
        self.assertEqual((taken["status"], taken["taker"]), ("doing", "小红"))
        self.assertEqual(self.clients["ou_a"].delete(f"/api/v1/wishes/{wish['id']}").status_code, 400)
        finished = self.clients["ou_a"].post(f"/api/v1/wishes/{wish['id']}/done").json()["wish"]
        self.assertEqual(finished["status"], "done")

    def test_admin_name_with_space_and_withdraw(self) -> None:
        wish = self.create("ou_admin")
        self.assertEqual(wish["author"], "管理")
        other = self.create("ou_a", "另一个心愿")
        self.assertEqual(self.clients["ou_admin"].delete(f"/api/v1/wishes/{other['id']}").status_code, 200)

    def test_missing_wish_is_404(self) -> None:
        self.assertEqual(self.clients["ou_a"].post("/api/v1/wishes/999/take").status_code, 404)

    def test_validation_and_cross_site(self) -> None:
        short = self.clients["ou_a"].post("/api/v1/wishes", json={"title": "x"})
        self.assertEqual(short.status_code, 422)
        self.assertTrue(short.json()["message"].startswith("参数不对"))
        cross = self.clients["ou_a"].post("/api/v1/wishes", json={"title": "跨站来的"},
                                          headers={"Origin": "http://evil.example"})
        self.assertEqual(cross.status_code, 403)

    def test_paused_create(self) -> None:
        wish_board.admin_apply("allow_create", {"value": False})
        response = self.clients["ou_a"].post("/api/v1/wishes", json={"title": "暂停时许愿"})
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
