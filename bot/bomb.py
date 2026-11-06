"""炸线检测（只读 git）：合并时整份丢了某一侧的修改；远端分支被强推冲掉提交。

git 参数是 run_git(args) -> (ok, stdout)，由调用方套上只读白名单。
"""
import json
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
        # 只有展开的几条路径（旧看板记录没存全部路径）时，总数只扣掉被忽略的，别按展开数算
        full = bool(item.get("paths"))
        new_item["count"] = len(remain_paths) if full else max(int(item.get("count") or 0) - (len(paths) - len(remain_paths)),
                                                               len(remain_paths))
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


def commit_known(git, rev: str) -> bool:
    ok, out = git(["rev-parse", "-q", "--verify", f"{rev}^{{commit}}"])
    return bool(ok and out.strip())


def tip_of(git, ref: str) -> str:
    ok, out = git(["rev-parse", "-q", "--verify", f"{ref}^{{commit}}"])
    return out.strip() if ok else ""


def blob(git, rev: str, path: str) -> str:
    ok, out = git(["rev-parse", "-q", "--verify", f"{rev}:{path}"])
    return out.strip() if ok and out.strip() else ""


def lost_commits(git, keep: str, lost: str, path: str) -> list:
    """丢掉那侧改过这个文件的提交。--full-history 防止 git 简化历史时跳过；
    普通提交找不到（改动是解冲突时在合并里做的）就把合并提交也列上。"""
    base = ["log", "--full-history", f"-{LOST_COMMITS_SHOWN}", "--format=%h|%an|%ad|%s", "--date=format:%m-%d %H:%M"]
    for extra in (["--no-merges"], []):
        ok, out = git(base + extra + [f"{keep}..{lost}", "--", path])
        rows = [line for line in out.splitlines() if line.strip()] if ok else []
        if rows:
            return rows
    return []


def fill_revs(git, item: dict) -> None:
    """看板里的旧记录只存了短合并号：补出完整合并号，并按文件找出丢掉那侧（和合并结果内容不同的那个父提交）。"""
    if not item.get("merge_rev"):
        ok, out = git(["rev-parse", "-q", "--verify", f"{item.get('merge')}^{{commit}}"])
        item["merge_rev"] = out.strip() if ok else ""
    merge = item.get("merge_rev")
    if not merge:
        return
    ok, out = git(["log", "-1", "--format=%P", merge])
    parents = out.split() if ok else []
    if len(parents) != 2:
        return
    for detail in item.get("files") or []:
        if detail.get("lost_rev") and detail.get("keep_rev"):
            continue
        now = blob(git, merge, detail["path"])
        lost = [parent for parent in parents if blob(git, parent, detail["path"]) != now]
        if len(lost) == 1:
            detail["lost_rev"] = lost[0]
            detail["keep_rev"] = parents[1] if lost[0] == parents[0] else parents[0]
            if not detail.get("commits"):
                detail["commits"] = lost_commits(git, detail["keep_rev"], detail["lost_rev"], detail["path"])


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


MERGE_SUBJECT = re.compile(
    r"Merge (?:remote-tracking )?branch '([^']+)'(\s+of\s+\S+)?(?:\s+into\s+'?([^'\s]+)'?)?", re.IGNORECASE)


JUDGE_DIFF_PER_FILE = 2000
JUDGE_DIFF_TOTAL = 16000
JUDGE_VERDICTS = ("像是误丢", "像是故意保留", "看不出")
JUDGE_PROMPT = (
    "你在帮美术/程序团队排查 git 合并时丢修改（炸线）。下面是一次合并里疑似被整份丢掉的修改："
    "合并结果和合并人这一侧完全一样，合进来那一侧对这些文件的改动没了。\n"
    "对每个文件，看「丢掉那侧的改动」和丢掉的提交说明，判断合并人是解冲突时误丢（多半是选了「用我的」），"
    "还是有意不要这些修改（例如对方改错了、已被别的改动替代、是自动生成或缓存文件、两边只是格式差异）。"
    "文件现在的状态也给你了：「已补回」说明后来又加回来了。\n"
    "材料里给了合并提交号、合并人这一侧和丢掉那侧的提交号，以及预先取好的 diff。diff 不够、被截断或丢掉的提交说明不清楚时，"
    "请在当前工程目录里自己用只读的 git 命令按提交号查，例如 git show <提交号> --stat、git show <提交号> -- <文件>、"
    "git log <合并人这侧>..<丢掉那侧> -- <文件>、git diff <合并提交> <丢掉那侧> -- <文件>。"
    "查到改动内容后要给出明确结论；只有二进制文件、或查过仍看不出意图时才说看不出，不要因为材料里没贴 diff 就说看不出。\n"
    "只输出一个 JSON 数组，不要别的文字。每项：{\"path\": 原样的文件路径, "
    "\"verdict\": \"像是误丢\" | \"像是故意保留\" | \"看不出\", \"reason\": 不超过 30 个字的中文理由}。")


