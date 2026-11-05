"""项目里的固定位置：代码按模块放在子目录，运行数据全放 data/，.env 留在项目根目录。

以前数据文件直接放在项目根目录；后台和机器人启动时调 migrate_legacy_data() 把它们挪进 data/。
"""
import os
import shutil

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT_DIR, "data")
ENV_PATH = os.path.join(ROOT_DIR, ".env")

# 以前放在根目录、现在归 data/ 的文件和目录
LEGACY_DATA = (
    "users.json", "sessions.json", "wishes.json", "help.json", "notes.json", "people.json", "faq.json",
    "coach.json", "reviews.json", "review_subs.json", "review_state.json", "reviews", "bomb.json",
    "bomb_history.json", "watch.json", "pull_schedule.json", "agents.json", "reminders.json",
    "merge_projects.json", "merge_open.json", "model_stats.json", "misses.jsonl", "questions.jsonl", "misses_handled.json",
    "chat_history.json", "gold_cache.json", "last_shader.json", "bilibili_cookie.txt",
    "chats.sqlite3", "chats.sqlite3-wal", "chats.sqlite3-shm",
    "ref_index.sqlite", "ref_index.sqlite-wal", "ref_index.sqlite-shm",
    "wiki", "stickers", "_img", "_coach_cwd", "_codex_work", "_agent_out",
    "bot.log", "bot.err.log", "console.log", "regress_out.txt",
)


def migrate_legacy_data(log=print) -> None:
    """把根目录里旧位置的数据挪进 data/。

    两边都有时：根目录的文件比 data/ 里的新（老进程重启前还在往根目录写），就用根目录的覆盖，
    data/ 里原来那份改名 .bak 留着；否则根目录那份不动，只提示。目录两边都有时一律不动。
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    for name in LEGACY_DATA:
        old = os.path.join(ROOT_DIR, name)
        if not os.path.exists(old):
            continue
        new = os.path.join(DATA_DIR, name)
        if os.path.exists(new):
            if os.path.isfile(old) and os.path.isfile(new) and os.path.getmtime(old) > os.path.getmtime(new):
                os.replace(new, new + ".bak")
                shutil.move(old, new)
                log(f"[data] {name} → data/{name}（根目录的更新，data/ 里原来那份留在 {name}.bak）")
            else:
                log(f"[data] 根目录和 data/ 都有 {name}，用 data/ 里的；根目录那份没动，确认没用了再删")
            continue
        shutil.move(old, new)
        log(f"[data] {name} → data/{name}")
