#!/bin/bash
# 重启机器人大豆（python -m bot）。和后台页面的「停止 → 启动」一样，用的是 console/server.py 里的 stop_bot / start_bot。
# 旧的 echo_bot.py 启动方式也会被认出来停掉；停完顺手删掉根目录里 data/ 已经有的旧数据文件，
# 免得新进程启动迁移时用根目录那份覆盖 data/。
# 用法：./restart_bot.sh
set -u
cd "$(dirname "$0")"

PY=".venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "没找到 $PY，先建环境：python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt"
  exit 1
fi

"$PY" - <<'EOF'
import os
import signal
import sys
import time

from common.paths import DATA_DIR, LEGACY_DATA, ROOT_DIR
from console.server import LOG_PATH, bot_processes, start_bot, stop_bot

old = bot_processes()
if old:
    print(f"停掉旧进程 {old}…")
    stop_bot()
    for _ in range(20):
        if not bot_processes():
            break
        time.sleep(0.5)
    for pid in bot_processes():
        print(f"{pid} 没停下，强制结束")
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    time.sleep(0.5)
else:
    print("大豆没在跑。")

# 旧进程留在根目录的数据文件：data/ 里已有同名文件的就删掉（data/ 才是现在用的）
for name in LEGACY_DATA:
    root_file = os.path.join(ROOT_DIR, name)
    if os.path.isfile(root_file) and os.path.isfile(os.path.join(DATA_DIR, name)):
        os.remove(root_file)
        print(f"删掉根目录残留的 {name}（用 data/{name}）")

# 关掉终端时别把大豆一起带走：忽略 SIGHUP，子进程会继承
signal.signal(signal.SIGHUP, signal.SIG_IGN)
print("启动 python -m bot…")
message = start_bot()
if message:
    print(f"✗ {message}")
    sys.exit(1)
time.sleep(2)
pids = bot_processes()
if not pids:
    print(f"✗ 大豆没起来，看 {LOG_PATH}")
    sys.exit(1)
print(f"✓ 大豆已起来，pid={pids}，日志 {LOG_PATH}")
EOF
