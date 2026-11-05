"""直接调用大豆配好的大模型，流式返回。

和机器人一样默认用本机 Codex CLI（AI_PROVIDER=codex，见 codex_cli.py）：Codex 不分段吐字，答完一次性给出；
Codex 出错且配了 LLM_API_KEY 时退回智谱。
网页聊天里 Codex 在 .env 的 PROJECT_ROOT（Unity 工程检出）里跑，可以只读地搜代码、看 git 提交和改动，
也可以搜项目里 wiki/ 下的飞书知识库缓存（相关片段已经由 routers/chat.py 用 wiki_kb 检索好放进提示）；
回答里如果带出了 .env 里的密钥，返回前打码。AI_PROVIDER=glm 时直接用智谱 GLM（OpenAI 兼容的 /chat/completions）。

智谱的配置和机器人用同一套：
- Key、接口地址：echo_bot 读的 LLM_API_KEY / LLM_BASE_URL（Windows 上也会读用户环境变量）
- 模型：后台「设置 → 模型」里选的聊天模型（console.load_models），失败时按机器人的备用模型顺序重试
- 每次调用都记进 model_stats，后台「模型」表里的今天次数会算上网页聊天
"""
import json
import re
import urllib.error
import urllib.request
from typing import Iterator, List

from common import codex_cli
from common import wiki_kb

TIMEOUT = 120
BOX_TOKENS = re.compile(r"<\|(begin|end)_of_box\|>")


class LLMError(Exception):
    pass


def settings() -> dict:
    from console import server as console
    from bot import echo_bot
    codex = echo_bot.USE_CODEX
    return {"api_key": echo_bot.LLM_API_KEY, "base_url": echo_bot.LLM_BASE_URL,
            "model": codex_cli.label() if codex else console.load_models()["chat"],
            "glm_model": console.load_models()["chat"], "fallbacks": tuple(echo_bot.LLM_FALLBACK_MODELS),
            "codex": codex}


def enabled(config: dict) -> bool:
    return bool(config.get("codex") or config["api_key"])


def note(model: str, ok: bool, error: str = "", prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
    from bot import echo_bot
    from common import model_stats
    model_stats.note(model, ok, echo_bot.redact(error) if error else "", prompt_tokens=prompt_tokens,
                     completion_tokens=completion_tokens)


def stream_once(config: dict, model: str, messages: List[dict]) -> Iterator[str]:
    """调一次模型，逐段产出文字。连接或首包前出错抛 LLMError。"""
    payload = {"model": model, "messages": messages, "temperature": 0.5, "stream": True}
    if model.startswith(("glm-4.5", "glm-4.6")):
        payload["thinking"] = {"type": "disabled"}
    request = urllib.request.Request(
        config["base_url"].rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + config["api_key"]},
    )
    usage = {}
    produced = False
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            for raw in response:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                chunk = json.loads(data)
                if isinstance(chunk.get("usage"), dict):
                    usage = chunk["usage"]
                choices = chunk.get("choices") or [{}]
                text = BOX_TOKENS.sub("", (choices[0].get("delta") or {}).get("content") or "")
                if text:
                    produced = True
                    yield text
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as error:
        detail = error.read().decode("utf-8", errors="replace")[:200] if isinstance(error, urllib.error.HTTPError) else ""
        message = f"{type(error).__name__}: {error} {detail}".strip()
        note(model, False, message)
        raise LLMError(message, produced)
    if not produced:
        note(model, False, "返回空内容")
        raise LLMError(f"{model} 返回空内容", False)
    note(model, True, prompt_tokens=int(usage.get("prompt_tokens") or 0),
         completion_tokens=int(usage.get("completion_tokens") or 0))


SECRET_NAMES = re.compile(r"(SECRET|KEY|TOKEN|PASSWORD|COOKIE)", re.I)


def mask_secrets(text: str) -> str:
    """Codex 的只读沙箱不限制读，万一它把 .env 里的密钥读出来写进回答，这里换掉。"""
    from common.auth import read_env_file
    for name, value in read_env_file().items():
        if value and len(value) >= 6 and SECRET_NAMES.search(name) and value in text:
            text = text.replace(value, f"<{name} 已隐藏>")
    return text


def project() -> tuple:
    from common import settings as bot_settings
    values = bot_settings.read_env_file()
    return bot_settings.project_root(values), bot_settings.git_branches(values)


def ask_codex(model: str, messages: List[dict]) -> str:
    project_dir, branches = project()
    try:
        answer, usage = codex_cli.ask(messages, project_dir=project_dir, branches=branches, docs_dir=wiki_kb.WIKI_DIR)
    except (codex_cli.CodexError, codex_cli.CodexTimeout, OSError) as error:
        message = f"{type(error).__name__}: {error}"
        note(model, False, message)
        raise LLMError(message, False)
    note(model, True, prompt_tokens=int(usage.get("input_tokens") or 0),
         completion_tokens=int(usage.get("output_tokens") or 0))
    return mask_secrets(answer)


def stream_chat(messages: List[dict]) -> Iterator[dict]:
    """产出事件：{"model": 名字}（开始用哪个模型）、{"delta": 文字}。还没吐字时失败就换备用模型。"""
    config = settings()
    if not enabled(config):
        raise LLMError("没有可用的模型：Codex 没找到，也没有配置 LLM_API_KEY。", False)
    if config.get("codex"):
        try:
            answer = ask_codex(config["model"], messages)
        except LLMError:
            if not config["api_key"]:
                raise
        else:
            yield {"model": config["model"]}
            yield {"delta": answer}
            return
    glm = config.get("glm_model") or config["model"]
    candidates = [glm] + [m for m in config["fallbacks"] if m != glm]
    last = None
    for model in candidates:
        started = False
        try:
            for text in stream_once(config, model, messages):
                if not started:
                    started = True
                    yield {"model": model}
                yield {"delta": text}
            return
        except LLMError as error:
            if started or (len(error.args) > 1 and error.args[1]):
                raise  # 已经吐过字，不能悄悄换模型重来
            last = error
    raise last or LLMError("没有可用的模型。", False)
