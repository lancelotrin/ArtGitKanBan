"""自检复盘的 Codex 分析：Codex（Code 模式，工作目录是网页配的项目目录）只读地查代码，按整个工程核对每天的提交。

- 复盘前先把工程更新到 origin/main 最新：有本地改动先 stash，不在 main 就切回 main，再快进。
- 每个人都开一个新会话，带上共用的工程笔记：上下文不会越滚越大（每条命令都要带整个上下文，共用长会话很费 token）。
- 一个人复盘完，在他的会话里续一轮，把新查到的工程认识并进笔记，下一个人接着用。笔记存在 data/review_codex.json。
提示里不贴 diff，只给提交号，Codex 自己 git show / rg，需要多少看多少。
"""
import datetime
import json
import os
import re
import subprocess
import threading

from common import codex_cli
from common.paths import DATA_DIR

STATE_PATH = os.path.join(DATA_DIR, "review_codex.json")
MEMORY_MAX_CHARS = 3000
TURN_TIMEOUT = 900
GIT_TIMEOUT = 600
MAX_COMMITS = 20
NO_ISSUE = "没看出明显问题，修改合理。"
_lock = threading.Lock()

RULES = (
    "你是项目组每日「自检复盘」的代码评审员。当前工作目录是项目目录 {root}（Unity 工程，在 git 仓库里），已更新到 origin/main 最新。"
    "之后每一轮我会给你一个人某天的提交号列表，你在工程里只读地核对这些提交。\n"
    "怎么查：\n"
    "- 用 git show --stat <提交号> 看改了哪些文件，再 git show <提交号> -- <文件> 看需要的 diff；提交可能在别的分支，按提交号查就行。\n"
    "- 改动涉及的类、字段、属性、宏、keyword、配置键、数据源，用 rg 在整个工程里查其他引用和调用方，"
    "核对是否都同步改了、有没有漏改、改动后的数据流是否一致。\n"
    "- 省 token：每条命令的输出控制在 200 行、8000 字符以内（用 --stat、-- <文件>、rg -m、head、sed -n 截取）；"
    "不要整文件输出，看 rg 命中附近几十行即可；diff 很大时按文件分段看；"
    "prefab / asset / meta / unity 这类序列化文件不要整段看 diff，只 rg 相关字段或 guid；不要去读工程里的说明文档（Assets/MD~ 等）；"
    "这个会话里已经弄清楚的工程结构、模块位置直接用，不要重复查。\n"
    "- 只读：不要修改、新建、删除文件，不要 fetch / pull / checkout / reset / stash / commit 等任何 git 写操作，不要联网，不要构建或跑测试。\n"
    "输出（只输出下面的内容，不要开场白和思考过程）：\n"
    "**逐条要点**\n\n"
    "每个提交一段：\n"
    "**【短提交号】** 时间 提交说明\n"
    "改动：一句话说清改了什么（按 diff，不要复述提交说明）\n"
    "影响：影响哪些系统/资源/平台，依据是什么\n"
    "隐患：在工程里核实过的具体问题，写出文件:行号依据；没有具体问题写「无」\n\n"
    "最后：所有提交都没有具体问题时只写一行「" + NO_ISSUE + "」；"
    "否则写一节 **需要留意**，每条以 - 开头，写清问题、依据（文件:行号）和怎么查，末尾括号带【短提交号】，最多 5 条。\n"
    "不要写「可能需要测试」「确保按预期工作」「兼容性测试」这类空话，没在工程里核实的猜测不要写。"
)
MEMORY_PROMPT = (
    "这次复盘结束了，下一个人会开新会话，只带工程笔记。把开头给你的工程笔记（没有就从空白开始）和这次新查到的、"
    "对以后复盘有用的认识合并成一份完整的新笔记，不超过 1500 字：主要模块和目录在哪、关键数据流（例如哪个 World/Context 管什么）、"
    "常见的易漏改点、各人常改的区域。旧笔记里查实过时的改掉，没新东西就原样输出旧笔记。"
    "只写工程事实，不写具体某天的复盘结论，不要再执行命令。只输出笔记。"
)


def load_state() -> dict:
    try:
        with open(STATE_PATH, encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_PATH)


def _git(root: str, args: list) -> tuple:
    try:
        result = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=GIT_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, str(error)
    return result.returncode == 0, (result.stdout if result.returncode == 0 else result.stderr or result.stdout).strip()


