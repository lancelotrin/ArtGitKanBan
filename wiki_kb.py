"""本地飞书知识库检索（参考 FeishuKonwledgeBot/src/text-search.mjs、wiki-kb.mjs）。

wiki/ 下每个 json 是一篇缓存好的飞书文档：objToken、title、path、url、editTime、text。
这里只读缓存做 BM25 检索，不调模型、不联网；把最相关的几篇文档片段（带路径和链接）拼成资料给 Codex。
目录里文件变了（数量或修改时间）下次检索时自动重建索引。

同步：.env 里配了 WIKI_SOURCE_DIR（例如 FeishuKonwledgeBot 的 data/wiki）时，后台启动时同步一次，
之后每 WIKI_SYNC_MINUTES 分钟（默认 30）同步一次：新增、变化的文件复制过来，源里已经没有的删掉。
复制先写临时文件、确认是完整的 JSON 再替换，源目录里写到一半的文件这一轮跳过，下一轮再拷。
"""
import json
import math
import os
import re
import shutil
import threading
import time
from collections import Counter, defaultdict

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
WIKI_DIR = os.path.join(BOT_DIR, "wiki")
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


def dir_stamp(root: str) -> tuple:
    try:
        entries = [entry for entry in os.scandir(root) if entry.name.endswith(".json")]
    except OSError:
        return (root, 0, 0)
    return (root, len(entries), max((entry.stat().st_mtime for entry in entries), default=0))


def load(root: str = "") -> dict:
    """读缓存、建索引；目录没变就复用。"""
    root = root or WIKI_DIR
    stamp = dir_stamp(root)
    with _lock:
        if _state["stamp"] == stamp and _state["index"] is not None:
            return _state
        docs, chunks = {}, []
        for name in sorted(os.listdir(root)) if os.path.isdir(root) else []:
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(root, name), encoding="utf-8") as file:
                    doc = json.load(file)
            except (OSError, ValueError):
                continue  # 坏文件跳过
            token = doc.get("objToken") if isinstance(doc, dict) else None
            if not token:
                continue
            doc["path"] = [str(part) for part in (doc.get("path") or [doc.get("title") or token])]
            docs[token] = doc
            heading = " / ".join(doc["path"])
            for part in chunk_text(doc.get("text") or "") or [""]:
                # 标题路径写两遍，提高标题命中的权重（和参考项目一致）
                chunks.append((token, f"{heading}\n{heading}\n{part}", part))
        _state.update(stamp=stamp, docs=docs, chunks=chunks, index=Bm25([text for _, text, _ in chunks]))
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
    from auth import env_value, read_env_file
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
            if mine.st_size == stat.st_size and int(mine.st_mtime) == int(stat.st_mtime):
                continue
        except OSError:
            pass
        temp = target + ".tmp"
        try:
            shutil.copy2(os.path.join(source, name), temp)
            with open(temp, encoding="utf-8") as file:
                json.load(file)  # 源文件正在写时可能不完整，这一轮先不换
            os.replace(temp, target)
            copied += 1
        except (OSError, ValueError):
            skipped += 1
            try:
                os.remove(temp)
            except OSError:
                pass
    if wanted:  # 源目录是空的多半是那边出了问题，不跟着清空
        for entry in os.scandir(dest):
            if entry.name.endswith(".json") and entry.name not in wanted:
                try:
                    os.remove(entry.path)
                    removed += 1
                except OSError:
                    pass
    return {"copied": copied, "removed": removed, "skipped": skipped, "total": len(wanted)}


_sync_started = threading.Event()


def start_sync_loop(log=print) -> bool:
    """配了 WIKI_SOURCE_DIR 就起后台线程定期同步；每轮现读 .env，改了不用重启。"""
    if _sync_started.is_set() or not setting("WIKI_SOURCE_DIR"):
        return False
    _sync_started.set()

    def loop() -> None:
        while True:
            source = setting("WIKI_SOURCE_DIR")
            if source:
                try:
                    result = sync(source)
                    if result["copied"] or result["removed"] or result["skipped"]:
                        log(f"[wiki] 同步 {source}：复制 {result['copied']}，删除 {result['removed']}，"
                            f"跳过 {result['skipped']}，共 {result['total']} 篇")
                except OSError as error:
                    log(f"[wiki] 同步失败：{error}")
            try:
                minutes = max(1, int(setting("WIKI_SYNC_MINUTES", "30")))
            except ValueError:
                minutes = 30
            time.sleep(minutes * 60)

    threading.Thread(target=loop, name="wiki-sync", daemon=True).start()
    return True


if __name__ == "__main__":
    import sys
    import time
    started = time.time()
    found = search(" ".join(sys.argv[1:]) or "圣遗物 命名")
    print(f"{len(_state['docs'])} 篇 / {len(_state['chunks'])} 段，用时 {time.time() - started:.1f}s")
    for doc in found["docs"]:
        print("-", " / ".join(doc["path"]), doc.get("url"))
