"""工作区里本地改过的 .meta。

- guid 没变（只动了导入设置）：可以直接提交这些 meta。
- guid 变了：说明提交里的旧 guid 和别的资源重复了（Unity 本地给这个 meta 换了新 guid）。
  找最近一次提交了「另一个 .meta 也用这个旧 guid」的提交（重复提交），和路径无关。
  工作区里引用旧 guid 的文件，最早引用它的提交在重复提交之前的，是之前那个资源的引用，不动；
  在重复提交或之后的，换成本地的新 guid。

一个按钮全部处理，一次提交。

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
                         "--format=\x1e%H%x1f%h%x1f%an%x1f%ad%x1f%s%x1f%ct", "--date=format:%Y-%m-%d %H:%M",
                         "--name-only", "HEAD", "--", "*.meta", f":(exclude){path}"], 120)
    if not ok:
        return {}
    for block in out.split("\x1e"):
        lines = [line for line in block.strip().splitlines() if line.strip()]
        if not lines:
            continue
        full, short, author, date, subject, stamp = (lines[0].split("\x1f") + [""] * 6)[:6]
        dups = []
        for meta_path in lines[1:]:
            if meta_path == path or not meta_path.endswith(".meta"):
                continue
            shown, text = git(root, ["show", f"{full}:{meta_path}"])
            if shown and guid_of(text) == guid:
                dups.append(meta_path)
        if dups:
            return {"hash": full, "short": short, "author": author, "date": date, "subject": subject,
                    "time": int(stamp or 0), "dup_paths": dups}
    return {}


def first_ref_commit(root: str, path: str, guid: str) -> dict:
    """这个文件最早引用 guid 的提交。没有（只是本地加的）返回 {}。"""
    ok, out = git(root, ["log", "--reverse", "-G", guid, "--format=%H%x1f%h%x1f%an%x1f%ad%x1f%ct",
                         "--date=format:%Y-%m-%d %H:%M", "HEAD", "--", path], 120)
    lines = out.strip().splitlines() if ok else []
    if not lines:
        return {}
    full, short, author, date, stamp = (lines[0].split("\x1f") + [""] * 5)[:5]
    return {"hash": full, "short": short, "author": author, "date": date, "time": int(stamp or 0)}


def is_ancestor(root: str, older: str, newer: str) -> bool:
    return git(root, ["merge-base", "--is-ancestor", older, newer])[0]


def after_dup(root: str, first: dict, dup: dict) -> bool:
    """引用是在重复提交（含）之后加的？先看提交先后关系，平行分支再比提交时间。"""
    if first["hash"] == dup["hash"] or is_ancestor(root, dup["hash"], first["hash"]):
        return True
    if is_ancestor(root, first["hash"], dup["hash"]):
        return False
    return first["time"] >= dup["time"]


def guid_refs(root: str, old: str, dup: dict, skip: set, dirty: dict) -> dict:
    """工作区里引用旧 guid 的已跟踪文件，分成要换的（重复提交及之后引用的）和不动的。"""
    ok, out = git(root, ["grep", "-l", "-I", "-i", "-F", "-z", old], 120)
    done = ok or not out
    pattern = re.compile(re.escape(old.encode("ascii")), re.I)
    refs = []
    for path in sorted({item for item in out.split("\0") if item} - skip) if done else []:
        absolute = os.path.join(root, path)
        if not os.path.isfile(absolute) or os.path.getsize(absolute) > MAX_REF_BYTES:
            continue
        count = len(pattern.findall(read_work(root, path)))
        if not count:
            continue
        first = first_ref_commit(root, path, old)
        if not first:
            replace, reason = False, "只在本地引用，没提交过"
        elif after_dup(root, first, dup):
            replace, reason = True, "重复提交及之后引用的"
        else:
            replace, reason = False, "重复提交之前就引用了，是之前那个资源的"
        refs.append({"path": path, "count": count, "dirty": path in dirty, "replace": replace,
                     "reason": reason, "since": first})
    return {"done": done, "refs": refs}


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
            if commit:
                found = guid_refs(root, old, commit, {path} | set(commit["dup_paths"]), dirty)
                item["refs"], item["grep_done"] = found["refs"], found["done"]
            else:
                item["note"] = "找不到别的 meta 用了同一个 guid 的提交，不处理。"
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


def apply_all(root: str) -> dict:
    """一次处理全部：导入设置的 meta 直接提交；guid 重复的替换重复提交及之后的引用，和 meta 一起提交。"""
    items = scan(root)["items"]
    imports = [item["path"] for item in items if item["kind"] == "import"]
    fixable = [item for item in items if item["kind"] == "guid" and item.get("commit") and item.get("grep_done")]
    skipped = [item["path"] for item in items if item["kind"] != "import" and item not in fixable]
    if not imports and not fixable:
        raise ValueError("没有能处理的 meta。")
    replacements = {}
    for item in fixable:
        for ref in item["refs"]:
            if ref["replace"]:
                replacements.setdefault(ref["path"], []).append((item["old_guid"], item["new_guid"]))
    changed = []
    for path, pairs in sorted(replacements.items()):
        data = read_work(root, path)
        replaced = data
        for old, new in pairs:
            replaced = re.sub(re.escape(old.encode("ascii")), new.encode("ascii"), replaced, flags=re.I)
        if replaced != data:
            with open(os.path.join(root, path), "wb") as handle:
                handle.write(replaced)
            changed.append(path)
    parts = []
    if imports:
        parts.append(f"导入设置 {len(imports)} 个")
    if fixable:
        names = short_names([item["path"][:-5] for item in fixable])
        parts.append(f"guid 重复 {len(fixable)} 个（{names}）用本地新 guid，替换 {len(changed)} 个文件里的引用")
    paths = imports + [item["path"] for item in fixable] + changed
    try:
        short = commit(root, "meta 处理：" + "；".join(parts), paths)
    except RuntimeError as error:
        raise RuntimeError(f"{error}\n已替换的 {len(changed)} 个文件留在工作区。")
    kept = sum(1 for item in fixable for ref in item["refs"] if not ref["replace"])
    text = f"已提交（{short}），没推送：" + "；".join(parts) + "。"
    if kept:
        text += f"有 {kept} 个文件在重复提交之前就引用旧 guid，没改。"
    if skipped:
        text += f"{len(skipped)} 个 meta 没处理：" + "、".join(os.path.basename(path) for path in skipped)
    return {"ok": True, "commit": short, "paths": paths, "message": text}
