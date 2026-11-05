"""热心市民 API 测试：数据、用户、会话都指到临时目录，模型用假的。

运行：.venv/bin/python -m unittest backend.test_help_api
"""
import json
import os
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from common import auth
from backend import help_store, llm
from backend.app import app
from backend.routers import help as help_router

CONFIG = {"api_key": "k", "base_url": "http://x", "model": "m", "glm_model": "m", "fallbacks": (), "codex": False}


class HelpApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (help_store.HELP_PATH, auth.USERS_PATH, auth.SESSIONS_PATH)
        help_store.HELP_PATH = os.path.join(self.tmp.name, "help.json")
        auth.USERS_PATH = os.path.join(self.tmp.name, "users.json")
        auth.SESSIONS_PATH = os.path.join(self.tmp.name, "sessions.json")
        help_router._recent.clear()
        users = {"ou_a": {"open_id": "ou_a", "name": "小明", "role": "user", "department": "美术组"},
                 "ou_b": {"open_id": "ou_b", "name": "小红", "role": "user", "department": "客户端组"},
                 "ou_admin": {"open_id": "ou_admin", "name": "管理", "role": "admin"}}
        with open(auth.USERS_PATH, "w", encoding="utf-8") as handle:
            json.dump(users, handle, ensure_ascii=False)
        self.c = {}
        for key in users:
            client = TestClient(app, base_url="http://onepiece.local")
            client.cookies.set(auth.COOKIE, auth.create_session(key))
            self.c[key] = client
        self.patches = [mock.patch.object(llm, "settings", return_value=dict(CONFIG)),
                        mock.patch.object(help_router, "wiki_text", return_value="")]
        for patch in self.patches:
            patch.start()

    def tearDown(self) -> None:
        for patch in self.patches:
            patch.stop()
        help_store.HELP_PATH, auth.USERS_PATH, auth.SESSIONS_PATH = self.saved
        self.tmp.cleanup()

    def test_requires_login(self) -> None:
        self.assertEqual(TestClient(app).get("/api/v1/help/people").status_code, 401)

    def test_people_and_profile(self) -> None:
        body = self.c["ou_b"].get("/api/v1/help/people").json()
        self.assertEqual({p["id"] for p in body["people"]}, {"ou_a", "ou_b", "ou_admin"})
        self.assertIn("美术组", body["departments"])
        r = self.c["ou_b"].put("/api/v1/help/people/me", json={"skills": "打包、编译报错", "tags": ["打包", "打包", "Xcode"]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["helper"])
        self.assertEqual(r.json()["tags"], ["打包", "Xcode"])
        people = self.c["ou_a"].get("/api/v1/help/people").json()["people"]
        self.assertEqual(people[0]["id"], "ou_b")  # 热心市民排前面
        # 普通人不能改别人、不能手动加人，不能删别人的
        self.assertEqual(self.c["ou_a"].put("/api/v1/help/people/ou_b", json={"name": "x"}).status_code, 403)
        self.assertEqual(self.c["ou_a"].post("/api/v1/help/people", json={"name": "x"}).status_code, 403)
        self.assertEqual(self.c["ou_a"].delete("/api/v1/help/people/ou_b").status_code, 403)
        r = self.c["ou_admin"].post("/api/v1/help/people", json={"name": "老王", "department": "IT", "skills": "电脑网络"})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertTrue(r.json()["manual"])
        self.assertEqual(self.c["ou_b"].delete("/api/v1/help/people/ou_b").status_code, 200)

    def test_question_bank(self) -> None:
        self.c["ou_b"].put("/api/v1/help/people/me", json={"skills": "打包"})
        r = self.c["ou_a"].post("/api/v1/help/questions", json={
            "title": "Unity 打包失败报错", "keywords": ["打包", "build"], "answer": "先清 Library",
            "department": "客户端组", "helpers": ["ou_b", "不存在"]})
        self.assertEqual(r.status_code, 201, r.text)
        question = r.json()
        self.assertEqual(question["helpers"], ["ou_b"])
        self.c["ou_a"].post("/api/v1/help/questions", json={"title": "电脑连不上网", "keywords": ["网络"]})
        found = self.c["ou_b"].get("/api/v1/help/questions", params={"q": "我打包一直失败怎么办"}).json()["questions"]
        self.assertEqual([q["title"] for q in found], ["Unity 打包失败报错"])
        self.assertEqual(found[0]["matched"], ["打包"])
        self.assertEqual(self.c["ou_b"].get("/api/v1/help/questions", params={"q": "天气"}).json()["questions"], [])
        # 只有作者和管理员能改、删
        url = f"/api/v1/help/questions/{question['id']}"
        self.assertEqual(self.c["ou_b"].put(url, json={"title": "改掉"}).status_code, 403)
        self.assertEqual(self.c["ou_admin"].put(url, json={"title": "Unity 打包失败", "keywords": ["打包"]}).status_code, 200)
        self.assertEqual(self.c["ou_b"].post(url + "/hit").status_code, 200)
        self.assertEqual(help_store.find_question(question["id"])["hits"], 1)
        self.assertEqual(self.c["ou_a"].delete(url).status_code, 200)
        self.assertEqual(self.c["ou_a"].delete(url).status_code, 404)

    def test_analyze(self) -> None:
        self.c["ou_b"].put("/api/v1/help/people/me", json={"skills": "打包"})
        self.c["ou_a"].post("/api/v1/help/questions", json={"title": "打包失败", "keywords": ["打包"], "helpers": ["ou_b"]})
        seen = {}

        def fake(messages):
            seen["messages"] = messages
            yield {"model": "fake"}
            yield {"delta": '好的：```json\n{"summary": "打包问题", "department": "客户端组", '}
            yield {"delta": '"people": [{"id": "ou_b", "reason": "管打包"}, {"id": "瞎编"}, {"id": "ou_b"}], "tips": "清缓存"}\n```'}

        with mock.patch.object(llm, "stream_chat", side_effect=fake):
            r = self.c["ou_a"].post("/api/v1/help/analyze", json={"problem": "打包卡住了"})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["summary"], "打包问题")
        self.assertEqual([p["person"]["id"] for p in body["picks"]], ["ou_b"])
        self.assertEqual(body["questions"][0]["title"], "打包失败")
        prompt = "\n".join(m["content"] for m in seen["messages"])
        self.assertIn("id=ou_b | 小红 | 客户端组 | 热心市民", prompt)
        self.assertIn("小明（美术组）", prompt)
        self.assertNotIn("id=ou_a |", prompt)

    def test_analyze_plain_text_and_errors(self) -> None:
        with mock.patch.object(llm, "stream_chat", side_effect=lambda m: iter([{"delta": "找客户端组吧"}])):
            body = self.c["ou_a"].post("/api/v1/help/analyze", json={"problem": "报错了"}).json()
        self.assertEqual(body["summary"], "找客户端组吧")
        self.assertEqual(body["picks"], [])

        def broken(_messages):
            raise llm.LLMError("超时", False)
            yield  # noqa

        with mock.patch.object(llm, "stream_chat", side_effect=broken):
            self.assertEqual(self.c["ou_a"].post("/api/v1/help/analyze", json={"problem": "报错了"}).status_code, 502)
        with mock.patch.object(help_router, "RATE_LIMIT", 0):
            self.assertEqual(self.c["ou_a"].post("/api/v1/help/analyze", json={"problem": "报错了"}).status_code, 429)


if __name__ == "__main__":
    unittest.main()
