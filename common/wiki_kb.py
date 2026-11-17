"""本地飞书知识库检索（参考 FeishuKonwledgeBot/src/text-search.mjs、wiki-kb.mjs）。

后台启动和每隔 WIKI_SYNC_MINUTES 分钟同步 wiki/，将变化的文档写入 SQLite，再更新 BM25 检索索引。
"""
import argparse
import asyncio
import json
import math
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
from collections import Counter, defaultdict
from contextlib import closing, suppress
from common.paths import DATA_DIR, ROOT_DIR

WIKI_DIR = os.path.join(ROOT_DIR, "wiki")
LEGACY_WIKI_DIR = os.path.join(DATA_DIR, "wiki")
DATABASE_NAME = "index.sqlite"
CONTEXT_CHARS = 9000

STOP = {"这个", "那个", "什么", "怎么", "一下", "可以", "问题", "请问", "如何", "是否", "我们", "你们",
        "他们", "就是", "还是", "没有", "一个", "现在", "时候", "因为", "所以", "如果",
        "the", "and", "for", "with", "this", "that"}
WORD = re.compile(r"[a-z_][a-z0-9_]{1,63}|[0-9]{3,}")
CAMEL = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])")
CJK = re.compile(r"[㐀-鿿]+")


def tokenize(text: str) -> list:
    text = str(text or "")
    tokens = []
    for word in WORD.findall(text.lower()):
        if word not in STOP:
            tokens.append(word)
        for part in word.split("_"):  # snake_case 的每段也能匹配
            if part != word and len(part) > 2:
                tokens.append(part)
    for word in CAMEL.findall(text):  # CamelCase 拆开：BattleManager -> battle、manager
        part = word.lower()
        if len(part) > 2 and part not in STOP:
            tokens.append(part)
    for run in CJK.findall(text):  # 中文按相邻两字切
        for i in range(len(run) - 1):
            pair = run[i:i + 2]
            if pair not in STOP:
                tokens.append(pair)
    return tokens


def chunk_text(text: str, size: int = 900, overlap: int = 150) -> list:
    paragraphs = [line.strip() for line in str(text or "").split("\n") if line.strip()]
    chunks, current = [], ""
    for paragraph in paragraphs:
        if current and len(current) + len(paragraph) + 1 > size:
            chunks.append(current)
            current = current[-overlap:] if overlap else ""
        if len(paragraph) > size:
            for i in range(0, len(paragraph), size - overlap):
                piece = paragraph[i:i + size]
                if current and len(current) + len(piece) + 1 > size:
                    chunks.append(current)
                    current = ""
                current += ("\n" if current else "") + piece
        else:
            current += ("\n" if current else "") + paragraph
    if current.strip():
        chunks.append(current)
    return chunks


class Bm25:
    def __init__(self, texts: list, k1: float = 1.2, b: float = 0.75) -> None:
        self.k1, self.b, self.size = k1, b, len(texts)
        self.postings = defaultdict(list)
        self.lengths = []
        for index, text in enumerate(texts):
            tokens = tokenize(text)
            self.lengths.append(len(tokens) or 1)
            for token, count in Counter(tokens).items():
                self.postings[token].append((index, count))
        self.average = sum(self.lengths) / (len(self.lengths) or 1)

    def search(self, query: str, limit: int = 8) -> list:
        scores = defaultdict(float)
        for token in set(tokenize(query)):
            postings = self.postings.get(token)
            if not postings:
                continue
            idf = math.log(1 + (self.size - len(postings) + 0.5) / (len(postings) + 0.5))
            weight = 1.5 if re.search(r"[a-z0-9]", token) else 1.0  # 英文标识符比中文两字更有区分度
            for index, count in postings:
                tf = count * (self.k1 + 1) / (count + self.k1 * (1 - self.b + self.b * self.lengths[index] / self.average))
                scores[index] += idf * tf * weight
        return sorted(scores.items(), key=lambda item: -item[1])[:limit]


_lock = threading.Lock()
_state = {"stamp": None, "docs": {}, "chunks": [], "index": None}


