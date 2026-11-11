"""meta 检查看板的后台任务、日志、上次检查结果和推送记录。

三种任务，同一时间只跑一个，在后台线程里跑（拉代码、Unity 导入、推送都可能好几分钟，网页轮询进度）：
- check：拉 .env 配的工程当前分支到最新（只快进）→ 连 Unity MCP 让它刷新资源（meta 会重新生成）→ 检查 meta
- apply：全部处理并提交 → 推送当前分支
- push：再推一次（上次推送失败时用）

和大豆的拉代码、切分支共用 echo_bot._pull_lock。
"""
import json
import os
import threading
import time

from common.paths import DATA_DIR

LOG_PATH = os.path.join(DATA_DIR, "meta_fix.log")
PUSH_PATH = os.path.join(DATA_DIR, "meta_pushes.json")
SCAN_PATH = os.path.join(DATA_DIR, "meta_last_scan.json")
ACTIONS = {"check": "拉最新、刷新 Unity 并检查", "apply": "全部处理、提交并推送", "push": "再推一次"}

_job_lock = threading.Lock()
JOB = {"running": False, "action": "", "title": "", "step": "", "who": "", "started_at": "", "finished_at": "",
       "ok": None, "message": ""}


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def read_json(path: str, fallback):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return fallback


def write_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=1)
    os.replace(temp, path)


def log(lines: list) -> None:
    """日志单独一个文件（bot.log 是大豆进程在写），网页上显示。"""
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as handle:
            for index, line in enumerate(lines):
                handle.write((f"[{now()}] " if index == 0 else "") + line + "\n")
    except OSError:
        pass


def log_text(count: int = 200) -> str:
    try:
        with open(LOG_PATH, encoding="utf-8", errors="replace") as handle:
            return "\n".join(handle.read().splitlines()[-count:])
    except OSError:
        return ""


def pushes() -> list:
    items = read_json(PUSH_PATH, [])
    return items if isinstance(items, list) else []


def last_scan() -> dict:
    data = read_json(SCAN_PATH, {})
    return data if isinstance(data, dict) else {}


def view() -> dict:
    with _job_lock:
        job = dict(JOB)
    return {"job": job, "scan": last_scan() or None}


def step(text: str) -> None:
    with _job_lock:
        JOB["step"] = text
    log([text])


def project_root() -> str:
    from bot import echo_bot
    return echo_bot.get_git_root()


def do_check(root: str, who: str) -> tuple:
    from bot import echo_bot, meta_fix, unity_bridge
    from common import settings
    step(f"拉最新：{root}")
    pulled = meta_fix.pull(root, echo_bot.GIT_REMOTE, settings.git_branches())
    log(["  " + pulled["message"]])
    step("让 Unity 刷新资源")
    refreshed = unity_bridge.refresh(settings.project_root())
    log(["  " + refreshed["message"]])
    step("检查 meta")
    scan = meta_fix.scan(root)
    kinds = [item["kind"] for item in scan["items"]]
    timing = "，".join(f"{name} {sec} 秒" for name, sec in scan["timing"].items())
    summary = (f"导入设置 {kinds.count('import')} 个，guid 变了 {kinds.count('guid')} 个，"
               f"读不到 guid {kinds.count('unknown')} 个")
    log([f"  检查完：{summary}，用时 {scan['seconds']} 秒（{timing}）"])
    write_json(SCAN_PATH, dict(scan, root=root, checked_at=now(), who=who,
                               pull=pulled, unity=refreshed))
    notes = [note["message"] for note in (pulled, refreshed) if not note["ok"]]
    return True, f"检查完：{summary}。" + ("".join(notes) and " 注意：" + " ".join(notes))


def do_push(root: str, who: str) -> dict:
    """推当前分支，把推送结果和这次提交的详情记进推送记录（新的在前，留 50 条）和日志。"""
    from bot import echo_bot, meta_fix
    step("推送")
    pushed = meta_fix.push(root, echo_bot.GIT_REMOTE)
    entry = dict(meta_fix.commit_info(root), at=now(), who=who, branch=pushed["branch"], remote=pushed["remote"],
                 pushed=pushed["ok"], output=pushed["output"])
    write_json(PUSH_PATH, ([entry] + pushes())[:50])
    where = f"{pushed['remote']}/{pushed['branch']}"
    if pushed["ok"]:
        log([f"  推送 {entry.get('short', '')} 到 {where} 成功"])
    else:
        log([f"  推送 {entry.get('short', '')} 到 {where} 失败，提交留在本地："] +
            ["    " + line for line in pushed["output"].splitlines()[-12:]])
    return entry


def do_apply(root: str, who: str) -> tuple:
    from bot import meta_fix
    step("全部处理并提交")
    result = meta_fix.apply_all(root)
    log(result["details"])
    entry = do_push(root, who)
    scan = last_scan()
    if scan:
        write_json(SCAN_PATH, dict(scan, items=result["scan"]["items"]))
    where = f"{entry['remote']}/{entry['branch']}"
    tail = f"已推送到 {where}。" if entry["pushed"] else f"推送到 {where} 失败，提交留在本地，详情看推送记录。"
    return entry["pushed"], result["message"] + tail


def run(action: str, who: str) -> None:
    """在当前线程里跑完一个任务（start 在后台线程里调它；测试直接调）。"""
    from bot import echo_bot
    log([f"开始：{ACTIONS[action]}" + (f"（{who} 点的）" if who else "")])
    ok, message = False, ""
    if not echo_bot._pull_lock.acquire(timeout=600):
        message = "大豆正在拉代码或切分支，等了 10 分钟还没完，没做。"
    else:
        try:
            root = project_root()
            if action == "check":
                ok, message = do_check(root, who)
            elif action == "apply":
                ok, message = do_apply(root, who)
            else:
                entry = do_push(root, who)
                ok = entry["pushed"]
                message = (f"已推送 {entry.get('short', '')} 到 {entry['remote']}/{entry['branch']}。" if ok
                           else "推送失败，详情看推送记录。")
        except Exception as error:  # 后台线程里，什么错都要记下来，不能让任务卡在「进行中」
            message = f"{type(error).__name__}：{error}" if not isinstance(error, (ValueError, RuntimeError)) else str(error)
        finally:
            echo_bot._pull_lock.release()
    log([("完成：" if ok else "没成功：") + message])
    with _job_lock:
        JOB.update(running=False, step="", finished_at=now(), ok=ok, message=message)


def start(action: str, who: str) -> dict:
    if action not in ACTIONS:
        raise ValueError("不认识的操作。")
    with _job_lock:
        if JOB["running"]:
            return {"ok": False, "message": f"{JOB['who'] or '有人'} 正在「{JOB['title']}」，等它做完。", "job": dict(JOB)}
        JOB.update(running=True, action=action, title=ACTIONS[action], step="排队", who=who, started_at=now(),
                   finished_at="", ok=None, message="")
    threading.Thread(target=run, args=(action, who), daemon=True, name="meta-" + action).start()
    return {"ok": True, "message": f"已开始「{ACTIONS[action]}」。", "job": view()["job"]}
