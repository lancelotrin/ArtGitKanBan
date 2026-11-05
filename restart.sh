#!/bin/bash
# 重启大豆后台（console.py，连带新看板后端）。用项目 .venv 里的 Python，那里装了 fastapi。
# 用法：./restart.sh
set -u
cd "$(dirname "$0")"

PY=".venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "没找到 $PY，先建环境：python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt"
  exit 1
fi

if pgrep -f "console.py" > /dev/null; then
  echo "停掉旧进程…"
  pkill -f "console.py"
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    pgrep -f "console.py" > /dev/null || break
    sleep 0.5
  done
  pgrep -f "console.py" > /dev/null && pkill -9 -f "console.py"
fi

echo "启动 console.py…"
nohup "$PY" console.py > console.log 2>&1 &
sleep 3

cat console.log
if curl -s -o /dev/null http://127.0.0.1:8765/; then
  echo "✓ 后台已起来：http://127.0.0.1:8765"
else
  echo "✗ 8765 没响应，看上面的 console.log"
  exit 1
fi
