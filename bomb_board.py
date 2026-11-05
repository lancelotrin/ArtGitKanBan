"""炸线看板的数据：每个疑似炸线的合并、每次强推各一条记录，存 bomb_history.json，保留 90 天。

只由大豆的炸线扫描写入，网页只读。同一个合并提交在两个分支都扫到时合成一条，分支记在 branches 里。
"""
import datetime
import json
import os
import threading

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_PATH = os.path.join(BOT_DIR, "bomb_history.json")
STATE_PATH = os.path.join(BOT_DIR, "bomb.json")  # 炸线扫描的开关、间隔、上次/下次时间（echo_bot 写）
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
    record.update({
        "author": str(item.get("author") or ""),
        "date": str(item.get("date") or ""),
        "at": str(item.get("at") or record.get("at") or stamp),
        "subject": str(item.get("subject") or ""),
        "count": int(item.get("count") or 0),
        "files": [{"path": str(detail.get("path") or ""), "state": str(detail.get("state") or ""),
                   "commits": [str(line) for line in detail.get("commits") or []]} for detail in item.get("files") or []],
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
