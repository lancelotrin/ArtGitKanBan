"""提问人自己电脑上的文本修改。大豆不写服务机工程，只生成协议；local_agent.py 落地。"""
import difflib
import os
import re

MAX_TEXT = 60000
PROTOCOL = "DADOU/1"

_LOCAL_EDIT = re.compile(
    r"(我的?(本地|电脑|机器)|本地文件).{0,24}(改|修改|替换|写成|换掉)|"
    r"(改|修改|替换|写成).{0,12}(我的?(本地|电脑|机器)|本地文件)"
)
_LOCAL_QUESTION = re.compile(r"怎么|如何|能不能|可不可以|会不会|是什么")
_LOCAL_PATH = re.compile(
    r"(?<![A-Za-z0-9_./\\])((?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.[A-Za-z0-9]{1,8})"
)
_LOCAL_BARE = re.compile(
    r"(?<![A-Za-z0-9_./\\])([A-Za-z][A-Za-z0-9_.-]*\.(?:cs|shader|hlsl|txt|json|xml|md|py|lua))"
)
_DENY_NAMES = {".env", ".git", "id_rsa", "credentials.json"}


def is_local_edit_request(text: str) -> bool:
    text = text or ""
    if _LOCAL_QUESTION.search(text):
        return False
    return bool(_LOCAL_EDIT.search(text))


def safe_local_path(path: str) -> str:
    raw = (path or "").strip().replace("\\", "/")
    if not raw or ":" in raw or raw.startswith("/"):
        return ""
    parts = [part for part in raw.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        return ""
    for part in parts:
        lowered = part.lower()
        if lowered in _DENY_NAMES or lowered.startswith(".git"):
            return ""
    return "/".join(parts)


def extract_local_edit_path(text: str) -> str:
    match = _LOCAL_PATH.search(text or "") or _LOCAL_BARE.search(text or "")
    if not match:
        return ""
    return safe_local_path(match.group(1))


def encode_message(fields: dict, body: str = "") -> str:
    lines = [PROTOCOL]
    for key in ("op", "id", "path", "ok", "root"):
        value = fields.get(key)
        if value is not None and value != "":
            lines.append(f"{key}: {value}")
    return "\n".join(lines) + "\n---\n" + (body or "")


def decode_message(text: str) -> tuple:
    text = (text or "").lstrip("\ufeff")
    if not text.startswith(PROTOCOL):
        return {}, ""
    head, _, body = text.partition("\n---\n")
    fields = {}
    for line in head.splitlines()[1:]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip()
    return fields, body


def extract_rewritten(text: str) -> str:
    match = re.search(r"<<<FILE>>>\n?(.*?)\n?<<<END>>>", text or "", re.DOTALL)
    if not match:
        return ""
    return match.group(1)


def change_size(old: str, new: str) -> int:
    if old == new:
        return 0
    count = 0
    for line in difflib.ndiff(old.splitlines(), new.splitlines()):
        if line.startswith("+ ") or line.startswith("- "):
            count += 1
    return count


def under_root(root: str, rel: str) -> str:
    rel = safe_local_path(rel)
    if not rel or not root:
        raise ValueError("路径不行")
    root_real = os.path.realpath(root)
    dest = os.path.realpath(os.path.join(root_real, *rel.split("/")))
    if os.path.commonpath([root_real, dest]) != root_real:
        raise ValueError("路径超出工程")
    return dest


def read_under(root: str, rel: str) -> str:
    dest = under_root(root, rel)
    if os.path.islink(dest):
        raise ValueError("不读链接")
    if not os.path.isfile(dest):
        raise ValueError("文件不存在")
    data = open(dest, "rb").read()
    if b"\0" in data[:8000]:
        raise ValueError("不是文本文件")
    if len(data) > MAX_TEXT:
        raise ValueError("文件太大，本地程序不读")
    return data.decode("utf-8")


def write_under(root: str, rel: str, text: str) -> None:
    if len(text.encode("utf-8")) > MAX_TEXT:
        raise ValueError("改完的文件太大")
    dest = under_root(root, rel)
    if os.path.islink(dest):
        raise ValueError("不改链接")
    parent = os.path.dirname(dest)
    if not os.path.isdir(parent):
        raise ValueError("目录不存在")
    temporary = dest + ".dadou-tmp"
    with open(temporary, "w", encoding="utf-8", newline="\n") as file:
        file.write(text)
    os.replace(temporary, dest)
