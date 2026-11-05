"""AI 聊天测试：每个人只能看到自己的对话；流式回答会存下来；模型调用按 OpenAI 流式格式解析。

运行：.venv/bin/python -m unittest backend.test_chat_api
"""
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from fastapi.testclient import TestClient

from common import auth
from backend import chat_store, llm
from backend.app import app
from backend.routers import chat as chat_router

KEY = {"api_key": "test-key", "base_url": "http://127.0.0.1:1", "model": "glm-test", "fallbacks": ("glm-backup",)}


def events(response) -> list:
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


class ChatApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (auth.USERS_PATH, auth.SESSIONS_PATH, chat_store.DB_PATH)
        auth.USERS_PATH = os.path.join(self.tmp.name, "users.json")
        auth.SESSIONS_PATH = os.path.join(self.tmp.name, "sessions.json")
        chat_store.DB_PATH = os.path.join(self.tmp.name, "chats.sqlite3")
        chat_router._recent.clear()
        with open(auth.USERS_PATH, "w", encoding="utf-8") as handle:
            json.dump({"ou_a": {"open_id": "ou_a", "name": "小明", "role": "user"},
                       "ou_admin": {"open_id": "ou_admin", "name": "管理员", "role": "admin"}}, handle, ensure_ascii=False)
        self.a = self.client("ou_a")
        self.admin = self.client("ou_admin")
        self.patches = [mock.patch.object(llm, "settings", return_value=dict(KEY)),
                        mock.patch("bot.echo_bot.now_text", return_value="2026年11月05日"),
                        mock.patch.object(chat_router, "wiki_context", return_value="")]
        for patch in self.patches:
            patch.start()

    def tearDown(self) -> None:
        for patch in self.patches:
            patch.stop()
        auth.USERS_PATH, auth.SESSIONS_PATH, chat_store.DB_PATH = self.saved
        self.tmp.cleanup()

    def client(self, open_id: str) -> TestClient:
        client = TestClient(app, base_url="http://onepiece.local")
        client.cookies.set(auth.COOKIE, auth.create_session(open_id))
        return client

    def ask(self, client: TestClient, content: str, conversation_id: str = "", reply=("你好", "！")):
        def fake(messages):
            self.last_context = messages
            yield {"model": "glm-test"}
            for part in reply:
                yield {"delta": part}
        with mock.patch.object(llm, "stream_chat", side_effect=fake):
            body = {"content": content, **({"conversation_id": conversation_id} if conversation_id else {})}
            return self.a.post("/api/v1/chat/messages", json=body) if client is self.a else client.post("/api/v1/chat/messages", json=body)

    def test_stream_and_saved_history(self) -> None:
        response = self.ask(self.a, "第一个问题")
        self.assertEqual(response.headers["content-type"].split(";")[0], "text/event-stream")
        got = events(response)
        conversation = got[0]["conversation"]
        self.assertEqual(conversation["title"], "第一个问题")
        self.assertEqual("".join(e.get("delta", "") for e in got), "你好！")
        self.assertEqual(got[-1], {"done": True})
        saved = self.a.get(f"/api/v1/chat/conversations/{conversation['id']}").json()
        self.assertEqual([(m["role"], m["content"], m["model"]) for m in saved["messages"]],
                         [("user", "第一个问题", ""), ("assistant", "你好！", "glm-test")])
        self.ask(self.a, "接着问", conversation["id"])
        self.assertEqual([m["role"] for m in self.last_context], ["system", "user", "assistant", "user"])
        self.assertIn("2026年11月05日", self.last_context[0]["content"])
        self.assertEqual(len(self.a.get("/api/v1/chat/conversations").json()["conversations"]), 1)

    def test_others_cannot_see_or_touch(self) -> None:
        conversation_id = events(self.ask(self.a, "我的秘密"))[0]["conversation"]["id"]
        self.assertEqual(self.admin.get("/api/v1/chat/conversations").json()["conversations"], [])
        self.assertEqual(self.admin.get(f"/api/v1/chat/conversations/{conversation_id}").status_code, 404)
        self.assertEqual(self.ask(self.admin, "插一句", conversation_id).status_code, 404)
        self.assertEqual(self.admin.patch(f"/api/v1/chat/conversations/{conversation_id}", json={"title": "x"}).status_code, 404)
        self.assertEqual(self.admin.delete(f"/api/v1/chat/conversations/{conversation_id}").status_code, 404)
        self.assertEqual(len(self.a.get(f"/api/v1/chat/conversations/{conversation_id}").json()["messages"]), 2)
        self.assertEqual(TestClient(app).get("/api/v1/chat/conversations").status_code, 401)

    def test_rename_and_delete_own(self) -> None:
        conversation_id = events(self.ask(self.a, "问题"))[0]["conversation"]["id"]
        self.assertEqual(self.a.patch(f"/api/v1/chat/conversations/{conversation_id}", json={"title": "新名字"}).status_code, 200)
        self.assertEqual(self.a.get("/api/v1/chat/conversations").json()["conversations"][0]["title"], "新名字")
        self.assertEqual(self.a.delete(f"/api/v1/chat/conversations/{conversation_id}").status_code, 200)
        self.assertEqual(self.a.get(f"/api/v1/chat/conversations/{conversation_id}").status_code, 404)

    def test_model_error_is_saved_but_not_reused(self) -> None:
        def broken(_messages):
            raise llm.LLMError("429 Too Many Requests", False)
            yield  # noqa: unreachable，让它成为生成器
        with mock.patch.object(llm, "stream_chat", side_effect=broken):
            got = events(self.a.post("/api/v1/chat/messages", json={"content": "问"}))
        self.assertIn("429", got[-1]["error"])
        conversation_id = got[0]["conversation"]["id"]
        saved = self.a.get(f"/api/v1/chat/conversations/{conversation_id}").json()["messages"]
        self.assertEqual(saved[-1]["status"], "error")
        self.ask(self.a, "再问", conversation_id)
        self.assertEqual([m["role"] for m in self.last_context], ["system", "user", "user"])

    def test_not_configured_and_rate_limit(self) -> None:
        with mock.patch.object(llm, "settings", return_value=dict(KEY, api_key="")):
            response = self.a.post("/api/v1/chat/messages", json={"content": "问"})
            self.assertEqual(response.status_code, 503)
            self.assertFalse(self.a.get("/api/v1/chat/info").json()["enabled"])
        with mock.patch.object(chat_router, "RATE_LIMIT", 2):
            self.assertEqual(self.ask(self.a, "1").status_code, 200)
            self.assertEqual(self.ask(self.a, "2").status_code, 200)
            self.assertEqual(self.ask(self.a, "3").status_code, 429)
        self.assertEqual(self.a.post("/api/v1/chat/messages", json={"content": ""}).status_code, 422)


