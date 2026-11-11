"""工作区里本地改过的 .meta。

- guid 没变（只动了导入设置）：可以直接提交这些 meta。
- guid 变了：说明提交里的旧 guid 和别的资源重复了（Unity 本地给这个 meta 换了新 guid）。
  工作区里还有别的 .meta 也用这个旧 guid（和路径无关）。两边各自拿到这个 guid 的提交里，
  后的那次是重复提交。引用旧 guid 的文件，最早引用它的提交在重复提交之前的，是之前那个资源的引用；
  在重复提交或之后的，是后来那个资源的引用。本地这个 meta 是后来的（通常如此），就把后来的引用换成本地新 guid；
  本地这个是之前的，就把之前的引用换成本地新 guid。

一个按钮全部处理，一次提交。

apply_all 只 commit；push() 把当前分支推到远端（跳过 pre-push）。不拉、不切分支。调用方负责和拉代码互斥。
"""
import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
GUID_LINE = re.compile(r"(?m)^guid:\s*([0-9a-fA-F]{32})\s*$")
WORKERS = 8


def git(root: str, args: list, timeout: int = 60, stderr: bool = False) -> tuple:
    """返回 (成功, 输出)。失败时输出含 stderr；stderr=True 时成功也带上 stderr（push 的结果写在 stderr）。"""
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
    if stderr:
        return True, ((result.stdout or "") + (result.stderr or "")).strip()
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


def grep_guids(root: str, guids: list) -> tuple:
    """一次 git grep 查全部 guid 在工作区（已跟踪文件）的引用：(查完没有, {guid: {路径: 处数}})。"""
    args = ["grep", "-I", "-i", "-F", "-o", "-z"]
    for guid in guids:
        args += ["-e", guid]
    ok, out = git(root, args, 600)
    if not ok and out:
        return False, {}
    found = {guid: {} for guid in guids}
    for line in out.split("\n"):
        path, _, match = line.partition("\0")
        guid = match.strip().lower()
        if guid in found:
            found[guid][path] = found[guid].get(path, 0) + 1
    return True, found


def own_guid(root: str, path: str) -> str:
    try:
        with open(os.path.join(root, path), "rb") as handle:
            return guid_of(handle.read(4096).decode("utf-8", errors="replace"))
    except OSError:
        return ""


def guid_commit(root: str, guid: str, dup_paths: list) -> dict:
    """这几个 meta 最近一次把 guid 写进去的提交。只翻这几个文件的历史。"""
    if not dup_paths:
        return {}
    ok, out = git(root, ["log", "-1", "-G", rf"^guid: *{guid}", "--no-renames",
                         "--format=%H%x1f%h%x1f%an%x1f%ad%x1f%s%x1f%ct", "--date=format:%Y-%m-%d %H:%M",
                         "--name-only", "HEAD", "--"] + dup_paths, 300)
    lines = [line for line in out.splitlines() if line.strip()] if ok else []
    if not lines:
        return {}
    full, short, author, date, subject, stamp = (lines[0].split("\x1f") + [""] * 6)[:6]
    wanted = set(dup_paths)
    dups = []
    for meta_path in lines[1:]:
        if meta_path in wanted:
            shown, text = git(root, ["show", f"{full}:{meta_path}"])
            if shown and guid_of(text) == guid:
                dups.append(meta_path)
    if not dups:
        return {}
    return {"hash": full, "short": short, "author": author, "date": date, "subject": subject,
            "time": int(stamp or 0), "dup_paths": dups}


def first_ref_commit(root: str, path: str, guid: str) -> dict:
    """这个文件最早引用 guid 的提交。没有（只是本地加的）返回 {}。"""
    ok, out = git(root, ["log", "--reverse", "-G", guid, "--format=%H%x1f%h%x1f%an%x1f%ad%x1f%ct",
                         "--date=format:%Y-%m-%d %H:%M", "HEAD", "--", path], 300)
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


def dup_commit(root: str, path: str, guid: str, dup_paths: list) -> dict:
    """重复提交：本地这个 meta 和别的 meta 各自拿到 guid 的提交里，后的那次。mine_later 表示本地这个是后来的。"""
    other = guid_commit(root, guid, dup_paths)
    if not other:
        return {}
    mine = guid_commit(root, guid, [path])
    if not mine or not after_dup(root, mine, other):
        return dict(other, mine_later=False, mine=mine)
    return dict(mine, dup_paths=other["dup_paths"], mine_later=True, other=other)