def judge_material(git, item: dict, branch: str) -> str:
    """给 Codex 判断用的材料：合并说明 + 每个文件「合并结果 → 丢掉那侧」的 diff（+ 是被丢掉的内容）。

    不用 merge-base：交叉合并时 merge-base 到丢掉那侧常常是空的，看不出丢了什么。"""
    fill_revs(git, item)
    parts = [f"分支：{branch}", f"合并提交：{item.get('merge_rev') or item.get('merge')} {item.get('date')} 合并人 {item.get('author')}",
             f"合并说明：{item.get('subject') or ''}"]
    used = 0
    for detail in item.get("files") or []:
        merged, lost = item.get("merge_rev"), detail.get("lost_rev")
        diff = ""
        if merged and lost and used < JUDGE_DIFF_TOTAL:
            ok, out = git(["diff", "--no-color", "-U2", merged, lost, "--", detail["path"]])
            diff = out if ok else ""
            if len(diff) > JUDGE_DIFF_PER_FILE:
                diff = diff[:JUDGE_DIFF_PER_FILE] + "\n…（太长截断）"
            used += len(diff)
        commits = "；".join(_commit_line(line) for line in detail.get("commits") or []) or "没找到对应提交"
        parts.append(f"\n### 文件：{detail['path']}\n现在的状态：{detail.get('state')}；丢的分支：{detail.get('lost_branch')}"
                     f"\n合并人这侧：{detail.get('keep_rev') or '未知'}；丢掉那侧：{lost or '未知'}"
                     f"\n丢掉的提交：{commits}\n合并结果 → 丢掉那侧的 diff（+ 是被丢掉的内容，- 是合并后才有、丢掉那侧没有的）：\n{diff.strip() or '（没取到 diff，可能是二进制文件或材料超长）'}")
    return "\n".join(parts)


def parse_judgement(answer: str, paths: list) -> dict:
    """Codex 回的 JSON 数组 → {路径: "像是误丢：理由"}。对不上的路径按文件名匹配，认不出的结论记成看不出。"""
    text = answer or ""
    start, end = text.find("["), text.rfind("]")
    try:
        rows = json.loads(text[start:end + 1]) if start >= 0 and end > start else []
    except ValueError:
        rows = []
    by_name = {}
    for path in paths:
        by_name.setdefault(path.rsplit("/", 1)[-1], path)
    found = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        raw = str(row.get("path") or "").strip()
        path = raw if raw in paths else by_name.get(raw.rsplit("/", 1)[-1])
        if not path:
            continue
        verdict = str(row.get("verdict") or "").strip()
        verdict = verdict if verdict in JUDGE_VERDICTS else "看不出"
        reason = re.sub(r"\s+", " ", str(row.get("reason") or "")).strip()[:60]
        found[path] = f"{verdict}：{reason}" if reason else verdict
    return found


def merge_sides(subject: str, branch: str) -> tuple:
    """从合并说明猜两侧分支名，返回 (第一父=合并人本地那侧, 第二父=合进来的那侧)。"""
    match = MERGE_SUBJECT.search(subject or "")
    if not match:
        return f"本地 {branch}", "合进来的分支"
    theirs = re.sub(r"^(origin|remotes/origin)/", "", match.group(1))
    ours = match.group(3) or branch
    pulled = bool(match.group(2)) or match.group(1).startswith(("origin/", "remotes/"))
    if theirs == ours:
        return f"本地 {ours}", f"远端 {theirs}"
    return f"本地 {ours}", f"远端 {theirs}" if pulled else theirs


