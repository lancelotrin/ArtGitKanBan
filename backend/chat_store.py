"""AI 聊天记录：存在 chats.sqlite3，按登录的人（飞书 open_id）隔开。

所有读写都要带 owner，查不到就当不存在——别人的对话连有没有都看不出来，管理员也一样。
"""
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Iterator, List, Optional

from backend.deps import ROOT_DIR

DB_PATH = os.environ.get("CHAT_DB_PATH") or os.path.join(ROOT_DIR, "chats.sqlite3")
TITLE_MAX = 40
_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY,
  owner TEXT NOT NULL,
  title TEXT NOT NULL,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS conversations_owner ON conversations(owner, updated_at DESC);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  model TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'ok',
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_conversation ON messages(conversation_id, id);
"""


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    with _lock:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def title_of(text: str) -> str:
    line = " ".join(text.split())
    return line[:TITLE_MAX] + ("…" if len(line) > TITLE_MAX else "") or "新对话"


def list_conversations(owner: str) -> List[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT id, title, created_at, updated_at FROM conversations WHERE owner=? "
                            "ORDER BY updated_at DESC LIMIT 200", (owner,)).fetchall()
    return [dict(row) for row in rows]


def get_conversation(owner: str, conversation_id: str) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute("SELECT id, title, created_at, updated_at FROM conversations WHERE id=? AND owner=?",
                           (conversation_id, owner)).fetchone()
        if not row:
            return None
        messages = conn.execute("SELECT id, role, content, model, status, created_at FROM messages "
                                "WHERE conversation_id=? ORDER BY id", (conversation_id,)).fetchall()
    return dict(row, messages=[dict(message) for message in messages])


def create_conversation(owner: str, first_message: str) -> str:
    conversation_id = uuid.uuid4().hex
    now = time.time()
    with connect() as conn:
        conn.execute("INSERT INTO conversations (id, owner, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                     (conversation_id, owner, title_of(first_message), now, now))
    return conversation_id


def add_message(owner: str, conversation_id: str, role: str, content: str, model: str = "", status: str = "ok") -> int:
    now = time.time()
    with connect() as conn:
        owned = conn.execute("SELECT 1 FROM conversations WHERE id=? AND owner=?", (conversation_id, owner)).fetchone()
        if not owned:
            raise KeyError(conversation_id)
        cursor = conn.execute("INSERT INTO messages (conversation_id, role, content, model, status, created_at) "
                              "VALUES (?, ?, ?, ?, ?, ?)", (conversation_id, role, content, model, status, now))
        conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now, conversation_id))
        return int(cursor.lastrowid)


def rename_conversation(owner: str, conversation_id: str, title: str) -> bool:
    with connect() as conn:
        cursor = conn.execute("UPDATE conversations SET title=? WHERE id=? AND owner=?",
                              (title_of(title), conversation_id, owner))
        return cursor.rowcount > 0


def delete_conversation(owner: str, conversation_id: str) -> bool:
    with connect() as conn:
        cursor = conn.execute("DELETE FROM conversations WHERE id=? AND owner=?", (conversation_id, owner))
        return cursor.rowcount > 0
