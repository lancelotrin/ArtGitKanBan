"""settings.py：工程目录、分支、模型都从 .env 读；后台不再能改。

运行：.venv/bin/python -m unittest backend.test_settings
"""
import os
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

import auth
import console
import settings
from backend.app import app

KEYS = ("PROJECT_ROOT", "FEISHU_BOT_PROJECT_ROOT", "GIT_BRANCHES", "LLM_MODEL", "LLM_VISION_MODEL")


class SettingsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.env_path = os.path.join(self.tmp.name, ".env")
        self.saved = (auth.ENV_PATH, auth.USERS_PATH, auth.SESSIONS_PATH)
        auth.ENV_PATH = self.env_path
        auth.USERS_PATH = os.path.join(self.tmp.name, "users.json")
        auth.SESSIONS_PATH = os.path.join(self.tmp.name, "sessions.json")
        self.environ = mock.patch.dict(os.environ, {}, clear=False)
        self.environ.start()
        for key in KEYS:
            os.environ.pop(key, None)

    def tearDown(self) -> None:
        self.environ.stop()
        auth.ENV_PATH, auth.USERS_PATH, auth.SESSIONS_PATH = self.saved
        self.tmp.cleanup()

    def write_env(self, text: str) -> None:
        with open(self.env_path, "w", encoding="utf-8") as handle:
            handle.write(text)

    def test_defaults_without_env(self) -> None:
        self.assertEqual(settings.project_root(), settings.DEFAULT_PROJECT_ROOT)
        self.assertEqual(settings.git_branches(), ["main", "CBT2_bugfix"])
        self.assertEqual(settings.models(), {"chat": "glm-4.5-flash", "vision": "glm-4.6v-flash"})

    def test_reads_env_file(self) -> None:
        self.write_env("PROJECT_ROOT=/work/Client\nGIT_BRANCHES=CBT2_bugfix, shader，bad name!\nLLM_MODEL='glm-4-flash'\n")
        self.assertEqual(settings.project_root(), "/work/Client")
        self.assertEqual(settings.git_branches(), ["main", "CBT2_bugfix", "shader", "bad"])
        self.assertEqual(settings.models()["chat"], "glm-4-flash")
        self.assertEqual(console.load_config()["project_root"], "/work/Client")

    def test_system_env_wins_and_old_name_still_works(self) -> None:
        self.write_env("FEISHU_BOT_PROJECT_ROOT=/old/Client\n")
        self.assertEqual(settings.project_root(), "/old/Client")
        os.environ["PROJECT_ROOT"] = "/from/system"
        self.assertEqual(settings.project_root(), "/from/system")

    def test_admin_can_no_longer_change_config_or_models(self) -> None:
        with open(auth.USERS_PATH, "w", encoding="utf-8") as handle:
            handle.write('{"ou_admin": {"open_id": "ou_admin", "name": "管理员", "role": "admin"}}')
        client = TestClient(app, base_url="http://onepiece.local")
        client.cookies.set(auth.COOKIE, auth.create_session("ou_admin"))
        for name in ("config", "models"):
            self.assertIn(client.post("/api/v1/admin/" + name, json={}).status_code, (404, 405))  # 没有写接口了
        status, payload = console.admin_post("/api/config", {"project_root": "/x"}, {"open_id": "ou_admin"})
        self.assertEqual(status, 400)
        self.assertIn(".env", payload["error"])
        self.assertFalse(os.path.exists(os.path.join(console.BOT_DIR, "bot_config.json")))


if __name__ == "__main__":
    unittest.main()
