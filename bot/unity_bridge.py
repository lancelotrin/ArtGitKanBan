"""直接连工程里 MCP for Unity（com.coplaydev.unity-mcp）在 Unity 编辑器里开的 TCP 端口，发命令。

协议（见包里 StdioBridgeHost.cs）：连上后 Unity 先发一行 "WELCOME UNITY-MCP 1 FRAMING=1"；
之后每条消息都是 8 字节大端长度 + UTF-8 JSON。命令是 {"type": 工具名, "params": {...}}。
端口写在 ~/.unity-mcp/unity-mcp-status-<sha1(Application.dataPath) 前 8 位>.json 的 unity_port 里，默认 6400。
"""
import glob
import hashlib
import json
import os
import socket
import struct
import time

DEFAULT_PORT = 6400
CONNECT_TIMEOUT = 5
REFRESH_TIMEOUT = 900


def status_dir() -> str:
    return os.environ.get("UNITY_MCP_STATUS_DIR") or os.path.join(os.path.expanduser("~"), ".unity-mcp")


def data_path(project_root: str) -> str:
    """Unity 的 Application.dataPath：工程目录/Assets，分隔符是 /。"""
    return os.path.abspath(os.path.join(project_root, "Assets")).replace("\\", "/")


def read_status(project_root: str) -> dict:
    """这个工程的 Unity 状态文件。按哈希找不到就挨个比 project_path。没有返回 {}。"""
    target = data_path(project_root)
    name = f"unity-mcp-status-{hashlib.sha1(target.encode('utf-8')).hexdigest()[:8]}.json"
    candidates = [os.path.join(status_dir(), name)] + sorted(glob.glob(os.path.join(status_dir(), "unity-mcp-status-*.json")))
    for path in candidates:
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            continue
        if str(data.get("project_path") or "").replace("\\", "/").rstrip("/").lower() == target.rstrip("/").lower():
            return data
    return {}


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks = b""
    while len(chunks) < size:
        chunk = sock.recv(size - len(chunks))
        if not chunk:
            raise ConnectionError("Unity 断开了连接。")
        chunks += chunk
    return chunks


def call(port: int, command: str, params: dict, timeout: float = REFRESH_TIMEOUT) -> dict:
    with socket.create_connection(("127.0.0.1", port), timeout=CONNECT_TIMEOUT) as sock:
        sock.settimeout(CONNECT_TIMEOUT)
        hello = b""
        while not hello.endswith(b"\n") and len(hello) < 256:
            chunk = sock.recv(1)
            if not chunk:
                break
            hello += chunk
        if b"FRAMING=1" not in hello:
            raise ConnectionError(f"端口 {port} 不是 MCP for Unity：{hello[:80]!r}")
        payload = json.dumps({"type": command, "params": params}).encode("utf-8")
        sock.sendall(struct.pack(">Q", len(payload)) + payload)
        sock.settimeout(timeout)
        (size,) = struct.unpack(">Q", _recv_exact(sock, 8))
        return json.loads(_recv_exact(sock, size).decode("utf-8", errors="replace"))


def refresh(project_root: str) -> dict:
    """让 Unity 刷新资源数据库（强制、同步导入），等它导完。返回 {ok, message}。"""
    status = read_status(project_root)
    deadline = time.time() + 60
    while status.get("reloading") and time.time() < deadline:
        time.sleep(2)
        status = read_status(project_root)
    port = int(status.get("unity_port") or DEFAULT_PORT)
    try:
        reply = call(port, "refresh_unity", {"mode": "force", "scope": "all", "wait_for_ready": True})
    except (OSError, ConnectionError, ValueError) as error:
        opened = "（找到了状态文件）" if status else "（没找到这个工程的状态文件，Unity 可能没开或 MCP 没启动）"
        return {"ok": False, "message": f"连不上 Unity 端口 {port}{opened}：{error}"}
    result = reply.get("result") if isinstance(reply.get("result"), dict) else reply
    if reply.get("status") == "error" or result.get("success") is False:
        return {"ok": False, "message": "Unity 刷新失败：" + json.dumps(reply, ensure_ascii=False)[:300]}
    state = (result.get("data") or {}).get("resulting_state", "") if isinstance(result.get("data"), dict) else ""
    return {"ok": True, "message": f"Unity 已刷新资源（端口 {port}{'，' + state if state else ''}）。"}
