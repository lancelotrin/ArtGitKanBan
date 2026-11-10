"""工作区里本地改过的 .meta。

- guid 没变（只动了导入设置）：可以直接提交这些 meta。
- guid 变了：说明提交里的旧 guid 和别的资源重复了（Unity 本地给这个 meta 换了新 guid）。
  找最近一次提交了「另一个 .meta 也用这个旧 guid」的提交，和路径无关，
  把那次提交里引用旧 guid 的文件改成本地的新 guid，再和 meta 一起提交。

只 commit，不 push、不拉、不切分支。调用方负责和拉代码互斥。
"""
import os
import re
import subprocess

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
GUID_LINE = re.compile(r"(?m)^guid:\s*([0-9a-fA-F]{32})\s*$")
MAX_REF_BYTES = 64 * 1024 * 1024


def git(root: str, args: list, timeout: int = 60) -> tuple:
    try:
        result = subprocess.run(
            ["git", "-C", root, "-c", "core.quotepath=false"] + args,
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
            creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, str(error)
    if result.returncode != 0:
        return False, ((result.stdout or "") + (result.stderr or "")).strip()
    return True, result.stdout or ""


def guid_of(text: str) -> str:
    match = GUID_LINE.search(text or "")
    return match.group(1).lower() if match else ""


def dirty_paths(root: str) -> dict:
    """已跟踪文件的本地修改：{路径: XY}。"""
    ok, out = git(root, ["status", "--porcelain=v1", "-z", "--untracked-files=no"], 180)
    if not ok:
        raise RuntimeError("读不到工作区状态：" + out[-300:])
    parts = out.split("\0")
    found = {}
    index = 0
    while index < len(parts):
        entry = parts[index]
        index += 1
        if len(entry) < 4:
            continue
        code, path = entry[:2], entry[3:]
        if "R" in code or "C" in code:
            index += 1  # 后面跟着原路径
        found[path] = code
    return found


def read_work(root: str, path: str) -> bytes:
    with open(os.path.join(root, path), "rb") as handle:
        return handle.read()


def dup_guid_commit(root: str, path: str, guid: str) -> dict:
    """最近一次提交了别的 .meta、它的 guid 也是 guid 的提交。和路径无关，只看 guid 重复。"""
    ok, out = git(root, ["log", "-n", "30", "-G", rf"^guid: *{guid}", "--no-renames",
                         "--format=\x1e%H%x1f%h%x1f%an%x1f%ad%x1f%s", "--date=format:%Y-%m-%d %H:%M",
                         "--name-only", "HEAD", "--", "*.meta", f":(exclude){path}"], 120)
    if not ok:
        return {}
    for block in out.split("\x1e"):
        lines = [line for line in block.strip().splitlines() if line.strip()]
        if not lines:
            continue
        full, short, author, date, subject = (lines[0].split("\x1f") + [""] * 5)[:5]
        dups = []
        for meta_path in lines[1:]:
            if meta_path == path or not meta_path.endswith(".meta"):
                continue
            shown, text = git(root, ["show", f"{full}:{meta_path}"])
            if shown and guid_of(text) == guid:
                dups.append(meta_path)
        if dups:
            return {"hash": full, "short": short, "author": author, "date": date, "subject": subject,
                    "dup_paths": dups}
    return {}


def guid_refs(root: str, commit: str, skip: set, old: str, dirty: dict) -> list:
    """那次提交里动过、现在工作区里还引用旧 guid 的文件。skip 是本地 meta 和重复 guid 的 meta 自己。"""
    ok, out = git(root, ["show", "--name-only", "--no-renames", "--format=", "-z", commit])
    if not ok:
        return []
    pattern = re.compile(re.escape(old.encode("ascii")), re.I)
    refs = []
    for path in sorted({item for item in out.split("\0") if item.strip()}):
        if path in skip:
            continue
        absolute = os.path.join(root, path)
        if not os.path.isfile(absolute) or os.path.getsize(absolute) > MAX_REF_BYTES:
            continue
        data = read_work(root, path)
        if b"\0" in data[:8000]:
            continue
        count = len(pattern.findall(data))
        if count:
            refs.append({"path": path, "count": count, "dirty": path in dirty})
    return refs


def other_refs(root: str, old: str, skip: set) -> dict:
    """工作区里其它还引用旧 guid 的已跟踪文件，只报不改。"""
    ok, out = git(root, ["grep", "-l", "-I", "-i", "-F", "-z", old], 120)
    if not ok and out:
        return {"done": False, "paths": []}
    paths = sorted(path for path in out.split("\0") if path and path not in skip)
    return {"done": True, "paths": paths}


def scan(root: str) -> dict:
    dirty = dirty_paths(root)
    items = []
    for path, code in sorted(dirty.items()):
        if not path.endswith(".meta") or "D" in code:
            continue
        ok, head = git(root, ["show", "HEAD:" + path])
        if not ok:
            continue
        try:
            work = read_work(root, path).decode("utf-8", errors="replace")
        except OSError:
            continue
        old, new = guid_of(head), guid_of(work)
        item = {"path": path, "old_guid": old, "new_guid": new}
        if not old or not new:
            item.update(kind="unknown", note="读不到 guid 行，不处理。")
        elif old == new:
            item["kind"] = "import"
        else:
            item["kind"] = "guid"
            commit = dup_guid_commit(root, path, old)
            item["commit"] = commit
            own = {path} | set(commit.get("dup_paths") or [])
            item["refs"] = guid_refs(root, commit["hash"], own, old, dirty) if commit else []
            skip = own | {ref["path"] for ref in item["refs"]}
            item["others"] = other_refs(root, old, skip)
        items.append(item)
    return {"items": items}


def short_names(paths: list) -> str:
    names = [os.path.basename(path) for path in paths]
    text = "、".join(names[:5])
    return text + (f" 等 {len(names)} 个" if len(names) > 5 else "")


def commit(root: str, message: str, paths: list) -> str:
    ok, out = git(root, ["commit", "--only", "-m", message, "--"] + paths, 300)
    if not ok:
        raise RuntimeError("提交失败：" + out[-500:])
    ok, short = git(root, ["rev-parse", "--short", "HEAD"])
    return short.strip() if ok else ""


def commit_imports(root: str, paths: list | None = None) -> dict:
    """提交 guid 没变的 meta。paths 为空就提交全部。"""
    current = [item["path"] for item in scan(root)["items"] if item["kind"] == "import"]
    if paths:
        wanted = set(paths)
        missing = sorted(wanted - set(current))
        if missing:
            raise ValueError("这些不是只改导入设置的 meta，或已经没有本地修改：" + "、".join(missing))
        current = [path for path in current if path in wanted]
    if not current:
        raise ValueError("没有只改导入设置的 meta。")
    short = commit(root, f"meta 导入设置：{short_names(current)}", current)
    return {"ok": True, "commit": short, "paths": current,
            "message": f"已提交 {len(current)} 个 meta（{short}），没推送。"}


def fix_guid(root: str, path: str) -> dict:
    """把最近提交了重复 guid 的那次提交里引用旧 guid 的文件换成本地新 guid，和 meta 一起提交。"""
    item = next((row for row in scan(root)["items"] if row["path"] == path), None)
    if not item or item["kind"] != "guid":
        raise ValueError("这个 meta 现在不是 guid 改动，重新检查一下。")
    if not item.get("commit"):
        raise ValueError("找不到别的 meta 用了同一个 guid 的提交。")
    old, new = item["old_guid"], item["new_guid"]
    pattern = re.compile(re.escape(old.encode("ascii")), re.I)
    changed = []
    for ref in item["refs"]:
        data = read_work(root, ref["path"])
        replaced = pattern.sub(new.encode("ascii"), data)
        if replaced != data:
            with open(os.path.join(root, ref["path"]), "wb") as handle:
                handle.write(replaced)
            changed.append(ref["path"])
    name = os.path.basename(path)[:-5]
    dups = "、".join(os.path.basename(dup)[:-5] for dup in item["commit"]["dup_paths"])
    message = (f"{name} guid 和 {dups} 重复（{old}，{item['commit']['short']}），用本地的 {new}，"
               f"替换 {len(changed)} 个引用")
    try:
        short = commit(root, message, [path] + changed)
    except RuntimeError as error:
        raise RuntimeError(f"{error}\n已替换的 {len(changed)} 个文件留在工作区。")
    text = f"已把 {len(changed)} 个文件里的旧 guid 换成本地的，和 meta 一起提交（{short}），没推送。"
    others = item.get("others") or {}
    if others.get("paths"):
        text += f"还有 {len(others['paths'])} 个别的文件引用旧 guid，没改。"
    return {"ok": True, "commit": short, "paths": [path] + changed, "message": text}