def file_stamp(stat: os.stat_result) -> tuple:
    return (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def json_files(root: str) -> dict:
    try:
        with os.scandir(root) as entries:
            return {entry.name: file_stamp(entry.stat()) for entry in entries
                    if entry.name.endswith(".json") and entry.is_file()}
    except FileNotFoundError:
        if not os.path.isdir(root):
            return {}
        raise  # 扫描时文件被移除，避免用不完整的清单更新数据库


def read_doc(path: str) -> dict:
    with open(path, encoding="utf-8") as file:
        doc = json.load(file)
    if not isinstance(doc, dict) or not isinstance(doc.get("objToken"), str) or not doc["objToken"]:
        raise ValueError("知识库文档缺少 objToken")
    return doc


def update_database(root: str) -> dict:
    """在一次 SQLite 事务中处理新增、修改和删除。坏文件保留上次入库的版本。"""
    os.makedirs(root, exist_ok=True)
    with closing(sqlite3.connect(os.path.join(root, DATABASE_NAME))) as db, db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("""CREATE TABLE IF NOT EXISTS documents (
            filename TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
            ctime_ns INTEGER NOT NULL, document TEXT NOT NULL
        )""")
        files = json_files(root)
        known = {name: (size, mtime, ctime) for name, size, mtime, ctime in
                 db.execute("SELECT filename, size, mtime_ns, ctime_ns FROM documents")}
        updated = skipped = 0
        for name, stamp in files.items():
            if known.get(name) == stamp:
                continue
            path = os.path.join(root, name)
            try:
                doc = read_doc(path)
                if file_stamp(os.stat(path)) != stamp:
                    skipped += 1
                    continue
            except (OSError, ValueError):
                skipped += 1
                continue
            db.execute("INSERT OR REPLACE INTO documents VALUES (?, ?, ?, ?, ?)",
                       (name, *stamp, json.dumps(doc, ensure_ascii=False)))
            updated += 1
        removed = known.keys() - files.keys()
        db.executemany("DELETE FROM documents WHERE filename = ?", ((name,) for name in removed))
        total = db.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    return {"updated": updated, "removed": len(removed), "skipped": skipped, "total": total}


def load(root: str = "") -> dict:
    """从 SQLite 建检索索引。首次入库前或独立文档目录可直接读 JSON。"""
    global _state
    root = root or WIKI_DIR
    database = os.path.join(root, DATABASE_NAME)
    with _lock:
        try:
            stamp = (root, "sqlite", file_stamp(os.stat(database)))
        except FileNotFoundError:
            files = json_files(root)
            stamp = (root, "json", files)
        if _state["stamp"] == stamp and _state["index"] is not None:
            return _state
        if stamp[1] == "sqlite":
            with closing(sqlite3.connect(database)) as db:
                table = db.execute("SELECT 1 FROM sqlite_master WHERE name = 'documents'").fetchone()
                if table:
                    records = [json.loads(row[0]) for row in
                               db.execute("SELECT document FROM documents ORDER BY filename")]
                else:
                    files = json_files(root)
                    stamp = (root, "json", files)
        if stamp[1] == "json":
            records = []
            for name in sorted(files):
                try:
                    records.append(read_doc(os.path.join(root, name)))
                except (OSError, ValueError):
                    continue
        docs, chunks = {}, []
        for doc in records:
            token = doc["objToken"]
            path = doc.get("path")
            doc["path"] = [str(part) for part in path] if isinstance(path, list) and path else [str(doc.get("title") or token)]
            docs[token] = doc
            heading = " / ".join(doc["path"])
            for part in chunk_text(doc.get("text") or "") or [""]:
                # 标题路径写两遍，提高标题命中的权重（和参考项目一致）
                chunks.append((token, f"{heading}\n{heading}\n{part}", part))
        _state = {"stamp": stamp, "docs": docs, "chunks": chunks,
                  "index": Bm25([text for _, text, _ in chunks])}
        return _state


def search(query: str, limit: int = 6, context_chars: int = CONTEXT_CHARS, root: str = "") -> dict:
    """返回 {"context": 拼好的资料, "docs": [命中的文档]}。每篇最多取两个片段。"""
    state = load(root)
    if not query.strip() or not state["chunks"]:
        return {"context": "", "docs": []}
    by_doc = {}
    for index, score in state["index"].search(query, 60):
        token, _, body = state["chunks"][index]
        entry = by_doc.setdefault(token, {"doc": state["docs"][token], "score": 0.0, "chunks": []})
        entry["score"] += score
        if len(entry["chunks"]) < 2 and body:
            entry["chunks"].append(body)
    ranked = sorted(by_doc.values(), key=lambda entry: -entry["score"])[:limit]
    sections, kept, used = [], [], 0
    for entry in ranked:
        doc = entry["doc"]
        body = "\n…\n".join(entry["chunks"]) or "（该文档无可读取正文，仅标题匹配）"
        section = f"《{' / '.join(doc['path'])}》 {doc.get('url') or ''}\n{body}"
        if used + len(section) > context_chars:
            break
        sections.append(section)
        kept.append(doc)
        used += len(section)
    return {"context": "\n\n---\n\n".join(sections), "docs": kept}


def setting(name: str, default: str = "") -> str:
    from common.auth import env_value, read_env_file
    return env_value(name, read_env_file()) or default


def sync(source: str, dest: str = "") -> dict:
    """把 source 下的 *.json 同步到 dest。返回 {"copied", "removed", "skipped", "total"}。"""
    dest = dest or WIKI_DIR
    if not os.path.isdir(source):
        raise FileNotFoundError(f"知识库源目录不存在：{source}")
    if os.path.realpath(source) == os.path.realpath(dest):
        return {"copied": 0, "removed": 0, "skipped": 0, "total": 0}
    os.makedirs(dest, exist_ok=True)
    wanted = {entry.name: entry.stat() for entry in os.scandir(source)
              if entry.is_file() and entry.name.endswith(".json")}
    copied = skipped = removed = 0
    for name, stat in wanted.items():
        target = os.path.join(dest, name)
        try:
            mine = os.stat(target)
            if mine.st_size == stat.st_size and mine.st_mtime_ns == stat.st_mtime_ns:
                continue
        except OSError:
            pass
        temp = None
        try:
            fd, temp = tempfile.mkstemp(dir=dest, suffix=".tmp")
            os.close(fd)
            shutil.copy2(os.path.join(source, name), temp)
            read_doc(temp)
            if file_stamp(os.stat(os.path.join(source, name))) != file_stamp(stat):
                skipped += 1
                continue
            os.replace(temp, target)
            copied += 1
        except (OSError, ValueError):
            skipped += 1
        finally:
            if temp:
                with suppress(FileNotFoundError):
                    os.unlink(temp)
    if wanted:  # 源目录是空的多半是那边出了问题，不跟着清空
        for entry in os.scandir(dest):
            if entry.name.endswith(".json") and entry.name not in wanted:
                try:
                    os.remove(entry.path)
                    removed += 1
                except OSError:
                    pass
    return {"copied": copied, "removed": removed, "skipped": skipped, "total": len(wanted)}


def refresh(log=print) -> dict:
    """同步文件并扫描入库。源目录暂时不可用时仍扫描本地缓存。"""
    if not os.path.exists(WIKI_DIR) and os.path.isdir(LEGACY_WIKI_DIR):
        try:
            os.rename(LEGACY_WIKI_DIR, WIKI_DIR)
        except FileNotFoundError:
            if not os.path.isdir(WIKI_DIR):
                raise
    source = setting("WIKI_SOURCE_DIR")
    sync_error = None
    if source:
        try:
            result = sync(source)
            log(f"[wiki] 同步到 wiki/：复制 {result['copied']}，删除 {result['removed']}，"
                f"跳过 {result['skipped']}，源目录共 {result['total']} 篇")
        except OSError as error:
            sync_error = error
            log(f"[wiki] 文件同步失败，继续扫描本地缓存：{error}")
    result = update_database(WIKI_DIR)
    load()
    log(f"[wiki] SQLite 扫描完成：更新 {result['updated']}，删除 {result['removed']}，"
        f"跳过 {result['skipped']}，数据库共 {result['total']} 篇")
    if sync_error:
        raise sync_error
    return result


async def sync_loop(log=print) -> None:
    """由后台生命周期管理，每轮重新读取同步配置。"""
    while True:
        try:
            await asyncio.to_thread(refresh, log)
        except (OSError, sqlite3.Error) as error:
            log(f"[wiki] 本轮更新失败：{error}")
        try:
            minutes = max(1, int(setting("WIKI_SYNC_MINUTES", "30")))
        except ValueError:
            minutes = 30
        await asyncio.sleep(minutes * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="同步或检索本地知识库")
    parser.add_argument("--sync", action="store_true", help="立即同步并扫描入库")
    parser.add_argument("query", nargs="*")
    args = parser.parse_args()
    if args.sync:
        refresh()
    if args.query or not args.sync:
        started = time.monotonic()
        found = search(" ".join(args.query) or "圣遗物 命名")
        print(f"{len(_state['docs'])} 篇 / {len(_state['chunks'])} 段，用时 {time.monotonic() - started:.1f}s")
        for doc in found["docs"]:
            print("-", " / ".join(doc["path"]), doc.get("url"))
