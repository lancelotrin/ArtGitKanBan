"""跑在提问人自己的电脑上。只改 --root 里面的文本文件，不碰大豆那台机器的工程。

准备：安装 lark-cli，并用你自己的飞书账号登录（lark-cli auth login）。
运行：python local_agent.py --root 你的工程目录
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys

import local_edit

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
EVENT_TYPE = "im.message.receive_v1"
WORK_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_agent_work")


def find_lark_cli() -> list:
    shim = shutil.which("lark-cli")
    if not shim:
        sys.exit("找不到 lark-cli。先安装并登录你自己的飞书账号。")
    run_js = os.path.join(os.path.dirname(shim), "node_modules", "@larksuite", "cli", "scripts", "run.js")
    if os.path.isfile(run_js):
        return ["node", run_js]
    return [shim]


def run_cli(args: list) -> str:
    result = subprocess.run(
        find_lark_cli() + args,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60, creationflags=NO_WINDOW,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stdout or "")[-300:] + (result.stderr or "")[-300:])
    return result.stdout


def log(text: str) -> None:
    print(text, flush=True)


def reply_file(message_id: str, name: str, content: str) -> None:
    os.makedirs(WORK_DIR, exist_ok=True)
    rel = name.replace("\\", "/")
    full = os.path.join(WORK_DIR, name)
    with open(full, "w", encoding="utf-8", newline="\n") as file:
        file.write(content)
    run_cli(["im", "+messages-reply", "--as", "user", "--message-id", message_id, "--file", rel])


def reply_text(message_id: str, content: str) -> None:
    run_cli(["im", "+messages-reply", "--as", "user", "--message-id", message_id, "--text", content])


def download_file(message_id: str, file_key: str) -> str:
    os.makedirs(WORK_DIR, exist_ok=True)
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", file_key)[:80] or "file"
    rel = safe + ".bin"
    run_cli(["im", "+messages-resources-download", "--as", "user", "--message-id", message_id,
             "--file-key", file_key, "--type", "file", "--output", rel])
    folder = WORK_DIR
    matches = [os.path.join(folder, name) for name in os.listdir(folder) if name.startswith(safe)]
    if not matches:
        raise RuntimeError("下载后找不到文件")
    matches.sort(key=os.path.getmtime, reverse=True)
    return matches[0]


def payload_from_message(message: dict, message_id: str) -> str:
    content = message.get("content") or ""
    if isinstance(content, str) and content:
        try:
            content = json.loads(content)
        except ValueError:
            content = {"text": content}
    if not isinstance(content, dict):
        content = {}
    text = str(content.get("text") or "")
    if text.startswith("DADOU/1") or text.startswith("DADOU_AGENT"):
        return text
    file_key = str(content.get("file_key") or "")
    if not file_key:
        return ""
    path = download_file(message_id, file_key)
    try:
        data = open(path, encoding="utf-8").read()
    except (OSError, UnicodeError):
        return ""
    if data.startswith("DADOU/1"):
        return data
    return ""


def handle_job(root: str, message_id: str, text: str) -> None:
    fields, body = local_edit.decode_message(text)
    op = fields.get("op") or ""
    job_id = fields.get("id") or ""
    path = fields.get("path") or ""
    if op == "read":
        try:
            content = local_edit.read_under(root, path)
            reply = local_edit.encode_message(
                {"op": "file", "id": job_id, "path": path, "ok": "1", "root": root}, content)
        except ValueError as error:
            reply = local_edit.encode_message(
                {"op": "error", "id": job_id, "path": path, "ok": "0", "root": root}, str(error))
        reply_file(message_id, f"dadou-file-{job_id or 'x'}.txt", reply)
        log(f"[read] {path}")
        return
    if op == "write":
        try:
            local_edit.write_under(root, path, body)
            reply = local_edit.encode_message(
                {"op": "wrote", "id": job_id, "path": path, "ok": "1", "root": root})
            log(f"[wrote] {path}")
        except ValueError as error:
            reply = local_edit.encode_message(
                {"op": "error", "id": job_id, "path": path, "ok": "0", "root": root}, str(error))
            log(f"[write failed] {path} {error}")
        reply_text(message_id, reply)
        return
    log(f"[skip] op={op or '-'}")


def main() -> None:
    parser = argparse.ArgumentParser(description="大豆本地程序：只改你自己电脑上的工程")
    parser.add_argument("--root", required=True, help="你的工程目录，文件路径相对这个目录")
    args = parser.parse_args()
    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        sys.exit(f"工程目录不存在：{root}")
    os.makedirs(WORK_DIR, exist_ok=True)
    os.chdir(WORK_DIR)
    log(f"[agent] root={root}")
    log("[agent] 等大豆发任务。这台程序只改上面这个目录。")
    command = find_lark_cli() + ["event", "+subscribe", "--as", "user", "--event-types", EVENT_TYPE]
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
        errors="replace", creationflags=NO_WINDOW,
    )
    assert process.stdout is not None
    for line in process.stdout:
        line = line.strip()
        if not line:
            continue
        if not line.startswith("{"):
            log(line[:300])
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        body = event.get("event", event)
        sender = body.get("sender") or {}
        if sender.get("sender_type") != "app":
            continue
        message = body.get("message") or {}
        message_id = str(message.get("message_id") or "")
        if not message_id:
            continue
        try:
            text = payload_from_message(message, message_id)
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            log(f"[recv failed] {error}")
            continue
        if not text.startswith("DADOU/1"):
            continue
        try:
            handle_job(root, message_id, text)
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            log(f"[job failed] {error}")


if __name__ == "__main__":
    main()
