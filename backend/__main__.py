"""单独起新后端调试：.venv/bin/python -m backend（平时由 console/server.py 在后台线程里带起来）。"""
import uvicorn

from backend.app import HOST, PORT, app

if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT)