def scan_merges(git, ref: str, since: str, base: str = "") -> tuple:
    """返回 (疑似炸线列表, 查过的合并数)。

    给了 base（上次扫到的分支最新提交）就只查 base..ref 之间新进来的合并，不看时间；
    这样晚推上来的旧合并也不会漏。base 不认识（比如被强推冲掉）时退回按 since 查。"""
    window = [f"{base}..{ref}"] if base and commit_known(git, base) else [ref, f"--since={since}"]
    ok, out = git(["log", *window, "--merges", f"-n{MERGE_SCAN_LIMIT}",
                   "--date=format:%m-%d %H:%M", "--pretty=format:%H|%P|%an|%ad|%aI|%s"])
    if not ok:
        return [], -1  # git 查失败：调用方别把「上次查到的位置」往前推
    rows = [line for line in out.splitlines() if line.strip()]
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
                files.append({"path": path, "lost": lost, "keep": keep, "side": 1 if lost == p1 else 2})
        if not files:
            continue
        shown = visible_paths([item["path"] for item in files])
        shown_set = set(shown)
        sides = merge_sides(subject, ref.split("/", 1)[-1])
        details = []
        for item in files:
            if item["path"] not in shown_set or len(details) >= FILES_SHOWN:
                continue
            details.append({
                "path": item["path"],
                "commits": lost_commits(git, item["keep"], item["lost"], item["path"]),
                "state": file_state(git, ref, merge, item["lost"], item["path"]),
                "lost_branch": sides[item["side"] - 1],
                "keep_rev": item["keep"], "lost_rev": item["lost"],
            })
        found.append({"merge": merge[:11], "merge_rev": merge, "author": author, "date": date, "at": at, "subject": subject,
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


def person_name(author: str, people: dict = None) -> str:
    """git 作者名换成自检名单里的真名，找不到就原样。people={小写作者: {"name", "open_id"}}。"""
    person = (people or {}).get(str(author or "").lower()) or {}
    return person.get("name") or author


def _person_mention(author: str, people: dict = None) -> str:
    person = (people or {}).get(str(author or "").lower()) or {}
    if person.get("open_id"):
        return f"<at id={person['open_id']}></at>"
    return f"**{person.get('name') or author}**"


def _commit_line(line: str, people: dict = None) -> str:
    parts = line.split("|", 3)
    if len(parts) < 4:
        return line
    short, author, date, subject = parts
    subject = re.sub(r"\s*https?://\S+", "", subject).strip()
    return f"{person_name(author, people)} {date}「{subject[:40]}」`{short}`"


def _lost_whose(detail: dict, people: dict = None) -> str:
    return "；".join(_commit_line(line, people) for line in detail.get("commits") or []) or "对面那侧的修改"


def _short_path(path: str) -> str:
    return "/".join(str(path or "").split("/")[-3:])


def by_time(merges: list) -> list:
    """按合并时间从新到旧，最近的排最前面。git log 和看板记录的顺序不一样，统一一下。"""
    return sorted(merges or [], key=lambda item: str(item.get("at") or ""), reverse=True)


def group_by_author(merges: list) -> list:
    """按合并人归组：先按时间从新到旧排，人按各自最近那次合并排，最近的在前。返回 [(合并人, [合并...])]。"""
    groups = {}
    for item in by_time(merges):
        groups.setdefault(item.get("author") or "未知", []).append(item)
    return list(groups.items())


def _person_title(author: str, items: list) -> str:
    total = sum(item.get("count") or 0 for item in items)
    return f"**{author}** 合并 {len(items)} 次，丢了 {total} 个文件的修改"


def format_report(branch: str, merges: list, forced: list) -> str:
    """纯文本/markdown 版（看板记录、自检复盘用）。分支名单独一行，按合并人归组。"""
    if not merges and not forced:
        return ""
    blocks = [f"**【{branch}】**"]
    for item in forced:
        lost = "；".join(_commit_line(line) for line in item["lost"][:5]) or "没列出被冲掉的提交"
        blocks.append(f"**疑似被强推**（{item['when']}）`{item['old']}` → `{item['new']}`\n冲掉：{lost}")
    for author, items in group_by_author(merges):
        lines = [_person_title(author, items)]
        for item in items:
            lines.append(f"合并 `{item['merge']}` {item['date']}：")
            for detail in item["files"]:
                branch_note = f" · 丢的分支：{detail['lost_branch']}" if detail.get("lost_branch") else ""
                lines.append(f"· `{_short_path(detail['path'])}`（{detail['state']}{branch_note}）丢的是：{_lost_whose(detail)}")
                if detail.get("judge"):
                    lines.append(f"  Codex 判断：{detail['judge']}")
            if item["count"] > len(item["files"]):
                lines.append(f"· 另外还有 {item['count'] - len(item['files'])} 个文件")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


CARD_TABLES_MAX = 5  # 飞书卡片表格组件数量有限，超出的人并进最后一张表
CARD_COLUMNS = (("merge", "合并"), ("file", "文件"), ("state", "状态"), ("branch", "丢的分支"), ("whose", "丢的提交"),
                ("judge", "Codex 判断"))


def _table_rows(items: list, with_author: bool = False, people: dict = None) -> list:
    rows = []
    for item in items:
        head = f"{item['merge']}\n{item['date']}"
        if with_author:
            head = f"{person_name(item.get('author') or '', people)}\n{head}"
        for detail in item["files"]:
            rows.append({"merge": head, "file": _short_path(detail["path"]), "state": detail["state"],
                         "branch": detail.get("lost_branch") or "对面那侧",
                         "whose": _lost_whose(detail, people).replace("`", ""), "judge": detail.get("judge") or "没判断"})
        if item["count"] > len(item["files"]):
            rows.append({"merge": head, "file": f"另外还有 {item['count'] - len(item['files'])} 个文件",
                         "state": "", "branch": "", "whose": "完整列表看炸线看板", "judge": ""})
    return rows


def _table(rows: list) -> dict:
    return {
        "tag": "table", "page_size": 10, "row_height": "auto", "freeze_first_column": False,
        "header_style": {"text_align": "left", "text_size": "normal", "background_style": "grey",
                         "bold": True, "lines": 1},
        "columns": [{"name": key, "display_name": label, "data_type": "text", "width": "auto",
                     "horizontal_align": "left", "vertical_align": "top"} for key, label in CARD_COLUMNS],
        "rows": rows,
    }


def build_card(title: str, intro: str, results: list, note: str = "", people: dict = None) -> dict:
    """飞书交互卡片（schema 2.0）：分支名一行，下面每个合并人一张表。results=[(分支, 合并, 强推)]。

    people 是自检名单（小写 git 作者 → 真名、open_id）：合并人有 open_id 就 @ 他，表里的作者也换成真名。"""
    elements = []
    if intro:
        elements.append({"tag": "markdown", "content": intro})
    def latest_at(entry) -> str:
        return max([str(item.get("at") or "") for item in entry[1]] or [""])  # 只有强推的分支排后面
    results = sorted(results, key=latest_at, reverse=True)
    groups = [(branch, author) for branch, merges, _forced in results for author, _items in group_by_author(merges)]
    own = set(groups if len(groups) <= CARD_TABLES_MAX else groups[:CARD_TABLES_MAX - 1])
    overflow = []
    for branch, merges, forced in results:
        if not merges and not forced:
            continue
        elements.append({"tag": "markdown", "content": f"**【{branch}】**"})
        for item in forced:
            lost = "；".join(_commit_line(line, people) for line in item["lost"][:5]) or "没列出被冲掉的提交"
            elements.append({"tag": "markdown",
                             "content": f"**疑似被强推**（{item['when']}）`{item['old']}` → `{item['new']}`\n冲掉：{lost}"})
        for author, items in group_by_author(merges):
            if (branch, author) not in own:
                overflow.append((branch, items))
                continue
            total = sum(item.get("count") or 0 for item in items)
            elements.append({"tag": "markdown",
                             "content": f"{_person_mention(author, people)} 合并 {len(items)} 次，丢了 {total} 个文件的修改"})
            elements.append(_table(_table_rows(items, people=people)))
    if overflow:
        names = " ".join(dict.fromkeys(_person_mention(items[0].get("author") or "", people) for _branch, items in overflow))
        elements.append({"tag": "markdown", "content": f"**其他人**（{names}）"})
        rows = []
        for branch, items in overflow:
            for row in _table_rows(items, with_author=True, people=people):
                row["merge"] = f"{branch} · {row['merge']}"
                rows.append(row)
        elements.append(_table(rows))
    if note:
        elements.append({"tag": "hr"})
        elements.append({"tag": "markdown", "content": f"<font color='grey'>{note}</font>"})
    return {
        "schema": "2.0",
        "config": {"width_mode": "fill"},
        "header": {"title": {"tag": "plain_text", "content": title}, "template": "red"},
        "body": {"elements": elements},
    }


BOMB_NOTE = "判断依据：某文件在被合进来的那一侧改过，合并结果却和另一侧完全一样，说明那侧修改被整份丢掉（多半是解冲突选了「用我的」）。也可能是故意保留，所以算疑似，请当事人确认。"