def classify_ref(root: str, path: str, guid: str, dup: dict) -> dict:
    first = first_ref_commit(root, path, guid)
    if not first:
        return {"replace": False, "reason": "只在本地引用，没提交过", "since": first}
    later = after_dup(root, first, dup)
    if dup["mine_later"]:
        if later:
            return {"replace": True, "reason": "重复提交及之后引用的", "since": first}
        return {"replace": False, "reason": "重复提交之前就引用了，是之前那个资源的", "since": first}
    if later:
        return {"replace": False, "reason": "重复提交及之后引用的，是后来那个资源的", "since": first}
    return {"replace": True, "reason": "重复提交之前引用的，是本地这个资源的", "since": first}


def scan(root: str) -> dict:
    started = time.time()
    timing = {}

    def lap(name: str, since: float) -> float:
        now = time.time()
        timing[name] = round(now - since, 1)
        return now

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
        else:
            item["kind"] = "import" if old == new else "guid"
        items.append(item)
    mark = lap("读工作区", started)
    guids = [item for item in items if item["kind"] == "guid"]
    if guids:
        done, found = grep_guids(root, sorted({item["old_guid"] for item in guids}))
        mark = lap("查引用", mark)
        with ThreadPoolExecutor(WORKERS) as pool:
            jobs = []
            for item in guids:
                hits = found.get(item["old_guid"], {})
                dups = sorted(hit for hit in hits if hit != item["path"] and hit.endswith(".meta")
                              and own_guid(root, hit) == item["old_guid"])
                item["hits"] = hits
                jobs.append(pool.submit(dup_commit, root, item["path"], item["old_guid"], dups))
            for item, job in zip(guids, jobs):
                item["commit"] = job.result()
            mark = lap("找重复提交", mark)
            history = []
            for item in guids:
                hits = item.pop("hits")
                item["grep_done"] = done
                commit = item["commit"]
                if not commit:
                    item["note"] = "找不到别的 meta 用了同一个 guid 的提交，不处理。"
                    continue
                skip = {item["path"]} | set(commit["dup_paths"])
                item["refs"] = [{"path": path, "count": count, "dirty": path in dirty}
                                for path, count in sorted(hits.items()) if path not in skip]
                for ref in item["refs"]:
                    history.append((ref, pool.submit(classify_ref, root, ref["path"], item["old_guid"], commit)))
            for ref, job in history:
                ref.update(job.result())
            lap("查引用历史", mark)
    return {"items": items, "timing": timing, "seconds": round(time.time() - started, 1)}


def short_names(paths: list) -> str:
    names = [os.path.basename(path) for path in paths]
    text = "、".join(names[:5])
    return text + (f" 等 {len(names)} 个" if len(names) > 5 else "")


def commit(root: str, message: str, paths: list) -> str:
    # 只提交 meta 和 guid 引用，跳过工程的 pre-commit 检查（编译、模型场景检查等）
    ok, out = git(root, ["commit", "--only", "--no-verify", "-m", message, "--"] + paths, 300)
    if not ok:
        raise RuntimeError("提交失败：" + out[-500:])
    ok, short = git(root, ["rev-parse", "--short", "HEAD"])
    return short.strip() if ok else ""


