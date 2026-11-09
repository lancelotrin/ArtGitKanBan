"""用本机的 Codex CLI 回答（参考 FeishuKonwledgeBot/src/codex.mjs）。

每次回答跑一次 `codex exec`：聊天消息压成一段提示从 stdin 传进去，始终是只读沙箱。
不给项目目录时在一个空的临时目录里跑，并告诉它资料都在提示里、不要执行命令；
给了项目目录（网页聊天）时在项目目录里跑，可以只读地搜代码、看 git 记录和改动。
code() 是 Code 模式：自检复盘、炸线判断用，直接在网页配的项目目录里跑，提示原样给。答案从 --json 事件流里取，turn.completed 一到就返回，不等进程慢慢退出。
默认跳过 ~/.codex/config.toml（MCP、插件）和规则文件，每次快好几秒；CODEX_USE_USER_CONFIG=true 时才加载。

配置（系统环境变量 → 项目根目录 .env）：
  CODEX_PATH              留空 = 在 PATH 里找 codex
  CODEX_MODEL             例如 gpt-6.1-sol；留空用 Codex 自己的默认
  CODEX_REASONING_EFFORT  low / medium / high；留空用默认
  CODEX_CONCURRENCY       同时最多几个 Codex 进程，默认 2
  AI_TIMEOUT_SECONDS      单次回答超时，默认 300
"""
import json
import os
import shutil
import subprocess
import threading
import uuid

from common.auth import env_value, read_env_file
from common.paths import DATA_DIR

WORK_DIR = os.path.join(DATA_DIR, "_codex_work")
MAX_ANSWER_CHARS = 16000

INTRO = ("你是飞书机器人的回答引擎。下面已经提供了回答所需的全部资料：不要执行任何命令，不要读取或修改文件，不要联网，"
         "直接输出给用户看的最终回答（不要输出思考过程或额外说明）。你当前所在的工作目录只是运行用的临时空目录，"
         "与任何项目无关，不要把它当作项目路径或资料来源。")


def project_intro(project_dir: str, branches: list, docs_dir: str = "") -> str:
    branch_text = "、".join(branches) if branches else "main"
    docs_text = (f"团队飞书知识库的本地缓存在 {docs_dir}（每个 json 一篇文档，字段 title / path / url / text）。"
                 "下面已经附上和问题最相关的文档片段；片段不够时可以用 rg 在这个目录里搜关键词再看全文，"
                 "引用文档时写出文档路径和 url。\n") if docs_dir else ""
    return ("你是飞书机器人「大豆」的回答引擎，当前工作目录就是项目组的 Unity 工程检出：\n"
            f"- {project_dir}\n"
            f"常用分支：{branch_text}。\n" + docs_text +
            "问题涉及代码、资源或 Git 改动时，可以只读地查看这个目录：用 rg 搜索、查看文件内容，"
            "用 git log / git show / git diff / git blame / git status / git branch -a 查看提交和改动"
            "（看远端分支用 origin/<分支名>，不要 fetch）。"
            "只能读取这个工程目录和上面的知识库目录，不要读取其他文件；不要修改、新建或删除任何文件，不要运行构建、测试、"
            "git 写操作（commit、checkout、reset、pull、fetch、merge 等）或其他会改变状态的命令，不要联网。"
            "查看要克制，够回答即可。最后直接输出给用户看的最终回答（不要输出思考过程或额外说明），"
            "引用代码时写出相对工程目录的文件路径，说到提交时带上短哈希、作者和日期。")


class CodexError(ValueError):
    """Codex 没给出回答。继承 ValueError，机器人里原有的大模型兜底都能接住。"""


class CodexTimeout(TimeoutError):
    pass


def setting(name: str, default: str = "") -> str:
    return env_value(name, read_env_file()) or default


def int_setting(name: str, default: int, low: int, high: int) -> int:
    try:
        return min(high, max(low, int(setting(name, str(default)))))
    except ValueError:
        return default


