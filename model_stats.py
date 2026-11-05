"""大豆实际在用的模型和今天的调用情况，存 model_stats.json。大豆进程写，后台只读。

只记模型名、成功/失败次数、时间和一句错误，不记 Key、提示词和回答。
"""
import datetime
import json
import os
import threading

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
STATS_PATH = os.path.join(BOT_DIR, "model_stats.json")
ERROR_MAX = 160
BEIJING_TZ = datetime.timezone(datetime.timedelta(hours=8))
_lock = threading.Lock()


def _now() -> datetime.datetime:
    return datetime.datetime.now(BEIJING_TZ)


def load() -> dict:
    try:
        with open(STATS_PATH, encoding="utf-8") as stats_file:
            data = json.load(stats_file)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    if not isinstance(data.get("models"), dict):
        data["models"] = {}
    return data


def _save(data: dict) -> None:
    temporary = STATS_PATH + ".tmp"
    with open(temporary, "w", encoding="utf-8", newline="\n") as stats_file:
        json.dump(data, stats_file, ensure_ascii=False, indent=2)
    os.replace(temporary, STATS_PATH)


def set_config(uses: list, provider: str) -> None:
    """uses 是 [{"use": 用途, "model": 主模型, "fallbacks": [备用]}]，大豆启动时写一次。"""
    with _lock:
        data = load()
        data["uses"] = uses
        data["provider"] = provider
        data["started_at"] = _now().isoformat(timespec="seconds")
        _save(data)


def note(model: str, ok: bool, error: str = "", prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
    if not model:
        return
    now = _now()
    today = f"{now:%Y-%m-%d}"
    stamp = now.isoformat(timespec="seconds")
    prompt = max(0, int(prompt_tokens or 0))
    completion = max(0, int(completion_tokens or 0))
    try:
        with _lock:
            data = load()
            item = data["models"].get(model) or {}
            if item.get("day") != today:
                item.update({"day": today, "ok": 0, "fail": 0, "prompt": 0, "completion": 0, "calls_with_usage": 0})
            if ok:
                item["ok"] = int(item.get("ok") or 0) + 1
                item["last_ok_at"] = stamp
            else:
                item["fail"] = int(item.get("fail") or 0) + 1
                item["last_fail_at"] = stamp
                item["last_error"] = str(error or "")[:ERROR_MAX]
            if prompt or completion:
                item["prompt"] = int(item.get("prompt") or 0) + prompt
                item["completion"] = int(item.get("completion") or 0) + completion
                item["calls_with_usage"] = int(item.get("calls_with_usage") or 0) + 1
            data["models"][model] = item
            _save(data)
    except OSError:
        return


def view() -> dict:
    data = load()
    today = f"{_now():%Y-%m-%d}"
    models = {}
    for name, item in data["models"].items():
        fresh = item.get("day") == today
        models[name] = {
            **item,
            "ok": int(item.get("ok") or 0) if fresh else 0,
            "fail": int(item.get("fail") or 0) if fresh else 0,
            "prompt": int(item.get("prompt") or 0) if fresh else 0,
            "completion": int(item.get("completion") or 0) if fresh else 0,
            "calls_with_usage": int(item.get("calls_with_usage") or 0) if fresh else 0,
        }
    return {"uses": data.get("uses") or [], "provider": data.get("provider", ""), "started_at": data.get("started_at", ""),
            "models": models, "today": today}
