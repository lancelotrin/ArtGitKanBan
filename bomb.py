"""炸线检测（只读 git）：合并时整份丢了某一侧的修改；远端分支被强推冲掉提交。

git 参数是 run_git(args) -> (ok, stdout)，由调用方套上只读白名单。
"""
import re

MERGE_SCAN_LIMIT = 80
FILES_SHOWN = 8
LOST_COMMITS_SHOWN = 2
MAX_DAYS = 7
DEFAULT_DAYS = 3
REFLOG_SCAN = 200

BOMB_PATTERN = re.compile(
    r"炸线|炸了线|合丢|合并.{0,8}(丢|没了)|丢了.{0,6}(修改|提交|代码)|(修改|提交|代码).{0,4}(被)?(丢|冲掉|没了)|"
    r"被(人)?覆盖|强推|force.?push", re.IGNORECASE)
NOISE_PATTERN = re.compile(r"(^|/)FolderHashCache\.txt$", re.IGNORECASE)
# 提醒默认忽略的路径前缀（自动上传目录）。看板仍记完整结果；可在控制台改。
DEFAULT_IGNORE_PATHS = ("BattleLibrary/output/", "Assets/DataTable/")


def normalize_ignore_path(raw: str) -> str:
    text = str(raw or "").strip().replace("\\", "/")
    if not text or text.startswith("#"):
        return ""
    while text.startswith("./"):
        text = text[2:]
    if text.lower().startswith("client/"):
        text = text[7:]
    return text.rstrip("/") + "/" if text else ""


def parse_ignore_paths(text_or_list) -> list:
    if isinstance(text_or_list, str):
        rows = text_or_list.splitlines()
    else:
        rows = list(text_or_list or [])
    return list(dict.fromkeys(path for path in (normalize_ignore_path(row) for row in rows) if path))


def path_ignored(path: str, ignore_paths: list) -> bool:
    if not ignore_paths:
        return False
    normalized = str(path or "").replace("\\", "/")
    if normalized.lower().startswith("client/"):
        normalized = normalized[7:]
    lower = normalized.lower()
    return any(lower.startswith(prefix.lower()) for prefix in ignore_paths if prefix)


def filter_merges_for_alert(merges: list, ignore_paths: list) -> tuple:
    """提醒用：按路径前缀去掉忽略文件；滤完后没有剩余路径的合并整条不报。

    用 item['paths']（全部丢改路径）判断，不只看已展开的 files，避免自动上传目录
    排在字母序前面把真实改动挤出前 8 条时误伤。看板仍用原始 merges。
    返回 (过滤后列表, 从提醒里拿掉的文件数)。
    """
    if not ignore_paths:
        return list(merges or []), 0
    kept = []
    dropped = 0
    for item in merges or []:
        paths = list(item.get("paths") or [detail.get("path") for detail in item.get("files") or []])
        remain_paths = [path for path in paths if path and not path_ignored(path, ignore_paths)]
        dropped += len(paths) - len(remain_paths)
        if not remain_paths:
            continue
        remain_set = set(remain_paths)
        visible = [detail for detail in item.get("files") or [] if detail.get("path") in remain_set]
        new_item = dict(item)
        new_item["paths"] = remain_paths
        new_item["files"] = visible
        new_item["count"] = len(remain_paths)
        kept.append(new_item)
    return kept, dropped


def is_bomb_request(text: str) -> bool:
    return bool(BOMB_PATTERN.search(text or ""))


def parse_days(text: str) -> int:
    raw = text or ""
    if "今天" in raw:
        return 0
    if "昨天" in raw:
        return 1
    match = re.search(r"(\d{1,2})\s*天", raw)
    if match:
        return min(max(int(match.group(1)), 1), MAX_DAYS)
    if re.search(r"这周|本周|一周", raw):
        return MAX_DAYS
    return DEFAULT_DAYS


def parse_branches(text: str, allowed: list) -> list:
    raw = (text or "").lower()
    picked = [branch for branch in allowed if branch.lower() != "main" and branch.lower() in raw]
    if re.search(r"(?<![a-z_])main(?![a-z_])|主干", raw):
        picked.insert(0, "main")
    return [branch for branch in dict.fromkeys(picked) if branch in allowed] or list(allowed)


def per_parent_files(git, merge: str) -> list:
    ok, out = git(["show", "-m", "--name-only", "--format=@@%H", merge])
    blocks = []
    for line in (out.splitlines() if ok else []):
        if line.startswith("@@"):
            blocks.append(set())
        elif line.strip() and blocks:
            blocks[-1].add(line.strip())
    return blocks


def side_files(git, keep: str, lost: str) -> set:
    ok, out = git(["log", "--format=", "--name-only", f"{keep}..{lost}"])
    return {line.strip() for line in out.splitlines() if line.strip()} if ok else set()


def blob(git, rev: str, path: str) -> str:
    ok, out = git(["rev-parse", "-q", "--verify", f"{rev}:{path}"])
    return out.strip() if ok and out.strip() else ""


