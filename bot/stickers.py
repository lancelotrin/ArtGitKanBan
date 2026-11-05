"""表情库：D:\\FeishuBot\\stickers 下的图片，文件名就是关键词。"""
import os
import random
import re
import shutil
from pathlib import Path
from common.paths import DATA_DIR

STICKER_DIR = os.path.join(DATA_DIR, "stickers")
STICKER_DIR_REL = "stickers"
STICKER_SUFFIXES = (".gif", ".png", ".jpg", ".jpeg", ".webp")
STICKER_MAX_BYTES = 10 * 1024 * 1024
STICKER_LIST_LIMIT = 60

_NAME = r"([\u4e00-\u9fffA-Za-z0-9_\-]{1,20})"
COLLECT_PATTERN = re.compile(
    r"^收藏(这个|这张)?(表情包?|动图|图)?\s*(叫|为|成|名字叫)\s*" + _NAME + r"\s*$"
    r"|^收藏(这个|这张)?(表情包?|动图)\s*" + _NAME + r"\s*$")
DELETE_PATTERN = re.compile(r"^(删除|删掉|删)(表情包?|动图)\s*" + _NAME + r"\s*$")
SEND_PATTERN = re.compile(r"^(发|来)(个|张|一个|一张|点)?\s*" + _NAME + r"?\s*(的)?(表情包?|动图)\s*$")
SEND_BARE_PATTERN = re.compile(r"^(发|来)(个|张|一个|一张)\s*" + _NAME + r"\s*$")
LIST_PATTERN = re.compile(r"^(表情包?列表|有(哪些|什么|啥)表情包?|表情包?(有哪些|有啥|有什么))[？?]?$")


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).strip("。！!？? ")


def safe_name(name: str) -> str:
    name = (name or "").strip()
    return name if re.fullmatch(_NAME, name) else ""


def parse_collect(text: str) -> str:
    match = COLLECT_PATTERN.match(clean(text))
    return (match.group(4) or match.group(7)) if match else ""


def parse_delete(text: str) -> str:
    match = DELETE_PATTERN.match(clean(text))
    return match.group(3) if match else ""


def parse_send(text: str):
    """返回关键词；空串表示随便来一张；None 表示不是发表情。"""
    raw = clean(text)
    match = SEND_PATTERN.match(raw)
    if match:
        return match.group(3) or ""
    match = SEND_BARE_PATTERN.match(raw)
    if match and find(match.group(3)):
        return match.group(3)
    return None


def is_list_request(text: str) -> bool:
    return bool(LIST_PATTERN.match(clean(text)))


def all_files() -> list:
    if not os.path.isdir(STICKER_DIR):
        return []
    return sorted((path for path in Path(STICKER_DIR).iterdir()
                   if path.is_file() and path.suffix.lower() in STICKER_SUFFIXES), key=lambda path: path.stem)


def list_names() -> list:
    return [path.stem for path in all_files()]


def find(name: str):
    files = all_files()
    if not files:
        return None
    if not name:
        return random.choice(files)
    exact = [path for path in files if path.stem.lower() == name.lower()]
    if exact:
        return exact[0]
    near = [path for path in files if name.lower() in path.stem.lower() or path.stem.lower() in name.lower()]
    return random.choice(near) if near else None


def image_suffix(path: str) -> str:
    head = Path(path).read_bytes()[:12]
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if head[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    return ""


def save(source: str, name: str) -> tuple:
    """返回 (相对路径, 错误)。同名覆盖。"""
    name = safe_name(name)
    if not name:
        return "", "名字只能用中文、字母、数字、下划线、短横线，最多 20 个字。"
    if os.path.getsize(source) > STICKER_MAX_BYTES:
        return "", "图片超过 10MB，不收。"
    suffix = image_suffix(source)
    if not suffix:
        return "", "这不是 GIF/PNG/JPG/WEBP 图片，不收。"
    os.makedirs(STICKER_DIR, exist_ok=True)
    for old in all_files():
        if old.stem.lower() == name.lower():
            old.unlink()
    target = os.path.join(STICKER_DIR, name + suffix)
    shutil.copyfile(source, target)
    return f"{STICKER_DIR_REL}/{name}{suffix}", ""


def delete(name: str) -> bool:
    removed = False
    for old in all_files():
        if old.stem.lower() == (name or "").lower():
            old.unlink()
            removed = True
    return removed


def relative(path) -> str:
    return f"{STICKER_DIR_REL}/{Path(path).name}"


def list_text() -> str:
    names = list_names()
    if not names:
        return "表情库还是空的。朱辉回复一张图说「收藏 叫xx」就能存进来。"
    shown = "、".join(names[:STICKER_LIST_LIMIT])
    more = f" 等 {len(names)} 个" if len(names) > STICKER_LIST_LIMIT else f"，共 {len(names)} 个"
    return f"现有表情：{shown}{more}。说「发个xx表情」就发。"
