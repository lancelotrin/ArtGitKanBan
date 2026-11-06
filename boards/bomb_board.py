"""炸线看板的数据：每个疑似炸线的合并、每次强推各一条记录，存 bomb_history.json，保留 90 天。

由大豆的炸线扫描写入；网页上只能「标记已解决 / 撤销」（resolved、resolved_by、resolved_at）。
同一个合并提交在两个分支都扫到时合成一条，分支记在 branches 里。
没标记解决的记录会被大豆定时再提醒，提醒直接用这里存的内容，不重新查 git。
"""
import datetime
import json
import os
import threading
from common.paths import DATA_DIR

HISTORY_PATH = os.path.join(DATA_DIR, "bomb_history.json")
STATE_PATH = os.path.join(DATA_DIR, "bomb.json")  # 炸线扫描的开关、间隔、上次/下次时间（echo_bot 写）
KEEP_DAYS = 90
BEIJING_TZ = datetime.timezone(datetime.timedelta(hours=8))
_lock = threading.Lock()


def now_text() -> str:
    return datetime.datetime.now(BEIJING_TZ).isoformat(timespec="seconds")


def load() -> dict:
    try:
        with open(HISTORY_PATH, encoding="utf-8") as history_file:
            data = json.load(history_file)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    if not isinstance(data.get("records"), dict):
        data["records"] = {}
    return data


def save(data: dict) -> None:
    temporary = HISTORY_PATH + ".tmp"
    with open(temporary, "w", encoding="utf-8", newline="\n") as history_file:
        json.dump(data, history_file, ensure_ascii=False, indent=2)
    os.replace(temporary, HISTORY_PATH)


def merge_status(files: list) -> str:
    states = [str(item.get("state") or "") for item in files or []]
    if any(state == "还丢着" for state in states):
        return "还丢着"
    if states and all(state == "已补回" for state in states):
        return "已补回"
    return "要人工看"


def _add_branch(record: dict, branch: str) -> None:
    branches = list(record.get("branches") or [])
    if branch not in branches:
        branches.append(branch)
    record["branches"] = branches


def _upsert_merge(records: dict, branch: str, item: dict, told: set, stamp: str) -> bool:
    merge = str(item.get("merge") or "")
    if not merge:
        return False
    key = "merge:" + merge
    record = records.get(key)
    fresh = record is None
    if fresh:
        record = {"key": key, "kind": "merge", "merge": merge, "first_seen": stamp, "branches": []}
        records[key] = record
    _add_branch(record, branch)
    old_judge = {str(f.get("path")): f.get("judge") for f in record.get("files") or [] if f.get("judge")}
    record.update({
        "author": str(item.get("author") or ""),
        "date": str(item.get("date") or ""),
        "at": str(item.get("at") or record.get("at") or stamp),
        "subject": str(item.get("subject") or ""),
        "count": int(item.get("count") or 0),
        "paths": [str(path) for path in item.get("paths") or []] or list(record.get("paths") or []),
        "files": [{"path": str(detail.get("path") or ""), "state": str(detail.get("state") or ""),
                   "commits": [str(line) for line in detail.get("commits") or []],
                   "lost_branch": str(detail.get("lost_branch") or ""),
                   "judge": str(detail.get("judge") or old_judge.get(str(detail.get("path") or "")) or "")}
                  for detail in item.get("files") or []],
        "checked_at": stamp,
        "alerted": bool(record.get("alerted")) or merge in told,
    })
    record["status"] = merge_status(record["files"])
    return fresh


def _upsert_forced(records: dict, branch: str, item: dict, told: set, stamp: str) -> bool:
    new = str(item.get("new") or "")
    if not new:
        return False
    key = "force:" + new
    record = records.get(key)
    fresh = record is None
    if fresh:
        record = {"key": key, "kind": "force", "new": new, "first_seen": stamp, "at": stamp, "branches": []}
        records[key] = record
    _add_branch(record, branch)
    record.update({
        "when": str(item.get("when") or ""),
        "old": str(item.get("old") or ""),
        "lost": [str(line) for line in item.get("lost") or []],
        "checked_at": stamp,
        "status": "强推",
        "alerted": bool(record.get("alerted")) or key in told,
    })
    return fresh