def lost_commits(git, keep: str, lost: str, path: str) -> list:
    ok, out = git(["log", "--no-merges", f"-{LOST_COMMITS_SHOWN}", "--format=%h|%an|%ad|%s", "--date=format:%m-%d %H:%M",
                   f"{keep}..{lost}", "--", path])
    return [line for line in out.splitlines() if line.strip()] if ok else []


def file_state(git, tip: str, merge: str, lost: str, path: str) -> str:
    now = blob(git, tip, path)
    if now and now == blob(git, lost, path):
        return "已补回"
    if now == blob(git, merge, path):
        return "还丢着"
    return "之后又改过，要人工看"


def visible_paths(paths: list) -> list:
    """同名文件和它的 .meta 只列一个。"""
    names = set(paths)
    return [path for path in paths if not (path.endswith(".meta") and path[:-5] in names)]


def scan_merges(git, ref: str, since: str) -> tuple:
    """返回 (疑似炸线列表, 查过的合并数)。"""
    ok, out = git(["log", ref, "--merges", f"-n{MERGE_SCAN_LIMIT}", f"--since={since}",
                   "--date=format:%m-%d %H:%M", "--pretty=format:%H|%P|%an|%ad|%aI|%s"])
    rows = [line for line in out.splitlines() if line.strip()] if ok else []
    found = []
    for row in rows:
        parts = row.split("|", 5)
        if len(parts) < 6 or len(parts[1].split()) != 2:
            continue
        merge, parents, author, date, at, subject = parts
        p1, p2 = parents.split()
        blocks = per_parent_files(git, merge)
        if len(blocks) != 2:
            continue
        vs_p1, vs_p2 = blocks
        files = []
        for keep, lost, vs_keep, vs_lost in ((p1, p2, vs_p1, vs_p2), (p2, p1, vs_p2, vs_p1)):
            if not vs_lost - vs_keep:
                continue
            dropped = sorted((side_files(git, keep, lost) & vs_lost) - vs_keep)
            for path in dropped:
                if NOISE_PATTERN.search(path):
                    continue
                files.append({"path": path, "lost": lost, "keep": keep})
        if not files:
            continue
        shown = visible_paths([item["path"] for item in files])
        shown_set = set(shown)
        details = []
        for item in files:
            if item["path"] not in shown_set or len(details) >= FILES_SHOWN:
                continue
            details.append({
                "path": item["path"],
                "commits": lost_commits(git, item["keep"], item["lost"], item["path"]),
                "state": file_state(git, ref, merge, item["lost"], item["path"]),
            })
        found.append({"merge": merge[:11], "author": author, "date": date, "at": at, "subject": subject,
                      "count": len(shown), "paths": shown, "files": details})
    return found, len(rows)


def scan_forced(git, ref: str) -> list:
    """本机 fetch 记录里 forced-update 的那几次，列出被冲掉的提交。"""
    ok, out = git(["log", "-g", f"-n{REFLOG_SCAN}", "--date=format:%m-%d %H:%M", "--pretty=format:%H|%gd|%gs", ref])
    entries = [line.split("|", 2) for line in out.splitlines() if line.count("|") >= 2] if ok else []
    found = []
    for index, (new, when, message) in enumerate(entries):
        if "forced" not in message.lower() or index + 1 >= len(entries):
            continue
        old = entries[index + 1][0]
        okl, lost = git(["log", "-20", "--format=%h|%an|%ad|%s", "--date=format:%m-%d %H:%M", f"{new}..{old}"])
        found.append({"when": when, "old": old[:11], "new": new[:11],
                      "lost": [line for line in lost.splitlines() if line.strip()] if okl else []})
    return found


def _commit_line(line: str) -> str:
    parts = line.split("|", 3)
    if len(parts) < 4:
        return line
    short, author, date, subject = parts
    subject = re.sub(r"\s*https?://\S+", "", subject).strip()
    return f"{author} {date}「{subject[:40]}」`{short}`"


def format_report(branch: str, merges: list, forced: list) -> str:
    blocks = []
    for item in forced:
        lost = "；".join(_commit_line(line) for line in item["lost"][:5]) or "没列出被冲掉的提交"
        blocks.append(f"**{branch} 疑似被强推**（{item['when']}）`{item['old']}` → `{item['new']}`\n冲掉：{lost}")
    for item in merges:
        lines = [f"**{branch}** `{item['merge']}` {item['date']} **{item['author']}** 合并，丢了 {item['count']} 个文件的修改："]
        for detail in item["files"]:
            name = "/".join(detail["path"].split("/")[-3:])
            whose = "；".join(_commit_line(line) for line in detail["commits"]) or "对面那侧的修改"
            lines.append(f"· `{name}`（{detail['state']}）丢的是：{whose}")
        if item["count"] > len(item["files"]):
            lines.append(f"· 另外还有 {item['count'] - len(item['files'])} 个文件")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


BOMB_NOTE = "判断依据：某文件在被合进来的那一侧改过，合并结果却和另一侧完全一样，说明那侧修改被整份丢掉（多半是解冲突选了「用我的」）。也可能是故意保留，所以算疑似，请当事人确认。"