class FakeModel(BaseHTTPRequestHandler):
    """模拟智谱的流式 /chat/completions。模型名 glm-bad 返回 500，用来测备用模型。"""

    def log_message(self, *args) -> None:
        return

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeModel.seen.append((body["model"], self.headers.get("Authorization"), body.get("stream")))
        if body["model"] == "glm-bad":
            self.send_response(500)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for part in ("你", "好<|begin_of_box|>", "呀"):
            self.wfile.write(f"data: {json.dumps({'choices': [{'delta': {'content': part}}]})}\n\n".encode())
        self.wfile.write(b'data: {"choices":[{"delta":{}}],"usage":{"prompt_tokens":3,"completion_tokens":2}}\n\n')
        self.wfile.write(b"data: [DONE]\n\n")


class StreamParseTest(unittest.TestCase):
    def test_streams_and_falls_back(self) -> None:
        FakeModel.seen = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), FakeModel)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        config = {"api_key": "k", "base_url": f"http://127.0.0.1:{server.server_port}/v4", "model": "glm-bad",
                  "fallbacks": ("glm-good",)}
        try:
            with mock.patch.object(llm, "settings", return_value=config), mock.patch.object(llm, "note") as note:
                items = list(llm.stream_chat([{"role": "user", "content": "hi"}]))
        finally:
            server.shutdown()
        self.assertEqual(items, [{"model": "glm-good"}, {"delta": "你"}, {"delta": "好"}, {"delta": "呀"}])
        self.assertEqual(FakeModel.seen, [("glm-bad", "Bearer k", True), ("glm-good", "Bearer k", True)])
        self.assertEqual(note.call_args_list[0].args[:2], ("glm-bad", False))
        self.assertEqual(note.call_args_list[1], mock.call("glm-good", True, prompt_tokens=3, completion_tokens=2))