def _prune(records: dict) -> None:
    cutoff = (datetime.datetime.now(BEIJING_TZ) - datetime.timedelta(days=KEEP_DAYS)).isoformat(timespec="seconds")
    for key in [key for key, record in records.items() if str(record.get("at") or record.get("first_seen") or "") < cutoff]:
        records.pop(key, None)


def record_scan(results: list, told, backfill: bool = False) -> int:
    """results 是 [(分支, 合并列表, 强推列表)]。told 是已经发过提醒的键。返回新增条数。"""
    told = {str(item) for item in told or []}
    stamp = now_text()
    added = 0
    with _lock:
        data = load()
        records = data["records"]
        for branch, merges, forced in results or []:
            for item in merges or []:
                added += _upsert_merge(records, str(branch), item, told, stamp)
            for item in forced or []:
                added += _upsert_forced(records, str(branch), item, told, stamp)
        _prune(records)
        data["updated_at"] = stamp
        if backfill:
            data["backfilled_at"] = stamp
        save(data)
    return added


def is_open(record: dict) -> bool:
    """还要提醒的：没人标记解决，而且不是全部已补回。"""
    return not record.get("resolved") and record.get("status") != "已补回"


def set_resolved(key: str, resolved: bool, by: str) -> dict:
    with _lock:
        data = load()
        record = data["records"].get(str(key or ""))
        if not record:
            raise KeyError("没有这条炸线记录，可能已经超过 90 天被清掉了。")
        record["resolved"] = bool(resolved)
        record["resolved_by"] = str(by or "")[:40] if resolved else ""
        record["resolved_at"] = now_text() if resolved else ""
        save(data)
        return dict(record)


def mark_alerted(keys) -> None:
    keys = {str(key) for key in keys or []}
    if not keys:
        return
    with _lock:
        data = load()
        changed = False
        for record in data["records"].values():
            short = record.get("merge") or ("force:" + str(record.get("new") or ""))
            if (short in keys or record.get("key") in keys) and not record.get("alerted"):
                record["alerted"] = True
                changed = True
        if changed:
            save(data)


def open_results() -> list:
    """没解决的记录，整理成扫描结果的样子 [(分支, 合并列表, 强推列表)]，给提醒直接用。按时间先后排。"""
    by_branch = {}
    for record in sorted(load()["records"].values(), key=lambda item: str(item.get("at") or item.get("first_seen") or "")):
        if not is_open(record):
            continue
        branch = (record.get("branches") or ["main"])[0]
        merges, forced = by_branch.setdefault(branch, ([], []))
        if record.get("kind") == "force":
            forced.append({"when": record.get("when", ""), "old": record.get("old", ""), "new": record.get("new", ""),
                           "lost": list(record.get("lost") or [])})
        else:
            files = [dict(item) for item in record.get("files") or []]
            merges.append({"merge": record.get("merge", ""), "author": record.get("author", ""),
                           "date": record.get("date", ""), "at": record.get("at", ""), "subject": record.get("subject", ""),
                           "count": int(record.get("count") or len(files)),
                           "paths": list(record.get("paths") or []), "files": files})
    return [(branch, merges, forced) for branch, (merges, forced) in by_branch.items()]


def needs_backfill() -> bool:
    return not load().get("backfilled_at")


def list_records() -> list:
    records = list(load()["records"].values())
    records.sort(key=lambda record: str(record.get("at") or record.get("first_seen") or ""), reverse=True)
    return records


def board_view() -> dict:
    data = load()
    return {"records": list_records(), "updated_at": data.get("updated_at", ""), "backfilled_at": data.get("backfilled_at", ""),
            "keep_days": KEEP_DAYS}


def scan_info() -> dict:
    """自动检查的状态，炸线看板顶上那行用。"""
    try:
        with open(STATE_PATH, encoding="utf-8") as state_file:
            state = json.load(state_file)
    except (OSError, ValueError):
        state = {}
    if not isinstance(state, dict):
        state = {}
    enabled = state.get("enabled", True) is not False
    return {"enabled": enabled, "minutes": state.get("minutes") or 30, "last_at": state.get("last_at", ""),
            "next_at": state.get("next_at", "") if enabled else ""}
