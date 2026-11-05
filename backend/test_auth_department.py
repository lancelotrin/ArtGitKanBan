"""登录时查部门：查到就存下来，没权限 / 报错不影响登录，这次没查到保留上次的。

运行：.venv/bin/python -m unittest backend.test_auth_department
"""
import json
import os
import tempfile
import unittest
from unittest import mock

import auth


def fake_get(responses: dict):
    def get(url, headers):
        for key, body in responses.items():
            if key in url:
                return body
        raise AssertionError(url)
    return get


class DepartmentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = auth.USERS_PATH
        auth.USERS_PATH = os.path.join(self.tmp.name, "users.json")
        auth._department_names.clear()
        self.token = mock.patch.object(auth, "tenant_token", return_value="t")
        self.token.start()

    def tearDown(self) -> None:
        self.token.stop()
        auth.USERS_PATH = self.saved
        self.tmp.cleanup()

    def test_found(self) -> None:
        responses = {"/users/": {"code": 0, "data": {"user": {"department_ids": ["od-1", "od-2"]}}},
                     "/departments/od-1": {"code": 0, "data": {"department": {"name": "美术组"}}},
                     "/departments/od-2": {"code": 0, "data": {"department": {"name": "UI 组"}}}}
        with mock.patch.object(auth, "_get_json", side_effect=fake_get(responses)):
            self.assertEqual(auth.lookup_department("ou_1"), "美术组、UI 组")

    def test_no_permission_or_error_is_empty(self) -> None:
        for body in ({"code": 0, "data": {"user": {"open_id": "ou_1"}}}, {"code": 99991672, "msg": "no permission"}):
            with mock.patch.object(auth, "_get_json", return_value=body), mock.patch("builtins.print") as printed:
                self.assertEqual(auth.lookup_department("ou_1"), "")
            self.assertIn("contact:user.department:readonly", printed.call_args.args[0])

    def test_keeps_previous_department(self) -> None:
        auth.remember_user({"open_id": "ou_1", "name": "小林", "department": "美术组"})
        user = auth.remember_user({"open_id": "ou_1", "name": "小林", "department": ""})
        self.assertEqual(user["department"], "美术组")
        self.assertEqual(auth.public_user(user)["department"], "美术组")
        with open(auth.USERS_PATH, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["ou_1"]["department"], "美术组")


if __name__ == "__main__":
    unittest.main()