class WikiContextTest(unittest.TestCase):
    def test_search_and_context(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            docs = [("T1", ["美术组", "UI 管线", "UI制作流程规范"], "切图按 2 倍尺寸导出，命名用 ui_ 前缀。"),
                    ("T2", ["策划组", "战斗"], "伙伴合击的冷却时间是 30 秒。")]
            for token, path, text in docs:
                with open(os.path.join(root, token + ".json"), "w", encoding="utf-8") as handle:
                    json.dump({"objToken": token, "title": path[-1], "path": path, "url": f"https://x/wiki/{token}",
                               "text": text}, handle, ensure_ascii=False)
            found = chat_router.wiki_kb.search("UI 切图 怎么命名", root=root)
            self.assertEqual([d["objToken"] for d in found["docs"]][:1], ["T1"])
            self.assertIn("《美术组 / UI 管线 / UI制作流程规范》 https://x/wiki/T1", found["context"])
            with mock.patch.object(chat_router.wiki_kb, "WIKI_DIR", root), \
                    mock.patch("bot.echo_bot.now_text", return_value="今天"):
                # 短追问连上一个问题一起搜
                messages = chat_router.context([
                    {"role": "user", "content": "伙伴合击冷却多久", "status": "ok"},
                    {"role": "assistant", "content": "30 秒", "status": "ok"},
                    {"role": "user", "content": "确定吗", "status": "ok"}])
        self.assertEqual([m["role"] for m in messages], ["system", "system", "user", "assistant", "user"])
        self.assertIn("伙伴合击", messages[1]["content"])


class ContextWeightTest(unittest.TestCase):
    def build(self, rounds: int, answer: str = "回答") -> list:
        history = []
        for n in range(rounds):
            history += [{"role": "user", "content": f"问题{n}", "status": "ok"},
                        {"role": "assistant", "content": f"{answer}{n}", "status": "ok"}]
        return history + [{"role": "user", "content": "当前问题", "status": "ok"}]

    def context(self, messages: list) -> list:
        with mock.patch("bot.echo_bot.now_text", return_value="今天"), \
                mock.patch.object(chat_router, "wiki_context", return_value=""):
            return chat_router.context(messages)

    def test_recent_verbatim_older_compressed(self) -> None:
        got = self.context(self.build(6, answer="```python\nprint(1)\n```\n回答"))
        self.assertEqual([m["role"] for m in got], ["system", "system"] + ["user", "assistant"] * 3 + ["user"])
        self.assertEqual(got[-1]["content"], "当前问题")
        self.assertEqual(got[2]["content"], "问题3")  # 最近 3 轮原样
        self.assertIn("```python", got[3]["content"])
        older = got[1]["content"]
        self.assertIn("已压缩", older)
        self.assertIn("- 问：问题0 ／ 答：[代码略] 回答0", older)
        self.assertNotIn("问题3", older)

    def test_short_chat_has_no_older_block(self) -> None:
        got = self.context(self.build(2))
        self.assertEqual([m["role"] for m in got], ["system", "user", "assistant", "user", "assistant", "user"])

    def test_long_answers_and_budget(self) -> None:
        long_answer = "开头" + "很长" * 4000 + "结论"
        got = self.context(self.build(40, answer=long_answer))
        recent_answer = got[-2]["content"]
        self.assertTrue(recent_answer.startswith("开头") and recent_answer.endswith("结论39"))
        self.assertIn("中间省略", recent_answer)
        older = got[1]["content"]
        self.assertLessEqual(len(older), chat_router.OLDER_CHARS + 50)
        self.assertIn("\n（更早的", older)
        self.assertLessEqual(sum(len(m["content"]) for m in got[2:]), chat_router.RECENT_CHARS + 100)

    def test_asker_and_department_in_prompt(self) -> None:
        with mock.patch("bot.echo_bot.now_text", return_value="今天"), \
                mock.patch.object(chat_router, "wiki_context", return_value=""):
            got = chat_router.context(self.build(0), {"name": "小林", "department": "美术组"})
            plain = chat_router.context(self.build(0), {"name": "小林"})
        self.assertIn("正在提问的同事：小林（美术组）", got[0]["content"])
        self.assertIn("部门的工作习惯", got[0]["content"])
        self.assertIn("正在提问的同事：小林。", plain[0]["content"])
        self.assertNotIn("部门的工作习惯", plain[0]["content"])

    def test_error_answers_are_skipped(self) -> None:
        got = self.context([{"role": "user", "content": "问", "status": "ok"},
                            {"role": "assistant", "content": "模型没答上来", "status": "error"},
                            {"role": "user", "content": "再问", "status": "ok"}])
        self.assertEqual([(m["role"], m["content"]) for m in got[1:]], [("user", "问"), ("user", "再问")])


class CodexChatTest(unittest.TestCase):
    CONFIG = {"api_key": "", "base_url": "http://127.0.0.1:1", "model": "codex gpt-test", "glm_model": "glm-test",
              "fallbacks": (), "codex": True}

    def test_codex_answers_in_one_piece(self) -> None:
        with mock.patch.object(llm, "settings", return_value=dict(self.CONFIG)), mock.patch.object(llm, "note") as note, \
                mock.patch.object(llm.codex_cli, "ask", return_value=("整段回答", {"input_tokens": 5, "output_tokens": 2})):
            items = list(llm.stream_chat([{"role": "user", "content": "hi"}]))
        self.assertEqual(items, [{"model": "codex gpt-test"}, {"delta": "整段回答"}])
        self.assertEqual(note.call_args, mock.call("codex gpt-test", True, prompt_tokens=5, completion_tokens=2))
        self.assertTrue(llm.enabled(self.CONFIG))

    def test_codex_runs_in_project_and_masks_secrets(self) -> None:
        env = {"FEISHU_APP_SECRET": "s3cr3t-value", "PROJECT_ROOT": "/proj"}
        with mock.patch.object(llm, "settings", return_value=dict(self.CONFIG)), mock.patch.object(llm, "note"), \
                mock.patch.object(llm, "project", return_value=("/proj", ["main"])), \
                mock.patch("common.auth.read_env_file", return_value=env), \
                mock.patch.object(llm.codex_cli, "ask", return_value=("密钥是 s3cr3t-value", {})) as ask:
            items = list(llm.stream_chat([{"role": "user", "content": "hi"}]))
        self.assertEqual(ask.call_args.kwargs, {"project_dir": "/proj", "branches": ["main"],
                                                "docs_dir": llm.wiki_kb.WIKI_DIR})
        self.assertEqual(items[1], {"delta": "密钥是 <FEISHU_APP_SECRET 已隐藏>"})

    def test_codex_failure_without_key_is_an_error(self) -> None:
        with mock.patch.object(llm, "settings", return_value=dict(self.CONFIG)), mock.patch.object(llm, "note"), \
                mock.patch.object(llm.codex_cli, "ask", side_effect=llm.codex_cli.CodexError("没登录")):
            with self.assertRaises(llm.LLMError):
                list(llm.stream_chat([{"role": "user", "content": "hi"}]))

    def test_codex_failure_falls_back_to_glm(self) -> None:
        config = dict(self.CONFIG, api_key="k")
        with mock.patch.object(llm, "settings", return_value=config), mock.patch.object(llm, "note"), \
                mock.patch.object(llm.codex_cli, "ask", side_effect=llm.codex_cli.CodexTimeout("超时")), \
                mock.patch.object(llm, "stream_once", return_value=iter(["智谱答"])) as glm:
            items = list(llm.stream_chat([{"role": "user", "content": "hi"}]))
        self.assertEqual(items, [{"model": "glm-test"}, {"delta": "智谱答"}])
        self.assertEqual(glm.call_args.args[1], "glm-test")


if __name__ == "__main__":
    unittest.main()