def apply_all(root: str) -> dict:
    """一次处理全部：导入设置的 meta 直接提交；guid 重复的替换重复提交及之后的引用，和 meta 一起提交。"""
    items = scan(root)["items"]  # 提交前按当前工作区重新检查一遍
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
    originals = {}
    for path, pairs in sorted(replacements.items()):
        data = read_work(root, path)
        replaced = data
        for old, new in pairs:
            replaced = re.sub(re.escape(old.encode("ascii")), new.encode("ascii"), replaced, flags=re.I)
        if replaced != data:
            originals[path] = data
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
        # 没提交成功就把替换过的文件改回去，下次重新检查还能找到它们
        for path, data in originals.items():
            with open(os.path.join(root, path), "wb") as handle:
                handle.write(data)
        raise RuntimeError(f"{error}\n替换过的 {len(changed)} 个文件已改回原样。")
    kept = sum(1 for item in fixable for ref in item["refs"] if not ref["replace"])
    text = f"已提交（{short}）：" + "；".join(parts) + "。"
    if kept:
        text += f"有 {kept} 个引用旧 guid 的文件没改。"
    if skipped:
        text += f"{len(skipped)} 个 meta 没处理。"
    details = [text]
    for path in imports:
        details.append(f"  提交导入设置 {path}")
    for item in fixable:
        commit_info = item["commit"]
        side = "本地这个是后来的" if commit_info.get("mine_later") else "本地这个是之前的"
        details.append(f"  guid 重复 {item['path']}：{item['old_guid']} → {item['new_guid']}，"
                       f"重复提交 {commit_info['short']} {commit_info['author']} {commit_info['date']}（{side}）")
        for ref in item["refs"]:
            if ref["replace"]:
                state = "已换" if ref["path"] in changed else "没换到"
                details.append(f"    {state} {ref['path']}（{ref['count']} 处）")
            else:
                details.append(f"    不动 {ref['path']}：{ref['reason']}")
    for item in items:
        if item["path"] in skipped:
            details.append(f"  没处理 {item['path']}：{item.get('note') or '没查完引用'}")
    done = set(imports) | {item["path"] for item in fixable}
    remaining = {"items": [item for item in items if item["path"] not in done]}
    return {"ok": True, "commit": short, "paths": paths, "message": text, "details": details, "scan": remaining}


def commit_info(root: str, rev: str = "HEAD") -> dict:
    """提交的详细信息：提交号、作者、时间、说明、改了哪些文件。"""
    ok, out = git(root, ["show", "-s", "--format=%H%x1f%h%x1f%an%x1f%ad%x1f%B", "--date=format:%Y-%m-%d %H:%M:%S", rev])
    if not ok:
        return {}
    full, short, author, date, body = (out.split("\x1f", 4) + [""] * 5)[:5]
    ok, names = git(root, ["show", "--name-only", "--no-renames", "--format=", "-z", rev])
    files = [name for name in names.split("\0") if name] if ok else []
    return {"hash": full.strip(), "short": short.strip(), "author": author, "date": date,
            "message": body.strip(), "files": files}


def push(root: str, remote: str = "origin") -> dict:
    """把当前分支推到远端同名分支。不拉、不强推；远端有新提交会被拒，留给人处理。"""
    ok, branch = git(root, ["rev-parse", "--abbrev-ref", "HEAD"])
    branch = branch.strip()
    if not ok or not branch or branch == "HEAD":
        return {"ok": False, "branch": branch, "remote": remote, "output": "读不到当前分支（可能是游离 HEAD），没推送。"}
    ok, out = git(root, ["push", "--no-verify", "--porcelain", remote, f"HEAD:refs/heads/{branch}"], 600, stderr=True)
    return {"ok": ok, "branch": branch, "remote": remote, "output": out[-1500:]}


def pull(root: str, remote: str, branches: list) -> dict:
    """把当前分支快进到远端最新。只快进、不清本地修改；本地修改和远端新提交冲突就不拉，原样报出来。"""
    ok, branch = git(root, ["rev-parse", "--abbrev-ref", "HEAD"])
    branch = branch.strip()
    if not ok or not branch or branch == "HEAD":
        return {"ok": False, "message": "读不到当前分支（可能是游离 HEAD），没拉。"}
    if branch not in branches:
        return {"ok": False, "message": f"当前分支 {branch} 不在 .env 的 GIT_BRANCHES（{'、'.join(branches)}）里，没拉。"}
    ok, before = git(root, ["rev-parse", "--short", "HEAD"])
    ok, out = git(root, ["fetch", remote, branch], 600, stderr=True)
    if not ok:
        return {"ok": False, "message": f"fetch {remote}/{branch} 失败：{out[-500:]}"}
    ok, out = git(root, ["merge", "--ff-only", "FETCH_HEAD"], 600, stderr=True)
    if not ok:
        return {"ok": False, "message": f"{branch} 没能快进到 {remote}/{branch}（本地有远端没有的提交，或本地修改和远端新提交冲突），没拉：{out[-500:]}"}
    ok, after = git(root, ["rev-parse", "--short", "HEAD"])
    if before.strip() == after.strip():
        return {"ok": True, "message": f"{branch} 已是最新（{after.strip()}）。"}
    ok, count = git(root, ["rev-list", "--count", f"{before.strip()}..{after.strip()}"])
    return {"ok": True, "message": f"{branch} 从 {before.strip()} 快进到 {after.strip()}（{count.strip()} 个提交）。"}