def update_to_main(root: str) -> tuple:
    """工程更新到 origin/main 最新。返回 (是否成功, 说明)。有本地改动先 stash（不自动 pop）。"""
    notes = []
    ok, out = _git(root, ["fetch", "origin", "main"])
    if not ok:
        return False, f"fetch main 失败：{out[:200]}"
    ok, status = _git(root, ["status", "--porcelain"])
    if not ok:
        return False, f"git status 失败：{status[:200]}"
    if status:
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        ok, out = _git(root, ["stash", "push", "-u", "-m", f"大豆自检复盘前自动暂存 {stamp}"])
        if not ok:
            return False, f"stash 失败：{out[:200]}"
        notes.append(f"本地改动已 stash（{stamp}）")
    ok, branch = _git(root, ["rev-parse", "--abbrev-ref", "HEAD"])
    if branch != "main":
        ok, out = _git(root, ["checkout", "main"])
        if not ok:
            ok, out = _git(root, ["checkout", "-B", "main", "origin/main"])
        if not ok:
            return False, f"切到 main 失败：{out[:200]}"
        notes.append(f"从 {branch} 切到 main")
    ok, out = _git(root, ["merge", "--ff-only", "origin/main"])
    if not ok:
        return False, "；".join(notes + [f"main 快进失败（本地 main 有没推的提交？）：{out[:200]}"])
    _ok, head = _git(root, ["rev-parse", "--short=11", "HEAD"])
    notes.append(f"main 已到 {head}")
    return True, "；".join(notes)


def _note_usage(note, usage: dict, ok: bool = True, error: str = "") -> None:
    if note:
        note(codex_cli.label(), ok, error, int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0))


class _Session:
    """一个人的复盘会话：第一次开新会话，之后（格式重写、更新笔记）续用它。
    续用时 Codex 给的 usage 是整个会话的累计，这里换算成每一轮的再记统计；total 就是这个人一共用的。"""

    def __init__(self, root: str, note=None):
        self.root, self.note, self.thread_id, self.total = root, note, "", {}

    def ask(self, prompt: str) -> str:
        meta = {}
        answer, total = codex_cli.code(prompt, self.root, TURN_TIMEOUT, keep=True, resume_id=self.thread_id, meta=meta)
        _note_usage(self.note, {key: max(0, int(total.get(key) or 0) - int(self.total.get(key) or 0))
                                for key in ("input_tokens", "output_tokens")})
        self.thread_id = self.thread_id or meta.get("thread_id") or ""
        self.total = total
        return answer.strip()


def _first_prompt(state: dict, root: str) -> str:
    parts = [RULES.format(root=root)]
    if state.get("memory"):
        parts.append("【之前复盘积累的工程笔记】（可能有过时的地方，以工程为准）\n" + state["memory"])
    return "\n\n".join(parts)


def valid_report(answer: str, shorts: list) -> bool:
    if not answer or ("需要留意" not in answer and "没看出明显问题" not in answer):
        return False
    return any(f"【{short}" in answer for short in shorts)


def review(root: str, name: str, author: str, day_text: str, commits: list, head_note: str, log, note=None) -> str:
    """commits: [(短号, 分支, "短号|作者|时间|说明")]。返回逐条要点 + 结论的 markdown；Codex 不可用或格式不对返回空串。"""
    if not codex_cli.available() or not commits:
        return ""
    commits = commits[:MAX_COMMITS]
    listing = "\n".join(f"{line}（{branch}）" for _short, branch, line in commits)
    task = (f"复盘 {name}（git 作者 {author}）{day_text} 的 {len(commits)} 条提交。工程状态：{head_note}。\n"
            f"提交（短号|作者|时间|说明（分支））：\n{listing}\n按规则输出。")
    shorts = [short for short, _branch, _line in commits]
    with _lock:  # 笔记是大家共用的，一个人跑完、笔记更新完再跑下一个
        state = load_state()
        session = _Session(root, note)
        try:
            answer = session.ask(_first_prompt(state, root) + "\n\n【本轮任务】\n" + task)
            if not valid_report(answer, shorts):
                log(f"[review codex] {author} 格式不对，要求重写")
                answer = session.ask("上面的输出格式不对。按规则重新输出这几条提交的逐条要点和结论，只输出规定内容。")
        except (codex_cli.CodexError, codex_cli.CodexTimeout, OSError) as error:
            _note_usage(note, {}, False, str(error)[:200])
            log(f"[review codex] {author} 失败 {type(error).__name__}: {str(error)[:160]}")
            return ""
        review_usage = dict(session.total)
        memory_note = "笔记没更新"
        try:
            memory = session.ask(MEMORY_PROMPT)
            if memory:
                for old in ("thread_id", "turns", "started_at", "fails", "session_usage", "last_input_tokens"):
                    state.pop(old, None)  # 以前共用长会话时的字段
                state.update(memory=memory[:MEMORY_MAX_CHARS], root=root,
                             updated_at=datetime.datetime.now().isoformat(timespec="seconds"))
                save_state(state)
                memory_note = f"笔记 {len(state['memory'])} 字"
        except (codex_cli.CodexError, codex_cli.CodexTimeout, OSError) as error:
            log(f"[review codex] {author} 更新笔记失败，沿用旧笔记 {type(error).__name__}: {str(error)[:120]}")
    log(f"[review codex] {author} commits={len(commits)} input={review_usage.get('input_tokens')} "
        f"cached={review_usage.get('cached_input_tokens')} output={review_usage.get('output_tokens')} "
        f"含笔记 input={session.total.get('input_tokens')} {memory_note}")
    if not valid_report(answer, shorts):
        return ""
    return re.sub(r"\n{3,}", "\n\n", answer).strip()
