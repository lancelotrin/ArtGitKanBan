"""大豆的运行设置：全部来自 .env（系统环境变量优先），不再用 bot_config.json，网页上也不再改。

后台（console/server.py）、新后端、机器人（echo_bot.py）都从这里读，每次现读，改了 .env 重启大豆即可生效。

  PROJECT_ROOT       Unity 工程的 Client 目录（下面要有 Assets），就是部署这台电脑上的检出
  GIT_BRANCHES       允许查询的 Git 分支，逗号或空格分隔；main 总会带上
  LLM_MODEL          聊天回答用的模型
  LLM_VISION_MODEL   看图用的模型
"""
import os
import re

from common.auth import env_value, read_env_file

DEFAULT_PROJECT_ROOT = r"D:\Fork\onepiece_engine\Client"
DEFAULT_BRANCHES = ("main", "CBT2_bugfix")
BRANCH_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,60}$")
MAX_BRANCHES = 8
# 只放智谱免费的 flash 模型，第一个是默认。
MODEL_CHOICES = {
    "chat": ("glm-4.5-flash", "glm-4-flash-250414", "glm-4-flash"),
    "vision": ("glm-4.6v-flash", "glm-4v-flash"),
}
MODEL_ENV = {"chat": "LLM_MODEL", "vision": "LLM_VISION_MODEL"}


def env(name: str, values: dict = None) -> str:
    return env_value(name, read_env_file() if values is None else values)


def project_root(values: dict = None) -> str:
    """FEISHU_BOT_PROJECT_ROOT 是以前的名字，还认。"""
    values = read_env_file() if values is None else values
    return env("PROJECT_ROOT", values) or env("FEISHU_BOT_PROJECT_ROOT", values) or DEFAULT_PROJECT_ROOT


def git_branches(values: dict = None) -> list:
    raw = env("GIT_BRANCHES", values)
    names = [name for name in re.split(r"[\s,，]+", raw) if name] if raw else list(DEFAULT_BRANCHES)
    clean = []
    for name in names:
        if BRANCH_NAME.match(name) and name not in clean:
            clean.append(name)
    if "main" not in clean:
        clean.insert(0, "main")
    return clean[:MAX_BRANCHES]


def models(values: dict = None) -> dict:
    """.env 里填的模型；没填就用默认的 flash 模型。填了列表外的名字也照用，大豆按名字调接口。"""
    values = read_env_file() if values is None else values
    return {kind: env(MODEL_ENV[kind], values) or choices[0] for kind, choices in MODEL_CHOICES.items()}


def load() -> dict:
    values = read_env_file()
    return {"project_root": project_root(values), "git_branches": git_branches(values), "models": models(values)}
