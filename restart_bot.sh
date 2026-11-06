#!/bin/bash
# 重启机器人大豆（python -m bot）。和后台页面的「停止 → 启动」一样，用的是 console/server.py 里的 stop_bot / start_bot。
# 旧的 echo_bot.py 启动方式也会被认出来停掉。
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
