import concurrent.futures
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from server import ReviewServer, database, initialize_database, make_handler


class ReviewTests(unittest.TestCase):
    def setUp(self):
        test_root = Path(__file__).parent / 'data' / 'tests'
        test_root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=test_root)
        self.db = Path(self.temp.name) / "review.sqlite3"
        initialize_database(self.db)
        self.snapshot = {"id": "test-snapshot", "files": [{"id": "one", "path": "example.cs", "category": "code"}]}
        self.server = ReviewServer(("127.0.0.1", 0), make_handler(self.snapshot, self.db))
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.temp.cleanup()

    def vote(self, reviewer="Alice", choice="ours", **extra):
        body = {"snapshot": "test-snapshot", "file_id": "one", "reviewer": reviewer, "choice": choice, **extra}
        request = Request(self.url + "/api/vote", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        with urlopen(request) as response:
            return json.load(response)

    def state(self):
        with urlopen(self.url + "/api/state") as response:
            return json.load(response)

    def test_latest_person_overwrites_shared_choice_and_clear(self):
        self.vote()
        self.vote("Bob", "both")
        self.vote("Alice", "theirs", comment="保留修复")
        votes = self.state()["votes"]
        self.assertEqual(len(votes), 1)
        self.assertEqual(votes[0]["reviewer"], "Alice")
        self.assertEqual(votes[0]["choice"], "theirs")
        self.assertEqual(votes[0]["comment"], "保留修复")
        self.vote("Bob", "clear")
        self.assertEqual(self.state()["votes"], [])

    def test_concurrent_votes_and_durable_storage(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda i: self.vote(f"user{i}", "both"), range(16)))
        self.assertEqual(len(self.state()["votes"]), 1)
        self.vote("last-person", "theirs")
        self.assertEqual(self.state()["votes"][0]["reviewer"], "last-person")
        conn = database(self.db)
        self.assertEqual(conn.execute("SELECT count(*) FROM decisions").fetchone()[0], 1)
        conn.close()

    def test_migrate_keeps_only_latest_person(self):
        legacy_path = Path(self.temp.name) / 'legacy.sqlite3'
        with sqlite3.connect(legacy_path) as conn:
            conn.execute('CREATE TABLE votes(snapshot,file_id,reviewer,choice,comment,updated_at)')
            conn.executemany('INSERT INTO votes VALUES (?,?,?,?,?,?)', [
                ('s','f','Alice','ours','','2026-11-04T01:00:00'),
                ('s','f','Bob','both','new','2026-11-04T02:00:00')])
        conn.close()
        initialize_database(legacy_path)
        conn = database(legacy_path)
        try:
            rows = conn.execute('SELECT * FROM decisions').fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['reviewer'], 'Bob')
            self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='votes' OR name='history'").fetchone())
        finally:
            conn.close()

    def test_validation_and_no_arbitrary_files(self):
        for body, expected in [({"snapshot": "old"}, 409), ({"file_id": "missing"}, 400), ({"choice": "invalid"}, 400), ({"reviewer": ""}, 400)]:
            with self.assertRaises(HTTPError) as error:
                self.vote(**body)
            self.assertEqual(error.exception.code, expected)
            error.exception.close()
        with self.assertRaises(HTTPError) as error:
            urlopen(self.url + "/../../.git/config")
        self.assertEqual(error.exception.code, 404)
        error.exception.close()

    def test_export_and_cross_origin(self):
        (self.db.parent / 'review.md').write_text('# Review\nTest Author\n', encoding='utf-8')
        with urlopen(self.url + '/api/review.md') as response:
            self.assertIn('Test Author', response.read().decode('utf-8'))
        self.vote("=Alice", "ours", comment="=formula")
        with urlopen(self.url + "/api/export.csv") as response:
            csv = response.read().decode("utf-8-sig")
        self.assertIn("'=Alice", csv)
        self.assertIn("'=formula", csv)
        request = Request(self.url + "/api/vote", data=b"{}", headers={"Content-Type":"application/json", "Origin":"http://foreign.invalid"})
        with self.assertRaises(HTTPError) as error:
            urlopen(request)
        self.assertEqual(error.exception.code, 403)
        error.exception.close()


if __name__ == "__main__":
    unittest.main()
