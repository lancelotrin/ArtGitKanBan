"""让 Unity 通过 MCP for Unity（com.coplaydev.unity-mcp）刷新资源。两种连法，先试 HTTP：

HTTP（10.x 默认）：本机跑一个 mcp-for-unity 服务（默认 http://127.0.0.1:8080），Unity 编辑器用 WebSocket 连上它。
工程 Library/MCPForUnity/RunState/mcp_http_<端口>.pid 记着端口。GET /api/instances 看连着哪些 Unity，
POST /api/command {"type", "params", "unity_instance"} 发命令（服务的 CLI 接口，只在本机模式开）。

TCP（stdio 模式）：Unity 编辑器自己开端口。协议（见包里 StdioBridgeHost.cs）：连上后 Unity 先发一行 "WELCOME UNITY-MCP 1 FRAMING=1"；
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
import urllib.error
import urllib.request

DEFAULT_PORT = 6400
DEFAULT_HTTP_PORT = 8080
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


def http_ports(project_root: str) -> list:
    """HTTP 服务的端口：先看工程 RunState 里的 pid 文件名，再加默认 8080。"""
    ports = []
    for path in sorted(glob.glob(os.path.join(project_root, "Library", "MCPForUnity", "RunState", "mcp_http_*.pid"))):
        digits = os.path.basename(path)[len("mcp_http_"):-len(".pid")]
        if digits.isdigit() and int(digits) not in ports:
            ports.append(int(digits))
    return ports + ([DEFAULT_HTTP_PORT] if DEFAULT_HTTP_PORT not in ports else [])


def http_json(url: str, body=None, timeout: float = CONNECT_TIMEOUT) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"},
                                     method="GET" if body is None else "POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read()
    except urllib.error.HTTPError as error:  # 服务出错也回 JSON（503 没连 Unity、500 超时等）
        text = error.read()
        if not text:
            raise
    return json.loads(text.decode("utf-8", errors="replace"))


def pick_instance(instances: list, project_root: str):
    """连着好几个 Unity 时按工程目录名挑；只连着一个就用它。"""
    name = os.path.basename(os.path.abspath(project_root).rstrip("/"))
    for item in instances:
        if str(item.get("project") or "").lower() == name.lower():
            return item
    return instances[0] if len(instances) == 1 else None


def refresh_http(project_root: str, port: int) -> dict:
    """返回 None 表示这个端口没有 HTTP 服务（再试别的连法）。"""
    base = f"http://127.0.0.1:{port}"
    try:
        health = http_json(base + "/health")
    except (OSError, ValueError):
        return None
    if "unity" not in str(health.get("message", "")).lower():
        return None
    try:
        instances = http_json(base + "/api/instances").get("instances") or []
    except (OSError, ValueError) as error:
        return {"ok": False, "message": f"MCP 服务（端口 {port}）查 Unity 列表失败：{error}"}
    if not instances:
        return {"ok": False, "message": f"MCP 服务（端口 {port}）开着，但没有 Unity 连上来："
                                        "打开 Unity 的 Window > MCP for Unity，点 Start Session / Connect 再检查。"}
    target = pick_instance(instances, project_root)
    if not target:
        names = "、".join(str(item.get("project")) for item in instances)
        return {"ok": False, "message": f"MCP 服务（端口 {port}）连着好几个 Unity（{names}），没有叫这个工程名的。"}
    params = {"mode": "force", "scope": "all", "wait_for_ready": True, "timeout_seconds": REFRESH_TIMEOUT}
    try:
        reply = http_json(base + "/api/command", {"type": "refresh_unity", "params": params,
                                                  "unity_instance": f"{target.get('project')}@{target.get('hash')}"},
                          timeout=REFRESH_TIMEOUT + 30)
    except (OSError, ValueError) as error:
        return {"ok": False, "message": f"让 Unity 刷新时出错（端口 {port}）：{type(error).__name__} {error}"}
    return judge(reply, f"{target.get('project')}，MCP 服务端口 {port}")


def judge(reply: dict, where: str) -> dict:
    result = reply.get("result") if isinstance(reply.get("result"), dict) else reply
    if reply.get("status") == "error" or result.get("success") is False or reply.get("success") is False:
        return {"ok": False, "message": "Unity 刷新失败：" + json.dumps(reply, ensure_ascii=False)[:300]}
    state = (result.get("data") or {}).get("resulting_state", "") if isinstance(result.get("data"), dict) else ""
    return {"ok": True, "message": f"Unity 已刷新资源（{where}{'，' + state if state else ''}）。"}


def refresh(project_root: str) -> dict:
    """让 Unity 刷新资源数据库（强制、同步导入），等它导完。返回 {ok, message}。"""
    for http_port in http_ports(project_root):
        done = refresh_http(project_root, http_port)
        if done is not None:
            return done
    status = read_status(project_root)
    deadline = time.time() + 60
    while status.get("reloading") and time.time() < deadline:
        time.sleep(2)
        status = read_status(project_root)
    port = int(status.get("unity_port") or DEFAULT_PORT)
    try:
        reply = call(port, "refresh_unity", {"mode": "force", "scope": "all", "wait_for_ready": True})
    except (OSError, ConnectionError, ValueError) as error:
        opened = "（找到了状态文件）" if status else "（没找到这个工程的状态文件）"
        tried = "、".join(str(item) for item in http_ports(project_root))
        return {"ok": False, "message": f"连不上 Unity：HTTP 端口 {tried} 没有 MCP 服务，"
                                        f"TCP 端口 {port}{opened}也连不上：{error}。Unity 可能没开或 MCP 没启动。"}
    return judge(reply, f"TCP 端口 {port}")