def codex_path() -> str:
    configured = setting("CODEX_PATH")
    if configured and (not os.path.isabs(configured) or os.path.isfile(configured)):
        return configured
    return shutil.which("codex") or configured or "codex"


def available() -> bool:
    path = codex_path()
    return bool(shutil.which(path) or os.path.isfile(path))


def model_name() -> str:
    return setting("CODEX_MODEL")


def label() -> str:
    model = model_name()
    return f"codex {model}" if model else "codex"


_limit_lock = threading.Lock()
_limiter = {"size": 0, "sem": None}


def limiter() -> threading.Semaphore:
    size = int_setting("CODEX_CONCURRENCY", 2, 1, 8)
    with _limit_lock:
        if _limiter["size"] != size:
            _limiter.update(size=size, sem=threading.BoundedSemaphore(size))
        return _limiter["sem"]


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # 看图消息：[{type: text/image_url}, ...]，图片另外用 -i 传
        return "\n".join(part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text")
    return str(content or "")


def messages_to_prompt(messages: list, intro: str = INTRO) -> str:
    system = [text_of(m.get("content")) for m in messages if m.get("role") == "system"]
    turns = [m for m in messages if m.get("role") != "system"]
    earlier = [f"{'助手' if m.get('role') == 'assistant' else '用户'}：{text_of(m.get('content'))}" for m in turns[:-1]]
    parts = [intro]
    if system:
        parts.append("【要求与资料】\n" + "\n\n".join(system))
    if earlier:
        parts.append("【最近的对话】\n" + "\n".join(earlier))
    if turns:
        parts.append("【当前消息】（最重要，回答它）\n" + text_of(turns[-1].get("content")))
    return "\n\n".join(parts)


def codex_args(out_file: str, images: list, work_dir: str = WORK_DIR, keep: bool = False, resume_id: str = "") -> list:
    """keep=True 时会话落盘可续用；resume_id 给了就续上那个会话（沿用它当初的工作目录，沙箱仍是只读）。"""
    if resume_id:
        args = [codex_path(), "exec", "resume", "--skip-git-repo-check", "--json", "-o", out_file,
                "-c", 'sandbox_mode="read-only"']
    else:
        args = [codex_path(), "exec", "--skip-git-repo-check", "--sandbox", "read-only", "--color", "never",
                "--json", "-C", work_dir, "-o", out_file]
        if not keep:
            args.append("--ephemeral")
    if setting("CODEX_USE_USER_CONFIG").lower() not in ("1", "true", "yes"):
        args += ["--ignore-user-config", "--ignore-rules"]
    if model_name():
        args += ["-m", model_name()]
    effort = setting("CODEX_REASONING_EFFORT")
    if effort:
        args += ["-c", f'model_reasoning_effort="{effort}"']
    for image in images:
        args += ["-i", image]
    if resume_id:
        args.append(resume_id)
    args.append("-")
    return args


def run_turn(prompt: str, images: list, timeout: float, work_dir: str = WORK_DIR, keep: bool = False,
             resume_id: str = "", meta: dict = None) -> tuple:
    """跑一轮，返回 (回答, usage)。回答文件总是写在自己的临时目录，不进项目目录。
    meta 给了就把会话 id 写进 meta["thread_id"]，下次用 resume_id 续上。"""
    os.makedirs(WORK_DIR, exist_ok=True)
    out_file = os.path.join(WORK_DIR, f"answer-{uuid.uuid4().hex}.txt")
    try:
        proc = subprocess.Popen(codex_args(out_file, images, work_dir, keep, resume_id), cwd=work_dir, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError as error:
        raise CodexError(f"无法启动 Codex（{codex_path()}）：{error}") from error
    timed_out = threading.Event()

    def kill_on_timeout() -> None:
        timed_out.set()
        proc.kill()

    timer = threading.Timer(timeout, kill_on_timeout)
    timer.daemon = True
    timer.start()
    answer, failure, usage = "", "", {}
    try:
        try:
            proc.stdin.write(prompt.encode("utf-8"))
            proc.stdin.close()
        except OSError:
            pass  # 进程可能还没读完就退出了，下面按退出处理
        for raw in proc.stdout:
            try:
                event = json.loads(raw.decode("utf-8", errors="replace"))
            except ValueError:
                continue
            kind = event.get("type")
            item = event.get("item") or {}
            if kind == "thread.started" and meta is not None:
                meta["thread_id"] = event.get("thread_id") or ""
            elif kind == "item.completed" and item.get("type") == "agent_message" and item.get("text"):
                answer = item["text"]
            elif kind in ("error", "turn.failed"):
                failure = event.get("message") or (event.get("error") or {}).get("message") or failure
            elif kind == "turn.completed" and answer:
                usage = event.get("usage") or {}
                break
        if timed_out.is_set():
            raise CodexTimeout(f"Codex 回答超时（{int(timeout)} 秒）")
        if not answer:  # 老版本 CLI 不在事件流里给，退回 -o 文件
            proc.wait(timeout=5)
            try:
                with open(out_file, encoding="utf-8") as file:
                    answer = file.read().strip()
            except OSError:
                pass
        if not answer:
            detail = f"：{str(failure)[:200]}" if failure else ""
            raise CodexError(f"Codex 没有返回回答（退出码 {proc.poll()}{detail}），在终端运行 codex 检查登录状态和额度")
        return answer, usage
    finally:
        timer.cancel()
        if proc.poll() is None:
            proc.kill()
        try:
            proc.stdout.close()
        except OSError:
            pass
        try:
            os.remove(out_file)
        except OSError:
            pass


def code(prompt: str, project_dir: str, timeout: float = 0, images: list = (), keep: bool = False,
         resume_id: str = "", meta: dict = None) -> tuple:
    """Code 模式（像 Codex 桌面端的 Code）：以项目目录为工作区跑，提示原样给，不套聊天机器人的开场和对话格式。
    自检复盘、炸线判断用这个；沙箱仍是只读。返回 (回答, usage)。"""
    if not project_dir or not os.path.isdir(project_dir):
        raise CodexError(f"项目目录不存在：{project_dir}")
    timeout = timeout or int_setting("AI_TIMEOUT_SECONDS", 300, 1, 900)
    with limiter():
        answer, usage = run_turn(prompt, list(images), timeout, project_dir, keep, resume_id, meta)
    answer = answer.strip()
    if not answer:
        raise CodexError("Codex 返回了空回答")
    return answer, usage


def ask(messages: list, images: list = (), timeout: float = 0, project_dir: str = "", branches: list = (),
        docs_dir: str = "") -> tuple:
    """返回 (回答, usage)。usage 是 Codex 的 input_tokens / output_tokens 等。
    project_dir 存在时在里面跑，允许只读地查代码和 git；否则在空目录里跑，只用提示里的资料。"""
    os.makedirs(WORK_DIR, exist_ok=True)
    timeout = timeout or int_setting("AI_TIMEOUT_SECONDS", 300, 1, 900)
    if project_dir and os.path.isdir(project_dir):
        work_dir, intro = project_dir, project_intro(project_dir, list(branches),
                                                     docs_dir if docs_dir and os.path.isdir(docs_dir) else "")
    else:
        work_dir, intro = WORK_DIR, INTRO
    with limiter():
        answer, usage = run_turn(messages_to_prompt(messages, intro), list(images), timeout, work_dir)
    answer = answer.strip()
    if not answer:
        raise CodexError("Codex 返回了空回答")
    if len(answer) > MAX_ANSWER_CHARS:
        answer = answer[:MAX_ANSWER_CHARS] + "\n[回答过长，已截断]"
    return answer, usage


if __name__ == "__main__":
    import sys
    question = " ".join(sys.argv[1:]) or "只回答：1+1 等于几"
    print(label(), "available" if available() else "NOT FOUND")
    print(ask([{"role": "user", "content": question}]))
