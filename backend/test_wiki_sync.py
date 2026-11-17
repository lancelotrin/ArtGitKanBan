"""知识库同步测试：新增 / 变化的复制过来，源里没有的删掉，写到一半的跳过，源目录为空时不清空。

运行：.venv/bin/python -m unittest backend.test_wiki_sync
"""
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from unittest import mock

from common import wiki_kb


def write(root: str, name: str, text: str) -> None:
    with open(os.path.join(root, name), "w", encoding="utf-8") as handle:
        handle.write(text)


def doc(token: str, text: str) -> str:
    return json.dumps({"objToken": token, "title": token, "path": [token], "url": "", "text": text})


class WikiSyncTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.source = os.path.join(self.tmp.name, "source")
        self.dest = os.path.join(self.tmp.name, "dest")
        os.makedirs(self.source)
        os.makedirs(self.dest)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_sync(self) -> None:
        write(self.source, "A.json", doc("A", "一"))
        write(self.source, "B.json", doc("B", "二"))
        write(self.dest, "old.json", doc("old", "旧"))
        self.assertEqual(wiki_kb.sync(self.source, self.dest), {"copied": 2, "removed": 1, "skipped": 0, "total": 2})
        self.assertEqual(sorted(os.listdir(self.dest)), ["A.json", "B.json"])

        # 没变就不再复制
        self.assertEqual(wiki_kb.sync(self.source, self.dest)["copied"], 0)

        # 变了的复制，写到一半的跳过且不留临时文件
        write(self.source, "A.json", doc("A", "一改过了"))
        os.utime(os.path.join(self.source, "A.json"), (1, 2_000_000_000))
        write(self.source, "C.json", '{"objToken": "C", "te')
        result = wiki_kb.sync(self.source, self.dest)
        self.assertEqual((result["copied"], result["skipped"]), (1, 1))
        with open(os.path.join(self.dest, "A.json"), encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["text"], "一改过了")
        self.assertEqual(sorted(os.listdir(self.dest)), ["A.json", "B.json"])

    def test_empty_source_does_not_wipe(self) -> None:
        write(self.dest, "A.json", doc("A", "一"))
        self.assertEqual(wiki_kb.sync(self.source, self.dest)["removed"], 0)
        self.assertEqual(os.listdir(self.dest), ["A.json"])

    def test_missing_source(self) -> None:
        with self.assertRaises(FileNotFoundError):
            wiki_kb.sync(os.path.join(self.tmp.name, "nope"), self.dest)

    def test_refresh_updates_search_and_preserves_last_valid_document(self) -> None:
        write(self.source, "A.json", doc("A", "legacyword"))
        write(self.source, "B.json", doc("B", "removedword"))
        legacy = os.path.join(self.tmp.name, "legacy")
        os.rename(self.dest, legacy)
        db_path = os.path.join(self.dest, wiki_kb.DATABASE_NAME)
        with mock.patch.object(wiki_kb, "WIKI_DIR", self.dest), \
                mock.patch.object(wiki_kb, "LEGACY_WIKI_DIR", legacy), \
                mock.patch.object(wiki_kb, "setting", return_value=self.source):
            self.assertEqual(wiki_kb.refresh(log=mock.Mock())["total"], 2)
            self.assertFalse(os.path.exists(legacy))
            self.assertEqual(wiki_kb.refresh(log=mock.Mock())["updated"], 0)
            self.assertIn("legacyword", wiki_kb.search("legacyword")["context"])

            write(self.source, "A.json", '{"objToken":')
            os.unlink(os.path.join(self.source, "B.json"))
            self.assertEqual(wiki_kb.refresh(log=mock.Mock())["removed"], 1)
            self.assertIn("legacyword", wiki_kb.search("legacyword")["context"])
            self.assertEqual(wiki_kb.search("removedword")["docs"], [])

            write(self.source, "A.json", doc("A", "latestword"))
            self.assertEqual(wiki_kb.refresh(log=mock.Mock())["updated"], 1)
            self.assertIn("latestword", wiki_kb.search("latestword")["context"])
            self.assertEqual(wiki_kb.search("legacyword")["docs"], [])
            with closing(sqlite3.connect(db_path)) as db:
                stored = db.execute("SELECT document FROM documents").fetchall()
            self.assertEqual([json.loads(row[0])["text"] for row in stored], ["latestword"])


if __name__ == "__main__":
    unittest.main()
