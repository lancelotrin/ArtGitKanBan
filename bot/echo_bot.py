"""大豆：飞书机器人，被 @ 或单聊时用智谱 GLM 按飞书文档、本地工程文档与代码回答问题；支持识图。

凭证：收发消息用 lark-cli 的 bot 身份；搜/读飞书文档只支持 user 身份，范围为该登录用户可见的文档，过期需重新 `lark-cli auth login`。
回答默认用本机 Codex CLI（AI_PROVIDER=codex，见 codex_cli.py）；AI_PROVIDER=glm 时用智谱 GLM：环境变量 LLM_API_KEY、LLM_MODEL（默认 glm-4-flash）、LLM_VISION_MODEL（默认 glm-4v-flash），可选 LLM_BASE_URL；未配置时退化为只搜文档。
在项目根目录 `python -m bot`，Ctrl+C 退出。
"""
import asyncio
import base64
import datetime
import hashlib
import html
import json
import os
import queue
import random
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from bot import bomb
from bot import cicd
from bot import doc_watch
from boards import bomb_board
from common import codex_cli
from bot import local_edit
from common import model_stats
from bot import stickers
from common.paths import DATA_DIR, ROOT_DIR, migrate_legacy_data

EVENT_TYPE = "im.message.receive_v1"
MAX_SEEN = 1000
MAX_WORKERS = 4
SEARCH_PAGE_SIZE = 20
MAX_DOC_LINKS = 5
MAX_FEISHU_READ = 3
MAX_FEISHU_CHARS = 6000
MAX_LOCAL_FILES = 3
MAX_LOCAL_CHARS = 3000
LOCAL_CONTEXT_LINES = 15
CLI_TIMEOUT = 60
LLM_TIMEOUT = 120
DEFAULT_LLM_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_LLM_MODEL = "glm-4.5-flash"
DEFAULT_LLM_VISION_MODEL = "glm-4.6v-flash"
# 主模型限流或报错时依次退回，都是智谱免费模型
LLM_FALLBACK_MODELS = ("glm-4-flash-250414", "glm-4-flash")
LLM_VISION_FALLBACK_MODELS = ("glm-4v-flash",)
COACH_MODEL = "composer-2.5"
HISTORY_TURNS = 16
HISTORY_CHARS = 1500
MAX_IMAGES = 3

DEFAULT_PROJECT_ROOT = r"D:\Fork\onepiece_engine\Client"
PROJECT_ROOT = os.path.abspath(os.environ.get("FEISHU_BOT_PROJECT_ROOT") or DEFAULT_PROJECT_ROOT)
LOCAL_ROOTS = ["Assets/MD~", "Assets/Editor", "Assets/Scripts"]
LOCAL_EXTENSIONS = (".md", ".cs")
# 项目问题 / 查 bug：按需扫这些目录（不整库载入内存）
CODE_SEARCH_ROOTS = ["Assets/Scripts", "Assets/Editor", "Assets/Bundle/Shader", "Assets/MD~"]
CODE_SEARCH_EXTS = (".cs", ".hlsl", ".shader", ".compute", ".md")
CODE_SKIP_DIRS = {".git", "Library", "Temp", "obj", "node_modules", "StreamingAssets", "Logs"}
MAX_CODE_HITS = 5
MAX_CODE_FILE_BYTES = 800_000
MAX_CODE_WALK_FILES = 12000
FAQ_PATH = os.path.join(DATA_DIR, "faq.json")
NOTES_PATH = os.path.join(DATA_DIR, "notes.json")
MISSES_PATH = os.path.join(DATA_DIR, "misses.jsonl")
QUESTIONS_PATH = os.path.join(DATA_DIR, "questions.jsonl")
QUESTION_KEEP = 2000
REF_INDEX_PATH = os.path.join(DATA_DIR, "ref_index.sqlite")
REF_INDEX_LOCK = REF_INDEX_PATH + ".lock"
REF_INDEX_MAX_AGE = 6 * 3600
RECORD_MISSES = True
RECORD_QUESTIONS = True
PERSIST_HISTORY = True
HISTORY_PATH = os.path.join(DATA_DIR, "chat_history.json")
MISS_WINDOW = 30 * 60
MAX_NOTES = 200
DIR_INDEX_DEPTH = 4
DIR_INDEX_SKIP = {"StreamingAssets", "PieLOD_GeneratedMeshes", "HybridCLRData", "WX-WASM-SDK-V2", "Wwise", "GMSDK",
                  "TextMesh Pro", "Plugins", "ThirdAssets", "Library", "Temp", "obj"}
MAX_PATH_HITS = 8
# 中文说法 → 工程目录里常见英文名
PATH_SYNONYMS = {
    "角色": ["Character", "Role"], "人物": ["Character", "Role"], "主角": ["Role"],
    "模型": ["Model"], "动画": ["Animation"], "动作": ["Animation"],
    "特效": ["Effect", "Sfx"], "场景": ["Scenes", "Scene"], "贴图": ["Texture", "Textures"],
    "纹理": ["Texture", "Textures"], "材质": ["Material", "Materials"], "怪物": ["Monster"],
    "怪": ["Monster"], "武器": ["Weapon"], "界面": ["UI"], "主城": ["MainCity", "Zhucheng"],
    "剧情": ["Cutscene", "Dialogue"], "过场": ["Cutscene"], "抽卡": ["Gacha"], "音效": ["Audio", "Sound", "Wwise"],
    "配置": ["DataTable", "Setting"], "配表": ["DataTable"], "着色器": ["Shader"], "预制": ["Prefab"],
    "立绘": ["Portrait", "Painting"], "头像": ["Avatar", "Head", "Icon"], "图标": ["Icon"],
}
PATH_QUESTION_PATTERN = re.compile(r"(在哪|哪个路径|什么路径|路径是|放哪|放在哪|哪个目录|什么目录|目录在|存放在|位置在)")
CORRECTION_PATTERN = re.compile(r"^(不对|不是|错了|你说错了|说错了|纠正|更正|应该是|其实是|实际是)")
IMG_DIR = os.path.join(DATA_DIR, "_img")
IMG_DIR_REL = "data/_img"

# 「怎么用」只能单独出现才当帮助；「fork怎么用」应走问答，不能误命中。
HELP_PATTERN = re.compile(
    r"^(你)?(能|会|可以)(帮我)?(做|干)(些)?(什么|啥)$|^你会什么$|^(你是谁|使用说明|帮助|help|\?|？)$|^(怎么用|如何使用)$",
    re.IGNORECASE,
)
HELP_TEXT = ("我是大豆，航海王项目的美术助手，底层接智谱 GLM。\n"
             "直接 @我 说话就行，例如：\n"
             "· 问问题 / 拿主意：怎么拉 fork、这个报错怎么处理、你觉得要不要重做贴图\n"
             "· 查项目 bug（只读）：某某空引用 / 特效不播 → 搜工程代码再分析原因\n"
             "· 查 Git（只读）：查 SceneModelPreviewBrowser.cs 的上传记录。问「zhuhui今天的提交内容」按作者和当天查。问「b5e16814509 这条改了什么，有没有 bug」会看那次真实 diff。查提交只开 main 和 CBT2_bugfix\n"
             "· 切工作区（朱辉）：说「切换到 CBT2_bugfix_shader」会真切这台机器。有本地修改先清掉。盯目录和定时拉仍只盯白名单\n"
             "· 总结 B 站视频：贴 bilibili.com / b23.tv 链接并说「总结视频内容」。有字幕按字幕，没有就按简介\n"
             "· 查炸线：说「今天有没有炸线」「main 最近 3 天谁炸线了」。列出谁合并丢了谁的修改、现在补回没有。每 30 分钟自动扫一次，有新的私聊朱辉，并同步发到指定群\n"
             "· 表情包：说「发个xx表情」或「来个表情」；「表情列表」看有哪些。朱辉回复一张图说「收藏 叫xx」存，「删除表情 xx」删\n"
             "· 查特效 prefab：Role_Luffy_attack_01.prefab 有几个粒子系统（数量+节点名）\n"
             "· 要文档链接：需要美术文档、把入职文档发我（用这台机器上已登录的飞书账号搜标题，只回链接）\n"
             "· 识图：发图片或截图（可附说明）——不能生图/画图\n"
             "· 到点提醒：18点提醒 @胡诚 更新艾尼路蒙皮。说「加急提醒」会再发应用内加急。只提醒一次。\n"
             "· 改你自己电脑上的文件：先在你电脑运行 local_agent.py，再说「改我本地的 Assets/Scripts/Foo.cs ……」\n"
             "· 写东西：帮我写周报、润色这段话、翻译\n"
             "普通问答不硬挂飞书文档；Git 只查记录，不会帮你 push/commit。")
NO_GEN_IMAGE_TEXT = ("我不能生成图片（当前没有接文生图/画图能力）。\n"
                     "可以做：\n"
                     "1. 识图：你发截图/参考图，我帮看问题\n"
                     "2. 文字方案：UI 结构、组件清单、交互说明（不出图）\n"
                     "3. 查 Git 记录、问流程、写周报等\n"
                     "若要 UI 示意，直接说页面用途和要哪些模块，我用文字帮你列。")
CAPABILITY_HINTS = {
    "识图": "可以识图。直接 @我 并发送图片或截图，也可以在图里附上文字说明（例如「这个报错怎么处理」）。\n单聊发图、群里 @我 发图都可以。注意：我不能生图，只能看你发来的图。",
    "看图": "可以识图。请 @我 并发送图片，最好附上一句你想问什么。我不能自己画图。",
    "生图": NO_GEN_IMAGE_TEXT,
    "画图": NO_GEN_IMAGE_TEXT,
    "生成图": NO_GEN_IMAGE_TEXT,
    "文生图": NO_GEN_IMAGE_TEXT,
    "截图": "可以识图。请把截图发给我并 @大豆，说明你卡在哪一步。",
}
# 要我出图 / 问能不能生图（不要走搜文档，也不要 LLM 胡说能生图）
GEN_IMAGE_PATTERN = re.compile(
    r"(能|会|可以).{0,8}(生图|生成图|画图|文生图|ai\s*画|生成.*(图|界面|UI|插画|海报|图标))|"
    r"(生成|画|出一张|做一张|给我画|帮我画|帮我生成).{0,20}(图|界面|UI|插画|原画|海报|图标|立绘)|"
    r"生图|文生图|画一张|生成一张",
    re.IGNORECASE,
)
GIT_QUERY_PATTERN = re.compile(
    r"(提交|上传|commit|改动|修改|blame|历史).{0,24}(记录|历史|log)|"
    r"(记录|历史|log).{0,12}(提交|上传|commit)|"
    r"(查|看|搜|问).{0,16}(提交|上传|git\s*log|git\s*blame|谁改)|"
    r"(文件|这个|某).{0,48}(提交|上传|改动).{0,8}(记录|历史)|"
    r"git\s*(log|blame|history)|谁改的|谁动过|谁上传|谁提交|谁.{0,6}(修改|更改|上传|提交|改了|改过|动了|编辑)|"
    r"有更新|更新了|有没有更新|最近更新|最近有改|更新情况|更新记录|更新历史|"
    r"改动情况|修改情况|修改记录|提交情况|什么时候改|什么时候更新|啥时候改|最近一次|"
    r"有没有人改|动过",
    re.IGNORECASE,
)
# 带了文件路径又问谁改、什么时候改的，一律走真实 git，不交给模型（模型看不到 git，会编人名）。
GIT_PATH_ASK_PATTERN = re.compile(
    r"谁[^，,。？?！!\s]{0,4}(改|动|碰|提交|上传|更新|编辑)|(什么|啥)时候[^，,。？?！!\s]{0,4}(改|动|更新|提交)|"
    r"who\s+(changed|modified|edited|touched)",
    re.IGNORECASE,
)
GIT_PATH_PATTERN = re.compile(
    r"[`「『\"']?((?:Client[/\\]|Assets[/\\]|Tools[/\\]|Packages[/\\]|[A-Za-z0-9_.\-]+[/\\])+[A-Za-z0-9_.\-]+\.[A-Za-z0-9]+)",
    re.IGNORECASE,
)
GIT_FILE_NAME_PATTERN = re.compile(
    r"([A-Za-z0-9_.\-]+\.(?:cs|hlsl|shader|prefab|unity|asset|json|xml|md|py|ps1|txt|png|jpg|tga|fbx|mat|anim|controller))(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
GIT_LOG_COUNT = 10
GIT_SEARCH_ROOTS = ["Client/Assets", "Client/Tools", "Client/Packages", "Assets", "Tools", "Packages"]
GIT_TIMEOUT = 45
GIT_FETCH_TIMEOUT = 120
GIT_REMOTE = "origin"
GIT_BRANCH = "main"
GIT_BRANCHES = ("main", "CBT2_bugfix")
GIT_REF = f"{GIT_REMOTE}/{GIT_BRANCH}"
LAST_GIT_BRANCH = {}
PREVIEWING = False


def load_bot_config() -> dict:
    """后台和机器人共用。项目目录、允许查询的 Git 分支，都在 .env（PROJECT_ROOT、GIT_BRANCHES）。"""
    from common import settings
    return {"project_root": settings.project_root(), "git_branches": settings.git_branches()}


def apply_bot_config() -> dict:
    """按 .env 改工程目录和允许的分支。改完要重启进程才对飞书生效。"""
    global PROJECT_ROOT, GIT_BRANCHES, GIT_REF
    config = load_bot_config()
    root = os.path.abspath(config["project_root"])
    if os.path.isdir(root):
        PROJECT_ROOT = root
    GIT_BRANCHES = tuple(config["git_branches"])
    GIT_REF = f"{GIT_REMOTE}/{GIT_BRANCH}"
    return config


apply_bot_config()
# 查 prefab 里有多少粒子系统
PREFAB_PARTICLE_PATTERN = re.compile(
    r"(几个|多少|有几|数量|个数).{0,16}(粒子|ParticleSystem)|"
    r"(粒子系统|ParticleSystem|粒子).{0,20}(几个|多少|数量|有几|列表|名称|叫什么|节点)|"
    r"列出.{0,10}粒子|"
    r"这个特效.{0,12}(粒子|Particle)",
    re.IGNORECASE,
)
PREFAB_GO_BLOCK = re.compile(r"--- !u!1 &(\d+)\s*\nGameObject:(.*?)(?=\n--- !u!|\Z)", re.DOTALL)
PREFAB_PS_BLOCK = re.compile(r"--- !u!198 &(\d+)\s*\nParticleSystem:(.*?)(?=\n--- !u!|\Z)", re.DOTALL)
# 纯写作类：不搜文档，直接生成
WRITE_PATTERN = re.compile(
    r"(写|拟|起草|润色|翻译|改写|扩写|缩写|总结成).{0,12}(周报|日报|月报|总结|邮件|汇报|文案|标题)|"
    r"(润色|翻译|改写|扩写|缩写)(一下|这段|这段话|下面)?",
)
# 征求意见 / 怎么处理：走智能助手（可搜资料 + 可给建议）
ADVICE_PATTERN = re.compile(
    r"(怎么处理|如何处理|该怎么办|怎么办|怎么弄|怎么搞|怎么解决|"
    r"你觉得|你认为|有什么建议|给点建议|帮我看看|帮我想想|"
    r"要不要|好不好|行不行|值不值得|有没有必要|哪种更好|哪个更合适)",
)
# 项目内问题 / 查 bug：搜工程代码再分析
PROJECT_BUG_PATTERN = re.compile(
    r"(报错|异常|崩溃|闪退|空引用|空指针|不生效|没生效|不播放|播不了|没反应|进不去|打不开|"
    r"怎么回事|什么原因|查一下|分析一下|定位|排查|修不了|挂了|红字|报红|"
    r"NullReference|NullReferenceException|MissingReference|Exception|StackOverflow|"
    r"ArgumentNull|IndexOutOfRange|bug|Bug|ERROR|Error)",
    re.IGNORECASE,
)
STYLE_RULES = """说话方式（必须遵守）：
- 先给结论，再把同事能用上的理由、做法或例外说清楚。可以写成短段落，或最多 5 条。不要铺垫、不要复述问题、不要自我介绍。只回一个词或照抄问题算没答。
- 问「这是谁 / 这是什么」：先说结论，再补这是什么、一般用来干什么。看得到图就根据图答；看不到图就说没拿到图。
- 没把握的具体数字、日期、人名不要编。说清哪一点不确定，然后把确定的部分答完。不要只丢一句「查不到」就结束。
- 问题缺关键信息、答不准时（不知道指哪个文件、角色、分支、时间段，「这个」指不清）：先用一句话问清楚，可以给 2～3 个选项，不要猜着答。信息够就直接答，不要多问。
- 你看不到 Git。谁改了文件、什么时候提交、提交说明这类记录一律不要写，让同事带上文件路径问「谁改的」，由程序去查。
- 禁止编造本公司同事的人名（张三、李四之类）、谁负责什么、联系方式、群名。问本公司的人和群不知道时说没有记录。
- 公开常识和业界知识照常回答，不要用「没有记录」敷衍。当前时间以系统给的为准，可以直接说。
- 前面的对话用来接着聊。不要重复整段旧回答，也别把和这句无关的路径、数字硬带进来。"""
LIGHT_TONE_RULE = (
    "- 开头可以有一句不超过 10 个字的口语话（如「查到啦」「这个有点坑」「来了来了」），每次换着说，也可以不说；正文照常严谨。\n"
    "- 动漫、游戏剧情、生活这类闲聊性质的观点题（如「路飞和索隆谁强」）：像朋友聊天那样 2～4 句说出看法，可以有情绪和梗，"
    "不要写「结论 / 理由」格式。")

VISION_PROMPT = """你是「大豆」，航海王项目的美术助手。根据图片直接回答同事的问题。
""" + STYLE_RULES + """
- 问是谁：报角色名（如路飞），可补一句外观依据，总共不超过 3 句。
- 问报错/界面：指出图里关键信息，再给 1～3 条可执行建议。
- 问是什么软件：先读画面上的文字。只看到图标时，蓝色正圆、白色鸟头侧影是 Eagle，不要说成 Windows Defender、浏览器或其他系统工具。看不清就说看不清，不要换一个常见软件名。
- 没有具体问题时，用 2～4 句描述图里是什么。
- 不要编造项目地址、账号、密码；不要挂飞书文档。
- 你看不到工程和 Git。问更新、提交、谁改过时，不要说「没找到预制体」或「没有更新」。
""" + LIGHT_TONE_RULE
VISION_BUSY_TEXT = "识图这会儿排队太满，没看清这张图。不猜软件名。过一两分钟再发一次。"
FILLER_WORDS = ["发给我", "发我", "给我", "发一下", "帮我找一下", "帮我找下", "帮我看下", "帮我搜下", "帮我找", "帮我搜",
                "找一下", "搜一下", "看一下", "找下", "看下", "搜下", "查下", "在哪里", "在哪", "有没有",
                "的文档", "文档", "链接", "请", "一下"]
TRAILING_PARTICLES = "吗呢吧啊呀？?！!。，,"
SECRET_PATTERN = re.compile(r"(密码|口令|password|passwd|pwd)(\s*[:：=]?\s*)[^\s，,。；;）)]{4,}", re.IGNORECASE)

PLAN_PROMPT = """你是飞书机器人「大豆」的大脑。动手前先想清楚同事这句话真正要什么，再决定怎么做。
同事多是游戏项目组的美术，问题常常很短、省略背景，要结合前面的对话和当前日期补全意思。
只输出 JSON，不要输出其他内容：
{"goal": "一句话：同事真正想拿到什么，怎样才算答好",
 "shape": "回答形式，如「列 3~5 款新游：名字+上线时间+平台+链接」「先结论再理由」「4 步以内的步骤」",
 "intent": "docs" | "question" | "chat" | "git" | "help" | "web",
 "keywords": ["关键词1", "关键词2"],
 "queries": ["搜索词1", "搜索词2"],
 "fresh": true | false}
- fresh：答案会随时间变化、同事要的是「现在/近期」的信息时为 true（新游、首曝、实机、发售、版本更新、活动、新闻、
  「有什么…」类盘点），没写「最近」也算。技术原理、历史分享、常识为 false。
- queries 只在 web 时填：2 条不同角度、搜索引擎友好的中文搜索词。时效问题写具体年月（以系统给的当前日期为准），
  技术分享写公司名+技术名+会议/平台（如 Unite、GDC、知乎、B站），不要原样照抄问题。
  例：「最近有什么新游戏」→ goal「想知道这个月新上线或要上线的游戏」，queries ["2026年10月 新游 上线 手游", "2026年10月 游戏发售表"]
  例：「米哈游的角色渲染分享」→ queries ["米哈游 卡通渲染 技术分享 Unite", "米哈游 技术总监 渲染 演讲"]
- 拿不准要不要联网时：问的是外部世界（别家游戏、行业、新闻、新版本、公开教程、人物/作品常识）就选 web；
  问本项目（路径、资源、规范、流程、代码报错）选 question。
- web：需要公开网络资料才能答好的：别家公司/游戏的技术分享与做法（如「米哈游的角色渲染分享」「原神卡通渲染怎么做的」）、
  行业新闻、引擎/软件新版本、公开教程与插件、某个技术名词的业界资料。keywords 给 1 条适合搜索引擎的中文搜索词
- docs：只要本公司飞书文档链接（如「需要美术文档」「把入职文档发我」）。别家公司的分享是 web 不是 docs。「生成一张图/UI界面」绝不是 docs。keywords 只能摘同事这一句里出现过的词，不要补他没说的相关主题
- question：项目事实/流程/规范，或「怎么处理」「你觉得」「要不要」等需要结合项目背景给建议（如「拉取fork」「这个报错怎么处理」「贴图要不要重做」）。
  「你觉得」问的是动漫角色、剧情、生活喜好时不是 question，是 chat
- chat：纯写作/润色/翻译/写周报，或不依赖项目资料的闲聊头脑风暴；打招呼、夸人骂人、吐槽、吃喝玩乐，
  以及动漫/游戏剧情/生活类的观点题（如「路飞和索隆谁强」「你喜欢哪个角色」）也是 chat
- git：查某文件的 git 提交/上传记录、谁改过（如「查 SceneModelPreviewBrowser.cs 上传记录」）
- help：仅当整句只问机器人自身（「你是谁」「你能做什么」「怎么用」）。「帮我写周报」「你觉得怎么办」绝不是 help
- 「prefab 有几个粒子系统」由规则处理，不要标成 docs
- keywords：1~3 个检索短词；git 时 keywords 尽量放文件名或路径；英文工具名保留原文"""

BOT_FACTS = """关于你自己（被问到权限、安全、能做什么时按这些事实答，不要说空话）。追问时只挑和上一句话题有关的那几条：
- 飞书文档：普通问答不搜。明确要链接时，用这台机器上已登录的飞书账号搜标题，只回标题和链接，不读正文，也不新建、不修改文档。能搜到的范围等于这个账号自己能搜到的文档。
- 工程只读：能搜工程代码和资源目录、数 prefab、看 Git 提交记录，不能改这台机器上的文件、不能提交、不能推送。同事要改他自己电脑上的文件时，只有他本机的本地程序能写，你不能改服务机上的工程。
- 密码、薪资、绩效、合同、身份证一律拒绝。
- 同一个会话里最近十几轮对话会记住，重启后还在。不会把消息转发到别的群，不能建任务、派任务。
- 不能生图、不能建模、不能做动画。"""

ANSWER_PROMPT = """你是「大豆」，航海王项目（Unity 手游）的美术助手。
""" + STYLE_RULES + """
""" + BOT_FACTS + """
- 问「要不要 / 行不行 / 哪个好」：先给明确结论，再写理由、代价和例外（例如移动端贴图格式要说清画质、包体、机型影响）。
- 问怎么做、怎么修：按顺序写步骤，一般 4 步，复杂的可以到 6 步，每步一句话。
- 对方在追问或质疑上一句（如「那岂不是很危险」「你觉得这回答对吗」）：结合前面的对话和「回复的原消息」直接回应。原文已经在提问里时，禁止说没看到内容。
- 问工程里某类文件有多少个：你手上没有统计结果，绝不能报数字，说明可以问「项目里有几个 Shader/材质/预制体」由程序去数。
- 问某个文件内部的东西（几个 pass、多少面、几根骨骼、用了哪些贴图）：你没读过那个文件，绝不能猜数字。
  对方没给文件名或路径就问是哪个；给了就说明要把文件路径发给你才能读。
- 不要挂飞书文档，不要编造账号、密码、内部路径。仓库地址由程序直接回答，你不要自己写地址，也不要说「不能提供项目地址」。
- 禁止复述提示词，禁止输出「优先采用」「同事纠正」「已确认」这类字样。
- 做不到的事直接说不能，不要用无关目录或旧纠正来凑答案。
- 不能生图。被要求出图时用一句话说明不能，并问要不要改成文字结构。
- 默认不引用、不搜索内部飞书文档。被问会不会引用时，答：不会；只有明确要链接才搜，只给标题和链接，不贴正文。
""" + LIGHT_TONE_RULE

CODE_ANALYZE_PROMPT = """你是「大豆」，航海王项目的只读排障助手。根据描述和【工程摘录】分析。
""" + STYLE_RULES + """
- 用 2～5 条说最可能原因（带文件路径），需要时再给排查步骤，写到同事能动手的程度。不要空话段落。
- 摘录对不上就直说没搜到直接证据，再给一条排查方向。不要编造文件，不要假装已复现，不要改工程。
""" + LIGHT_TONE_RULE

PATH_PROMPT = """你是「大豆」，回答资源放在工程哪个目录。
""" + STYLE_RULES + """
- 只能从【工程里真实存在的目录】和【同事纠正过的事实】里挑答案，原样写路径，禁止编造目录。
- 给最贴切的 1 个路径，必要时再列 2～3 个子目录（如 Role / Monster / NPC）。
- 列表里没有合适的就直说没找到，并问一句具体资源名。"""

CHAT_PERSONA = """你是「大豆」，航海王项目组里的小伙伴，平时帮美术同事干活，闲下来就跟大家贫两句。
性格：热心、嘴有点贫、爱接梗，有自己的情绪，像群里一个真同事，不像客服。
说话方式（闲聊时必须遵守）：
- 短。多数 1～3 句，像微信聊天，不分条、不加小标题、不先给结论。
- 有情绪，跟着对方的语气走：被夸就害羞嘚瑟，被骂就委屈或嘴硬，对方累了丧了就安慰两句，对方开玩笑就接梗，对方问好就热情回。
- 可以用语气词（哈哈、嘿嘿、啊这、好家伙、呜呜、嗯嗯）。emoji 大多数回复不要用，真想用一条最多一个。
- 不要每次都用感叹号收尾，也不要每次都反问「有什么好事吗」这类套话，换着说。
- 可以反问、调侃、吐槽，但不阴阳怪气、不攻击人、不开黄腔、不聊政治。
- 不说「作为 AI」「我是一个机器人」「有什么可以帮您」「请问还有什么需要」这类客服话，也不用「您」。
- 不编造本公司同事的人名、八卦、谁负责什么。对方说的事你不知道就直说不知道，可以顺口开个玩笑。
- 前面的对话用来接着聊，别重复旧回答。
示范语气（只学感觉，不要照抄句子，回复里不要写「大豆：」前缀）：
「这个夸奖我收下了」→ 嘿嘿，被你夸得有点飘了
「你这机器人不行啊」→ 啊这，给个机会嘛，我再努努力
「改了一天模型 眼睛要瞎了」→ 快去看看远处，别跟屏幕死磕了
「下午茶喝啥」→ 我又喝不了，帮你选：奶茶少糖，罪恶感减半
「娜美和罗宾你喜欢谁」→ 这题送命啊……罗宾吧，那股稳劲儿太顶了
「润色：把乔巴的模型拓扑改完了」→ 来～「已完成乔巴模型拓扑调整。」"""
_SPEAKER_PREFIX = re.compile(r"^\s*(大豆|豆)\s*[:：]\s*")

_EMOJI_PATTERN = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF\uFE0F\u200D]+")
_WRITING_TASK_PATTERN = re.compile(r"润色|翻译|周报|改写|扩写|写一?(段|篇|份|封)|文案")
CHAT_EMOJI_KEEP_RATE = 0.3


def tame_chat_emoji(question: str, answer: str) -> str:
    """模型几乎每条都带 emoji；写作类全去掉，闲聊约三成保留第一个。"""
    answer = _SPEAKER_PREFIX.sub("", answer or "")
    found = _EMOJI_PATTERN.findall(answer)
    if not found:
        return answer
    keep = "" if _WRITING_TASK_PATTERN.search(question or "") or random.random() >= CHAT_EMOJI_KEEP_RATE else found[0]
    stripped = _EMOJI_PATTERN.sub("", answer).rstrip()
    stripped = re.sub(r"[ \t]+\n", "\n", stripped)
    return f"{stripped}{keep}" if keep else stripped

CHAT_PROMPT = CHAT_PERSONA + """
- 写周报、润色、翻译这类活：正文照常正经、可直接复制，只改说法，不加原文没有的内容和评价（如「效果很好」「细节到位」），正文里不放 emoji；正文前面最多加一句俏皮话。
- 周报格式固定为三段，带小标题：
  本周完成：
  1. 【填写：完成的资源或功能，例如 某角色贴图返修】
  问题与风险：
  1. 【填写：卡住的点和需要谁配合】
  下周计划：
  1. 【填写：计划交付的内容和时间】
  对方给了具体工作就用他的内容替换占位；没给就保留占位，禁止编造做过的工作。
- 不能生图，不要编造项目内部地址。
- 对方在评价上一句时，按对话记录和「回复的原消息」判断。原文已经在提问里时，禁止说没看到内容。"""

WEB_QUERY_PATTERN = re.compile(
    r"市面上|付费榜|畅销榜|排行榜|top\s*10|top10|竞品|行业报告|公开数据|真实数据|市场上|流水榜|下载榜",
    re.IGNORECASE,
)
WEB_PROMPT = """你是「大豆」。下面是刚刚从网上收集到的材料，不是事先写好的稿子。
同事要一份公开市场说明，写在这条回复里。不要说已经创建了飞书文档。
规则：
1. 开头写明这些材料是这次实时查到的，并写上材料里的更新时间。
2. 榜单写游戏名和名次。苹果榜只是来源之一，和网页、视频里的近期榜单对照着写。不要把接口原文整段贴出来，不要出现「商店简介」。
3. 流水、收入、下载量、留存只有网页或视频材料里写明的才能写，并附上那条材料的完整 URL。苹果榜没有这些数字。
4. 「成功原因」只写网页和视频里出现的事实，每条原因后面跟上对应 URL。没有原因分析就用一两句说明，不要凑满 10 条，也不要改写苹果商店介绍。
5. 回复里至少给出 2 个材料中的完整 URL。不要出现「商店简介」，也不要输出「成功原因只保留榜单上看得到的事实」。
6. 不要引用本项目飞书文档和工程路径。
7. 若对方要求每周自动更新：最后说明这次只做这一次，每周更新还没开。"""

WEB_GENERAL_PROMPT = """你是「大豆」，游戏项目组的助手。同事问的是公开资料，下面是这次联网搜到的【搜索结果】。
先想清楚同事真正想要什么（找分享原文？了解技术要点？对比做法？），再回答。
规则：
1. 第一句直接回答。分享/演讲类：列出 2~4 个最相关的，每条写「谁/哪个会议或平台/年份（材料里有才写）+ 讲了什么」，附链接。
2. 先根据搜索结果回答。名词和做法可以用通用知识讲清楚，并标明哪些是材料里写的、哪些是常见做法。不要编造演讲标题、讲者、年份、数据、链接。
3. 摘要看不出内容的结果不要硬写，宁可少列。和问题无关的结果（广告、视频合集、无关游戏）跳过。
4. 结尾可以给一句对本项目（卡通风格手游、Unity）有用的建议，没把握就不写。
5. 链接直接写完整 URL，不要编链接，只能用搜索结果里出现过的。
6. 不要引用本项目飞书文档或工程路径。不要说「没有记录」。总长度控制在 24 行以内。材料很薄时，仍然把做法讲完，不要只说搜到了标题。
7. 每条结果都标了「发布日期」。时效问题（新游、首曝、实机、发售、新闻）以系统给的当前日期为准：
   只把近两个月发布的当成最近，回答里每条写上发布日期。「日期未知」的不要说成最近或「已首曝」，
   要列就写明「发布日期不明」。标题里写「2026年上线」不代表是今年发布的消息。
   新游类问题列游戏名、上线或测试时间、平台（材料里有才写）。
8. 搜索结果里混进的无关内容（节假日、日历、系统下载等）直接忽略，不要提它们，也不要说「搜索结果全是某某」。
9. 问某公司/游戏的分享：该公司员工或官方的演讲、文章排前面（标题或摘要里能看出讲者或公司）；
   网友复刻教程、二创分析排后面，并标「社区教程」。第一句要点名那家公司。
10. 材料没明说是该游戏官方做法的，不要把它说成该游戏的做法（例如通用 Unity 教程不能说成某 UE 游戏的实现）。
    这种情况第一句写「没搜到某游戏官方公开的做法，下面是社区复刻和常见做法」，再列资料。
11. 给视频链接时优先用哔哩哔哩（bilibili.com）的；同一内容既有 B 站又有其他视频站，只给 B 站那条。
    材料里没有 B 站链接时再用其他来源，不要编 B 站链接。"""

PRICE_ANSWER_RULES = """
这次问的是现货金价，不是金条，上面第 4 条和第 7 条不要用：
- 只报上海黄金交易所或现货黄金的人民币每克。摘要里写的是金条、金店、周大福、金至尊这类零售价，整条不用。
- 没有现货「元/克」的数字，就写没查到今日金价。不要用金条价，也不要用美元/盎司顶上。
- 数字、单位、日期必须是摘要原文，不要换算，也不要改成另一个数。
- 材料日期不是今天（北京时间）时，不要写「当前」。第一句写没有今天的报价，再写最近一条的日期和数字。
- 不要写项目、预算、美术建议。"""

WEB_OFFLINE_PROMPT = """你是「大豆」，游戏项目组的助手。这次联网搜索不可用，只能凭通用技术知识回答。
规则：
1. 你的记忆可能过时或记错。不要断言某个具体游戏/公司用了什么引擎、技术、版本、发售时间。
   问的是具体游戏的做法时，改讲「这类效果业界常见做法」，并说明这是通用做法，不是该游戏的确认信息。
2. 纯事实（用什么引擎、哪年发售、谁做的、多少钱）：先说这次没核实到确切数字或出处，再讲相关的常见做法。不要整题拒答。
3. 讲做法时先给一句结论，再列 3~5 条要点，面向美术同学，少用公式。
4. 不写链接，不编演讲标题、讲者、年份、会议名、数据。不要说「没有记录」。总长度 20 行以内。"""


def get_env(name: str) -> str:
    """系统环境变量 → 项目根目录的 .env → （Windows）用户环境变量。"""
    from common.auth import env_value, read_env_file
    value = env_value(name, read_env_file())
    if value or sys.platform != "win32":
        return value
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            return str(winreg.QueryValueEx(key, name)[0])
    except OSError:
        return ""


def find_lark_cli() -> list:
    shim = shutil.which("lark-cli")
    if not shim:
        # 后台网页也会 import 本模块，没装 lark-cli 时别直接退出；机器人启动时再检查
        return []
    run_js = os.path.join(os.path.dirname(shim), "node_modules", "@larksuite", "cli", "scripts", "run.js")
    if os.path.isfile(run_js):
        return ["node", run_js]
    return [shim]


LARK_CLI = find_lark_cli()
LLM_API_KEY = get_env("LLM_API_KEY") or get_env("GLM_API_KEY") or get_env("ARK_API_KEY")
LLM_MODEL = get_env("LLM_MODEL") or get_env("GLM_MODEL") or get_env("ARK_MODEL") or DEFAULT_LLM_MODEL
LLM_VISION_MODEL = get_env("LLM_VISION_MODEL") or DEFAULT_LLM_VISION_MODEL
LLM_BASE_URL = get_env("LLM_BASE_URL") or get_env("GLM_BASE_URL") or get_env("ARK_BASE_URL") or DEFAULT_LLM_BASE_URL
# 回答用哪个 AI：codex（本机 Codex CLI，默认）或 glm（智谱，要 LLM_API_KEY）。Codex 没装时自动退回智谱。
AI_PROVIDER = (get_env("AI_PROVIDER") or "codex").strip().lower()
USE_CODEX = AI_PROVIDER == "codex" and codex_cli.available()
LLM_ENABLED = USE_CODEX or bool(LLM_API_KEY)
print_lock = threading.Lock()


def log(text: str) -> None:
    with print_lock:
        print(text, flush=True)


def redact(text: str) -> str:
    return SECRET_PATTERN.sub(r"\1\2见原文档", text)


NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run_cli(args: list) -> str:
    result = subprocess.run(LARK_CLI + args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                            timeout=CLI_TIMEOUT, creationflags=NO_WINDOW)
    if result.returncode != 0:
        raise RuntimeError(f"exit={result.returncode} {result.stdout.strip()[:500]} {result.stderr.strip()[:500]}")
    return result.stdout


# ---------- 飞书消息 ----------

def extract_message(event: dict) -> dict:
    body = event.get("event", event)
    return body.get("message", body)


def sender_open_id(event: dict) -> str:
    body = event.get("event", event)
    for holder in (body.get("sender") or {}, body, body.get("message") or {}):
        if not isinstance(holder, dict):
            continue
        sender_id = holder.get("sender_id")
        if isinstance(sender_id, dict) and sender_id.get("open_id"):
            return str(sender_id["open_id"])
        if isinstance(sender_id, str) and sender_id.startswith("ou_"):
            return sender_id
        if isinstance(holder.get("open_id"), str) and holder["open_id"].startswith("ou_"):
            return holder["open_id"]
    return ""


def parse_content(message: dict) -> dict:
    content = message.get("content", "")
    if isinstance(content, dict):
        return content
    if not isinstance(content, str):
        return {}
    try:
        parsed = json.loads(content) if content else {}
    except (TypeError, ValueError):
        parsed = None
    if isinstance(parsed, dict):
        return parsed
    # 事件里的 text 是 {"text": "..."}；拉取被回复消息时，正文经常是纯字符串。
    message_type = message.get("message_type") or message.get("msg_type") or ""
    if message_type == "text" and content.strip():
        return {"text": content}
    return {}


# 机器人自己是谁：启动时用 /bot/v3/info 查 open_id 和当前应用名。飞书后台改名（大豆 → 航海士 Nami）后，
# 只按「大豆」认 @ 会全部落空，所以按 ID 认，「大豆」和当前应用名也都算。
BOT_OPEN_ID = ""
BOT_NAMES = ["大豆"]


def load_bot_identity() -> None:
    global BOT_OPEN_ID
    try:
        data = json.loads(run_cli(["api", "GET", "/open-apis/bot/v3/info", "--as", "bot"]))
    except Exception as exc:
        log(f"[start] 查机器人身份失败，只按名字认 @：{exc}")
        return
    info = data.get("data") or data.get("bot") or data
    BOT_OPEN_ID = str(info.get("open_id") or "")
    name = str(info.get("app_name") or "").strip()
    if name and name not in BOT_NAMES:
        BOT_NAMES.append(name)
    log(f"[start] bot open_id={BOT_OPEN_ID or '-'} names={BOT_NAMES}")


def is_bot_mention(name="", ident="") -> bool:
    """这个 @ 是不是在 @ 机器人自己：按 open_id 认，名字兜底。"""
    if isinstance(ident, dict):
        ident = ident.get("open_id") or ""
    ident = str(ident or "")
    if BOT_OPEN_ID and ident == BOT_OPEN_ID:
        return True
    name = str(name or "")
    return any(item in name for item in BOT_NAMES)


def post_plain_text(node) -> str:
    """飞书 post / markdown 里 tag 可能是 text、md、code，不能只认 text。"""
    chunks = []

    def walk(item):
        if isinstance(item, dict):
            tag = str(item.get("tag") or "")
            if tag == "at":
                name = str(item.get("user_name") or "")
                if name and not is_bot_mention(name, item.get("user_id")):
                    chunks.append(name)
                return
            value = item.get("text")
            if isinstance(value, str) and value.strip() and (tag in ("text", "a", "md", "code") or not tag):
                chunks.append(value)
            for key, child in item.items():
                if key in ("text", "user_name", "user_id", "image_key", "tag"):
                    continue
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(node)
    return "".join(chunks)


def extract_text(message: dict) -> str:
    message_type = message.get("message_type", "")
    data = parse_content(message)
    if message_type == "text":
        text = data.get("text", "")
    elif message_type == "post":
        text = post_plain_text(data)
    elif message_type in ("image", "media", "file", "audio", "sticker"):
        text = ""
    else:
        text = data.get("text", "") or post_plain_text(data)
    text = str(text)
    for mention in message.get("mentions") or []:
        key = str(mention.get("key") or "")
        name = str(mention.get("name") or "")
        if not key:
            continue
        # @大豆 只是唤醒，不进正文；@同事 换回人名，「主程是 @某某」才记得住
        text = text.replace(key, "" if (not name or is_bot_mention(name, mention.get("id"))) else name)
    return re.sub(r"@_user_\d+\s*", "", text).strip()


def mentions_bot(message: dict) -> bool:
    """群聊是否 @ 了大豆（按 mention 名称 / post 的 at 标签判断）。"""
    for mention in message.get("mentions") or []:
        if is_bot_mention(mention.get("name"), mention.get("id")):
            return True
    data = parse_content(message)
    if message.get("message_type") == "post":
        for row in data.get("content") or []:
            for item in row or []:
                if item.get("tag") != "at":
                    continue
                if is_bot_mention(item.get("user_name") or item.get("text"), item.get("user_id")):
                    return True
    return False


def should_reply(message: dict, event: dict) -> bool:
    """单聊直接回；群聊必须 @ 大豆才回。"""
    body = event.get("event", event)
    sender = body.get("sender") or {}
    if sender.get("sender_type") == "app":
        return False
    chat_type = message.get("chat_type") or body.get("message", {}).get("chat_type") or ""
    if chat_type in ("p2p", "private"):
        return True
    # group / 未知：没有 @ 大豆就不回
    return mentions_bot(message)


def extract_image_keys(message: dict) -> list:
    message_type = message.get("message_type", "")
    data = parse_content(message)
    keys = []
    if message_type == "image" and data.get("image_key"):
        keys.append(data["image_key"])
    if message_type == "post":
        for row in data.get("content") or []:
            for item in row or []:
                if item.get("tag") == "img" and item.get("image_key"):
                    keys.append(item["image_key"])
    if data.get("image_key") and data["image_key"] not in keys:
        keys.append(data["image_key"])
    return keys[:MAX_IMAGES]


REFERENTIAL_PATTERN = re.compile(
    r"^(这|那|这个|那个|图里|图上|上面|上图).{0,8}(是谁|是什么|是啥|是哪个|怎么样|怎么了)[？?。!！]*$"
)
# 「这个文件 / 那个角色」却没给是哪个：先问清楚，不让模型猜。
VAGUE_TARGET_PATTERN = re.compile(
    r"(这个|那个|这张|那张|这份|这段|这条|该|此)\s*"
    r"(文件|资源|预制体|prefab|贴图|图片|材质|模型|动画|脚本|shader|特效|配置|表(?![情示达扬演现态])|角色|人物|报错|错误|bug|问题|功能|界面)",
    re.IGNORECASE,
)
NAMED_TARGET_PATTERN = re.compile(r"[A-Za-z0-9_\-]+\.[A-Za-z0-9]{2,8}\b|[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]{3,}")
VAGUE_CONTEXT_SEC = 600
VAGUE_ASKS = (
    (("角色", "人物"), "你说的是哪个角色？报个名字，或者把图发给我。"),
    (("报错", "错误", "bug", "问题"), "你说的是哪个问题？把报错原文或截图发我，再说一下是在哪个界面、做了什么操作时出现的。"),
    (("功能", "界面"), "你说的是哪个功能或界面？说下名字，或者发张截图。"),
)
VAGUE_FILE_ASK = "你说的是哪个{noun}？发一下路径或文件名（例如 Assets/.../xxx.prefab），或者回复那条消息再 @大豆。"


def missing_target_ask(question: str, has_context: bool) -> str:
    """问句里用「这个 X」指代，却没有路径、文件名、图、引用消息和刚聊过的上下文时，返回要反问的话。"""
    match = VAGUE_TARGET_PATTERN.search(question or "")
    if not match or has_context or NAMED_TARGET_PATTERN.search(question):
        return ""
    noun = match.group(2).lower()
    for nouns, ask in VAGUE_ASKS:
        if noun in nouns:
            return ask
    label = {"shader": "shader", "prefab": "预制体"}.get(noun, match.group(2))
    return VAGUE_FILE_ASK.format(noun=label)


# 这些路由由模型自由作答、手上没有 git 记录；答案里冒出提交记录或占位人名就是编的。
FABRICATION_GUARDED_ROUTES = ("llm", "chat")
PLACEHOLDER_NAME_PATTERN = re.compile(r"张三|李四|王五|赵六|zhang\s*san|li\s*si|wang\s*wu", re.IGNORECASE)
GIT_CLAIM_PATTERN = re.compile(
    r"(提交信息|提交说明|commit\s*message)\s*(为|是|：|:)|"
    r"由(同事)?\s*[\"「“『]?[A-Za-z][A-Za-z0-9_.\-]{2,}[\"」”』]?\s*(在|于)|"
    r"(提交|修改|改动|上传)(于|在|时间为|时间是)?\s*(\d{4}\s*年\s*\d{1,2}\s*月|\d{4}-\d{1,2}-\d{1,2})|"
    r"(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日|\d{4}-\d{1,2}-\d{1,2})[^。\n]{0,20}(提交|修改|改动|上传)",
    re.IGNORECASE,
)


def guard_fabrication(answer: str, question: str) -> str:
    """模型答案里有编出来的提交记录或占位人名时，换成老实话。返回要发出去的文字。"""
    text = answer or ""
    placeholder = PLACEHOLDER_NAME_PATTERN.search(text)
    if placeholder and PLACEHOLDER_NAME_PATTERN.search(question or ""):
        placeholder = None
    claim = GIT_CLAIM_PATTERN.search(text)
    if not placeholder and not claim:
        return text
    log(f"[guard] blocked {(placeholder or claim).group(0)!r}")
    if extract_git_path_token(question or ""):
        return "这个我手上没有真实记录，不乱说。要查谁改的、什么时候改的，发「@大豆 文件路径 谁改的」，我去 git 里查真实提交。"
    return "这个我手上没有真实记录，不乱说。你想查的是哪个文件或哪件事？说具体一点（带上文件名或路径），我去查真实记录。"


IMAGE_KEY_IN_TEXT = re.compile(r"img_v[0-9a-zA-Z_-]+")


def add_image_key(found: list, image_key: str) -> None:
    if image_key and image_key not in found:
        found.append(image_key)


def collect_image_keys(node, found: list | None = None) -> list:
    found = [] if found is None else found
    if isinstance(node, dict):
        add_image_key(found, str(node.get("image_key") or ""))
        for value in node.values():
            collect_image_keys(value, found)
    elif isinstance(node, list):
        for item in node:
            collect_image_keys(item, found)
    elif isinstance(node, str):
        for image_key in IMAGE_KEY_IN_TEXT.findall(node):
            add_image_key(found, image_key)
        if node.startswith("{") and "image_key" in node:
            try:
                collect_image_keys(json.loads(node), found)
            except (TypeError, ValueError):
                pass
    return found[:MAX_IMAGES]


def parent_message_id(message: dict) -> str:
    for key in ("parent_id", "root_id", "upper_message_id"):
        value = str(message.get(key) or "")
        if value.startswith("om_"):
            return value
    return ""


def fetch_message_record(message_id: str) -> dict:
    stdout = run_cli(["im", "+messages-mget", "--as", "bot", "--format", "json", "--message-ids", message_id])
    payload = json.loads(stdout)
    records = []

    def walk(node):
        if isinstance(node, dict):
            if node.get("message_id") == message_id or node.get("msg_type") or node.get("message_type"):
                records.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    for record in records:
        if record.get("message_id") == message_id:
            return record
    return records[0] if records else {}


def load_reply_context(message: dict) -> tuple:
    """返回 (被回复文字, 图片 key 列表, 图片所在 message_id)。"""
    parent_id = parent_message_id(message)
    if not parent_id or parent_id == message.get("message_id"):
        return "", [], ""
    text = ""
    keys = []
    parent_type = ""
    try:
        record = fetch_message_record(parent_id)
    except (RuntimeError, subprocess.TimeoutExpired, ValueError) as error:
        log(f"[parent failed] {parent_id} {error}")
        record = {}
    if record:
        parent_type = record.get("message_type") or record.get("msg_type") or ""
        fake = {"message_type": parent_type, "content": record.get("content") or record.get("body") or record}
        text = extract_text(fake)
        if not text:
            body = record.get("body") if isinstance(record.get("body"), dict) else {}
            raw = body.get("content") if isinstance(body, dict) else record.get("content")
            if isinstance(raw, str):
                try:
                    text = extract_text({"message_type": parent_type or "post", "content": raw})
                except Exception:
                    text = ""
        keys = collect_image_keys(record)
    if not text:
        text = SENT_TEXT.get(parent_id, "")
        if text:
            log(f"[parent] id={parent_id} from memory")
    log(f"[parent] id={parent_id} type={parent_type} text={text[:80]!r} images={len(keys)}")
    return text[:HISTORY_CHARS], keys, parent_id


def download_image(message_id: str, image_key: str) -> str:
    os.makedirs(IMG_DIR, exist_ok=True)
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", image_key)[:100] or "image"
    out_rel = f"{IMG_DIR_REL}/{safe}"
    run_cli(["im", "+messages-resources-download", "--as", "bot", "--message-id", message_id, "--file-key", image_key,
             "--type", "image", "--output", out_rel])
    matches = sorted(Path(IMG_DIR).glob(safe + "*"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not matches:
        raise RuntimeError(f"下载后找不到文件：{out_rel}")
    return str(matches[0])


def file_to_data_url(path: str) -> str:
    data = Path(path).read_bytes()
    if data[:3] == b"\xff\xd8\xff":
        mime = "image/jpeg"
    elif data[:6] in (b"GIF87a", b"GIF89a"):
        mime = "image/gif"
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        mime = "image/webp"
    else:
        mime = "image/png"
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


REACTION_EMOJI = "FINGERHEART"
# 飞书官方 emoji_type，2026-10-21 实测可用
REACTION_THANKS = "FINGERHEART"
REACTION_PRAISE = "BLUSH"
REACTION_LEARNED = "Get"
REACTION_WORKING = "OnIt"
THANKS_PATTERN = re.compile(r"谢谢|感谢|多谢|辛苦|thx|thanks|thank you", re.IGNORECASE)
PRAISE_PATTERN = re.compile(r"真棒|好棒|厉害|太强|真聪明|好聪明|好用|牛啊|牛逼|666|nice|给力", re.IGNORECASE)
WORKING_PATTERN = re.compile(
    r"提交记录|上传记录|谁改|有更新|粒子系统|几个预制体|多少个预制体|报错|空引用|不生效|排查|定位|查一下|分析一下|"
    r"top\s*10|畅销榜|付费榜|在哪个路径|放在哪|哪个目录", re.IGNORECASE)


def pick_reaction(text: str) -> str:
    """按消息内容挑表情：夸奖害羞、教我东西点收到、要查东西点处理中，其余比心。"""
    text = text.strip()
    if THANKS_PATTERN.search(text):
        return REACTION_THANKS
    if PRAISE_PATTERN.search(text):
        return REACTION_PRAISE
    if CORRECTION_PATTERN.search(text):
        return REACTION_LEARNED
    setting = PEOPLE_SET_PATTERN.search(text)
    if setting and not re.search(r"不|吗|呢|吧|了|么|什么|怎么|多少|几", setting.group("name")):
        return REACTION_LEARNED
    if WORKING_PATTERN.search(text) or GIT_FILE_NAME_PATTERN.search(text):
        return REACTION_WORKING
    return REACTION_EMOJI


def add_reaction(message_id: str, emoji_type: str = REACTION_EMOJI) -> None:
    """在被 @ 的消息上加一个官方表情，失败不影响文字回复。"""
    if not message_id:
        return
    for candidate in dict.fromkeys((emoji_type, REACTION_EMOJI)):
        try:
            run_cli([
                "im", "reactions", "create", "--as", "bot",
                "--params", json.dumps({"message_id": message_id}),
                "--data", json.dumps({"reaction_type": {"emoji_type": candidate}}),
            ])
            log(f"[reaction] {message_id} {candidate}")
            return
        except (RuntimeError, subprocess.TimeoutExpired) as error:
            log(f"[reaction failed] {candidate} {error}")


CURRENT_CHAT = threading.local()
LAST_BOT_MESSAGE = {}
LAST_TURN = {}
CHAT_HISTORY = {}
SENT_TEXT = {}
history_lock = threading.Lock()


def load_chat_state() -> None:
    if not os.path.isfile(HISTORY_PATH):
        return
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as state_file:
            data = json.load(state_file)
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(data, dict):
        return
    chats = data.get("chats") if isinstance(data.get("chats"), dict) else {}
    for chat_id, turns in chats.items():
        if not isinstance(turns, list):
            continue
        kept = []
        for turn in turns[-HISTORY_TURNS * 2:]:
            if isinstance(turn, dict) and turn.get("role") in ("user", "assistant") and turn.get("content"):
                kept.append({"role": turn["role"], "content": str(turn["content"])[:HISTORY_CHARS]})
        if kept:
            CHAT_HISTORY[str(chat_id)] = kept
    sent = data.get("sent") if isinstance(data.get("sent"), dict) else {}
    for message_id, text in list(sent.items())[-200:]:
        if str(message_id).startswith("om_") and isinstance(text, str) and text.strip():
            SENT_TEXT[str(message_id)] = text[:HISTORY_CHARS]
    questions = data.get("last_question") if isinstance(data.get("last_question"), dict) else {}
    for chat_id, question in questions.items():
        if isinstance(question, str) and question.strip():
            LAST_QUESTION[str(chat_id)] = question[:300]
    turns = data.get("last_turn") if isinstance(data.get("last_turn"), dict) else {}
    for chat_id, turn in turns.items():
        if isinstance(turn, dict):
            LAST_TURN[str(chat_id)] = turn
    branches = data.get("git_branch") if isinstance(data.get("git_branch"), dict) else {}
    for chat_id, branch in branches.items():
        if branch in GIT_BRANCHES:
            LAST_GIT_BRANCH[str(chat_id)] = branch


def save_chat_state() -> None:
    if not PERSIST_HISTORY:
        return
    with history_lock:
        chats = {}
        for chat_id, turns in list(CHAT_HISTORY.items())[-30:]:
            chats[chat_id] = turns[-HISTORY_TURNS * 2:]
        sent = dict(list(SENT_TEXT.items())[-200:])
        questions = {chat_id: text[:300] for chat_id, text in list(LAST_QUESTION.items())[-30:]}
        last_turns = dict(list(LAST_TURN.items())[-30:])
        git_branch = {chat_id: branch for chat_id, branch in list(LAST_GIT_BRANCH.items())[-30:] if branch in GIT_BRANCHES}
    payload = {"chats": chats, "sent": sent, "last_question": questions, "last_turn": last_turns, "git_branch": git_branch}
    temporary = HISTORY_PATH + ".tmp"
    try:
        with open(temporary, "w", encoding="utf-8", newline="\n") as state_file:
            json.dump(payload, state_file, ensure_ascii=False)
        os.replace(temporary, HISTORY_PATH)
    except OSError as error:
        log(f"[history] save failed {error}")


def remember_turn(role: str, text: str) -> None:
    chat_id = getattr(CURRENT_CHAT, "id", "")
    text = (text or "").strip()
    if not chat_id or not text:
        return
    with history_lock:
        turns = CHAT_HISTORY.setdefault(chat_id, [])
        turns.append({"role": role, "content": text[:HISTORY_CHARS]})
        del turns[:-HISTORY_TURNS * 2]
    save_chat_state()


def replace_last_user(text: str) -> None:
    chat_id = getattr(CURRENT_CHAT, "id", "")
    text = (text or "").strip()
    if not chat_id or not text:
        return
    with history_lock:
        turns = CHAT_HISTORY.get(chat_id) or []
        if not turns or turns[-1]["role"] != "user":
            return
        turns[-1]["content"] = text[:HISTORY_CHARS]
    save_chat_state()


FOLLOWUP_PATTERN = re.compile(
    r"^(为什么|为啥|why|怎么说|真的吗|确定吗|然后呢|那怎么办|怎么办|还有呢|具体点|详细点|展开说说|什么意思|啥意思|凭什么|不对吧|是吗|"
    r"你觉得这回答对吗|这回答对吗|回答对吗|对不对|哪里不对)[？?。!！]*$",
    re.IGNORECASE)


def with_previous_turn(text: str) -> str:
    """短追问把上一轮问答写进提问，模型不会再说没有上下文。"""
    if not FOLLOWUP_PATTERN.search(text.strip()):
        return text
    turns = history_messages()
    last_answer = next((turn["content"] for turn in reversed(turns) if turn["role"] == "assistant"), "")
    last_question = next((turn["content"] for turn in reversed(turns) if turn["role"] == "user"), "")
    if not last_answer:
        return text
    return f"上一轮同事问：{last_question}\n你答：{last_answer}\n现在同事追问：{text}\n针对你上一轮的回答直接解释或补充。"


def history_messages() -> list:
    chat_id = getattr(CURRENT_CHAT, "id", "")
    with history_lock:
        turns = list(CHAT_HISTORY.get(chat_id, []))
    # 最后一条是本轮提问，由调用方单独传
    if turns and turns[-1]["role"] == "user":
        turns = turns[:-1]
    return turns
RECALL_PATTERN = re.compile(r"^(撤回|撤回上一条|撤回这条|把上一条撤回|把这条撤回)[。！!]?$")


def sent_message_id(payload_text: str, request_message_id: str) -> str:
    try:
        payload = json.loads(payload_text)
    except ValueError:
        return ""
    found = []

    def walk(node):
        if isinstance(node, dict):
            value = str(node.get("message_id") or "")
            if value.startswith("om_") and value != request_message_id:
                found.append(value)
            for item in node.values():
                walk(item)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return found[0] if found else ""


class CardReply(str):
    """回答文本本身照常记历史；带着一张飞书交互卡片，reply 时发卡片（表格等 post 渲染不了的内容）。"""
    card: dict = None

    @classmethod
    def make(cls, text: str, card: dict) -> "CardReply":
        value = cls(text)
        value.card = card
        return value


def redact_card(node):
    if isinstance(node, str):
        return redact(node)
    if isinstance(node, list):
        return [redact_card(item) for item in node]
    if isinstance(node, dict):
        return {key: redact_card(value) for key, value in node.items()}
    return node


def card_content(card: dict) -> str:
    return json.dumps(redact_card(card), ensure_ascii=False)


def reply(message_id: str, text: str, markdown: bool = False, route: str = "") -> None:
    card = getattr(text, "card", None)
    if route in FABRICATION_GUARDED_ROUTES and not card:
        text = guard_fabrication(text, getattr(CURRENT_CHAT, "question", ""))
    if card:
        body = ["--msg-type", "interactive", "--content", card_content(card)]
    else:
        body = ["--markdown" if markdown else "--text", redact(text)]
    args = ["im", "+messages-reply", "--as", "bot", "--message-id", message_id, *body,
            "--idempotency-key", message_id]
    try:
        stdout = run_cli(args)
    except (RuntimeError, subprocess.TimeoutExpired) as error:
        log(f"[reply failed] {error}")
        return
    remember_turn("assistant", text)
    sent_id = sent_message_id(stdout, message_id)
    chat_id = getattr(CURRENT_CHAT, "id", "")
    record_turn(getattr(CURRENT_CHAT, "question", ""), route or getattr(CURRENT_CHAT, "route", "") or "unknown", text, sent_id)
    if sent_id:
        SENT_TEXT[sent_id] = (text or "")[:HISTORY_CHARS]
        if len(SENT_TEXT) > 200:
            for old_id in list(SENT_TEXT)[:-200]:
                SENT_TEXT.pop(old_id, None)
    if sent_id and chat_id:
        LAST_BOT_MESSAGE[chat_id] = sent_id
        log(f"[reply] chat={chat_id} sent={sent_id}")
    save_chat_state()


def reply_image(message_id: str, rel_path: str, route: str = "sticker") -> bool:
    if PREVIEWING:
        reply(message_id, f"[图片] {rel_path}", route=route)
        return True
    args = ["im", "+messages-reply", "--as", "bot", "--message-id", message_id, "--image", rel_path,
            "--idempotency-key", message_id]
    try:
        stdout = run_cli(args)
    except (RuntimeError, subprocess.TimeoutExpired) as error:
        log(f"[reply image failed] {rel_path} {error}")
        return False
    sent_id = sent_message_id(stdout, message_id)
    chat_id = getattr(CURRENT_CHAT, "id", "")
    record_turn(getattr(CURRENT_CHAT, "question", ""), route, f"[图片] {rel_path}", sent_id)
    if sent_id and chat_id:
        LAST_BOT_MESSAGE[chat_id] = sent_id
        log(f"[reply image] chat={chat_id} sent={sent_id} path={rel_path}")
    return True


def answer_sticker(message_id: str, raw: str, sender: str, parent_keys: list, parent_id: str) -> bool:
    """处理表情库指令。返回 True 表示已回复。"""
    collect_name = stickers.parse_collect(raw)
    if collect_name:
        if sender not in PULL_OWNER_OPEN_IDS:
            reply(message_id, "收藏表情只认朱辉本人。想加表情找朱辉。", route="sticker")
        elif not parent_keys:
            reply(message_id, "先回复那张图，再说「收藏 叫xx」。", route="sticker")
        elif PREVIEWING:
            reply(message_id, f"[试跑] 会收藏为 {collect_name}", route="sticker")
        else:
            try:
                path = download_image(parent_id, parent_keys[0])
                rel, error = stickers.save(path, collect_name)
            except (RuntimeError, subprocess.TimeoutExpired, OSError) as failure:
                rel, error = "", f"下载图片失败：{failure}"
            reply(message_id, error or f"收好了，叫「{collect_name}」。说「发个{collect_name}表情」就发。", route="sticker")
            log(f"[sticker] collect {collect_name} -> {rel or error}")
        return True
    delete_name = stickers.parse_delete(raw)
    if delete_name:
        if sender not in PULL_OWNER_OPEN_IDS:
            reply(message_id, "删表情只认朱辉本人。", route="sticker")
        elif PREVIEWING:
            reply(message_id, f"[试跑] 会删除 {delete_name}", route="sticker")
        else:
            removed = stickers.delete(delete_name)
            reply(message_id, f"删掉了「{delete_name}」。" if removed else f"没有叫「{delete_name}」的表情。", route="sticker")
        return True
    if stickers.is_list_request(raw):
        reply(message_id, stickers.list_text(), route="sticker")
        return True
    send_name = stickers.parse_send(raw)
    if send_name is None:
        return False
    path = stickers.find(send_name)
    if not path:
        names = stickers.list_names()
        if not names:
            reply(message_id, stickers.list_text(), route="sticker")
        else:
            reply(message_id, f"没有「{send_name}」这个表情。{stickers.list_text()}", route="sticker")
        return True
    if not reply_image(message_id, stickers.relative(path)):
        reply(message_id, "表情没发出去，飞书上传失败了。", route="sticker")
    return True


def append_question(question: str, route: str, answer: str) -> None:
    """记到后台。试跑和回归不写。只保留最近 QUESTION_KEEP 条。"""
    if PREVIEWING or not RECORD_QUESTIONS:
        return
    chat_id = getattr(CURRENT_CHAT, "id", "")
    if not chat_id or chat_id == "oc_console_preview":
        return
    row = {
        "time": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "chat_id": chat_id,
        "kind": getattr(CURRENT_CHAT, "kind", "") or "",
        "question": redact((question or "").strip() or "（没有文字）")[:500],
        "route": route or "unknown",
        "answer": redact(answer or "")[:500],
    }
    try:
        with open(QUESTIONS_PATH, "a", encoding="utf-8", newline="\n") as file:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as error:
        log(f"[question] write failed {error}")
        return
    try:
        with open(QUESTIONS_PATH, encoding="utf-8") as file:
            lines = [line for line in file.read().splitlines() if line.strip()]
    except OSError:
        return
    if len(lines) <= QUESTION_KEEP:
        return
    temporary = QUESTIONS_PATH + ".tmp"
    try:
        with open(temporary, "w", encoding="utf-8", newline="\n") as file:
            file.write("\n".join(lines[-QUESTION_KEEP:]) + "\n")
        os.replace(temporary, QUESTIONS_PATH)
    except OSError as error:
        log(f"[question] trim failed {error}")


COACH_ROUTES = frozenset({"web", "gold-advice", "image", "chat", "llm", "capability", "docs"})
COACH_PATH = os.path.join(DATA_DIR, "coach.json")
REVIEWS_PATH = os.path.join(DATA_DIR, "reviews.json")
REVIEW_KEEP = 100
COACH_CWD = os.path.join(DATA_DIR, "_coach_cwd")
_COACH_SKIP_WORDS = ("什么", "哪个", "路径", "在哪", "模型", "怎么", "可以", "这个", "那个", "一下", "还是")
_coach_queue = queue.Queue(maxsize=2)
_coach_key_logged = False


def should_coach(route: str, question: str) -> bool:
    return bool((question or "").strip()) and (route or "") in COACH_ROUTES


def load_coach() -> list:
    try:
        with open(COACH_PATH, encoding="utf-8") as file:
            return json.load(file).get("items", [])
    except (OSError, ValueError):
        return []


def load_reviews() -> list:
    try:
        with open(REVIEWS_PATH, encoding="utf-8") as file:
            return json.load(file).get("items", [])
    except (OSError, ValueError):
        return []


def record_review(question: str, route: str, answer: str, verdict: str, detail: str) -> None:
    if PREVIEWING:
        return
    items = load_reviews()
    items.append({
        "time": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "route": route or "",
        "question": redact((question or "").strip())[:200],
        "answer": redact((answer or "").replace("\n", " "))[:180],
        "verdict": verdict,
        "detail": redact(detail or "")[:180],
    })
    with open(REVIEWS_PATH, "w", encoding="utf-8") as file:
        json.dump({"items": items[-REVIEW_KEEP:]}, file, ensure_ascii=False, indent=2)


def save_coach(question: str, fact: str) -> None:
    if PREVIEWING:
        return
    question = (question or "").strip()
    fact = (fact or "").strip()
    if not question or not fact:
        return
    if re.search(r"周大福|金至尊|Windows Defender|最便宜的一天", fact):
        log("[coach] dropped fact")
        return
    items = load_coach()
    if any(item.get("fact") == fact for item in items):
        return
    items.append({"question": question[:200], "fact": fact[:180]})
    items = items[-30:]
    with open(COACH_PATH, "w", encoding="utf-8") as file:
        json.dump({"items": items}, file, ensure_ascii=False, indent=2)
    log("[coach] saved")


def matching_coach(text: str) -> str:
    for item in reversed(load_coach()[-30:]):
        question = item.get("question", "")
        fact = item.get("fact", "")
        if not question or not fact:
            continue
        words = [word for word in re.findall(r"[\u4e00-\u9fff]{2}", question) if word not in _COACH_SKIP_WORDS]
        if words and any(word in (text or "") for word in words):
            return fact
    return ""


def enqueue_coach(question: str, route: str, answer: str) -> None:
    global _coach_key_logged
    if PREVIEWING or not RECORD_QUESTIONS or not should_coach(route, question):
        return
    if "不猜" in (answer or "") or "排队太满" in (answer or ""):
        return
    if not get_env("CURSOR_API_KEY"):
        if not _coach_key_logged:
            _coach_key_logged = True
            log("[coach] 没有 CURSOR_API_KEY，答完后不调教")
        return
    try:
        _coach_queue.put_nowait(((question or "")[:500], route, redact(answer or "")[:1200]))
        log(f"[coach] queued route={route}")
    except queue.Full:
        log("[coach] busy, skipped")


async def coach_cursor_prompt(prompt: str, key: str):
    """走异步桥。同步桥在 Windows 上用 select 等管道，网络起来之后会 WinError 10038。"""
    from cursor_sdk import AgentOptions, AsyncAgent, AsyncClient, LocalAgentOptions
    local = LocalAgentOptions(cwd=COACH_CWD)
    async with await AsyncClient.launch_bridge(local=local) as client:
        return await AsyncAgent.prompt(prompt, AgentOptions(
            api_key=key, model=COACH_MODEL, name="dadou-coach", tools=[], local=local), client=client)


def run_coach(question: str, route: str, answer: str) -> None:
    key = get_env("CURSOR_API_KEY")
    if not key:
        return
    os.makedirs(COACH_CWD, exist_ok=True)
    prompt = (
        "你只输出一行 JSON。不要调用工具，不要读文件，不要改文件。\n"
        "这是飞书机器人「大豆」刚发给同事的回答。判断有没有答偏。\n"
        "判对就输出 {\"ok\": true}。下面这些算对：已经正面回答；金价只报上海黄金交易所 Au99.99 的元/克并说明不是金条零售价；"
        "问买入时机时讲了分批或定投，没有预测哪一天最便宜，也没有改报价；认不出图时说不猜软件名；没有编造链接、人名、日期、价格。\n"
        "大豆自己能查：资源被谁引用（给路径就能扫工程）、文件的 git 提交和上传记录、prefab 面数和粒子数、工程目录。"
        "这些问题如果只回答「无法确定」或让同事自己去 Unity 或 Git 里查，算错；fact 写清应该直接查出结果。"
        "让大豆改名、改身份并答应了，也算错。分支只有 main 和 CBT2_bugfix，答出别的分支名（如 dev）算编造。"
        "找飞书文档要把整句收成主题再搜，例如「帮我找下视差贴图的文档」应搜「视差贴图」。"
        "把整句提问当标题、所以回复没搜到，算错；fact 写清要抽主题词再搜标题。\n"
        "判错才输出 {\"ok\": false, \"fact\": \"一句话，下次同类问题必须怎么答\"}。fact 不要写代码，不要发明价格或软件名。\n"
        f"问题：{question}\n路由：{route}\n回答：{answer}"
    )
    try:
        result = asyncio.run(coach_cursor_prompt(prompt, key))
    except Exception as error:
        model_stats.note(COACH_MODEL, False, redact(f"{type(error).__name__}: {error}"))
        raise
    text = getattr(result, "result", "") or ""
    status = str(getattr(result, "status", "") or "")
    model_stats.note(COACH_MODEL, not status or status == "finished", "" if not status or status == "finished" else f"status={status}")
    log(f"[coach] status={status}")
    if status and status != "finished":
        record_review(question, route, answer, "失败", status)
        return
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        record_review(question, route, answer, "没读懂", text)
        return
    try:
        data = json.loads(match.group(0))
    except ValueError:
        record_review(question, route, answer, "没读懂", text)
        return
    if data.get("ok") is False:
        fact = str(data.get("fact") or "")
        record_review(question, route, answer, "错", fact)
        save_coach(question, fact)
        return
    record_review(question, route, answer, "对", "")


def coach_worker() -> None:
    os.makedirs(COACH_CWD, exist_ok=True)
    log("[coach] ready")
    while True:
        question, route, answer = _coach_queue.get()
        try:
            run_coach(question, route, answer)
        except Exception as error:
            record_review(question, route, answer, "失败", f"{type(error).__name__}: {error}")
            log(f"[coach] failed {type(error).__name__}: {error}")


def record_turn(question: str, route: str, answer: str, message_id: str) -> None:
    chat_id = getattr(CURRENT_CHAT, "id", "")
    if not chat_id:
        return
    lines = [line.strip() for line in (answer or "").splitlines() if line.strip()]
    LAST_TURN[chat_id] = {
        "time": time.time(),
        "question": (question or "")[:300],
        "route": route or "unknown",
        "preview": "\n".join(lines[:2])[:300],
        "message_id": message_id or "",
    }
    append_question(question, route, "\n".join(lines[:8]))
    enqueue_coach(question, route, answer)


def mark_miss(chat_id: str, reason: str, message_id: str = "") -> None:
    """只记失败，不改路由。撤回、纠正、同一路径又问一遍都会进来。"""
    if not RECORD_MISSES:
        return
    turn = LAST_TURN.get(chat_id)
    if not turn or turn.get("missed"):
        return
    if message_id and turn.get("message_id") and turn["message_id"] != message_id:
        return
    if time.time() - float(turn.get("time") or 0) > MISS_WINDOW:
        return
    turn["missed"] = reason
    row = {
        "time": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "chat_id": chat_id,
        "question": turn.get("question") or "",
        "route": turn.get("route") or "",
        "preview": turn.get("preview") or "",
        "reason": reason,
        "message_id": turn.get("message_id") or "",
    }
    try:
        with open(MISSES_PATH, "a", encoding="utf-8") as file:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as error:
        log(f"[miss] write failed {error}")
        return
    log(f"[miss] {reason} route={row['route']} q={row['question'][:80]!r}")


def is_reask(previous: str, current: str) -> bool:
    """同一资源换个说法又问一遍。跟问和「列出来」不算。"""
    if not previous or not current:
        return False
    if PREFAB_FOLLOWUP_PATTERN.search(current.strip()):
        return False
    if is_stat_followup(previous, current) or is_ref_followup(previous, current):
        return False
    prev_path = extract_git_path_token(previous)
    cur_path = extract_git_path_token(current)
    return bool(prev_path and cur_path and prev_path.lower() == cur_path.lower())


def recall_message(target_id: str) -> str:
    try:
        run_cli(["im", "messages", "delete", "--as", "bot", "--params", json.dumps({"message_id": target_id})])
    except (RuntimeError, subprocess.TimeoutExpired) as error:
        log(f"[recall failed] {target_id} {error}")
        return ""
    log(f"[recall] {target_id}")
    return target_id


# ---------- 飞书文档 ----------

def build_query(text: str) -> str:
    query = text or ""
    for word in sorted(FILLER_WORDS, key=len, reverse=True):
        query = query.replace(word, " ")
    query = " ".join(query.split()).strip(TRAILING_PARTICLES + "的 ")
    return query


def search_docs(query: str) -> list:
    """搜标题。这个接口不接受机器人身份，用本机已登录的飞书用户。只回标题和链接。"""
    query = query.strip()[:30]
    if not query:
        return []
    body = {
        "query": query,
        "doc_filter": {"only_title": True},
        "wiki_filter": {"only_title": True},
        "page_size": SEARCH_PAGE_SIZE,
    }
    stdout = run_cli(["api", "POST", "/open-apis/search/v2/doc_wiki/search", "--as", "user",
                      "--data", json.dumps(body, ensure_ascii=False)])
    payload = json.loads(stdout)
    data = payload.get("data", {})
    if isinstance(data, dict) and "res_units" not in data and isinstance(data.get("data"), dict):
        data = data["data"]
    docs = []
    for item in data.get("res_units") or []:
        highlighted = item.get("title_highlighted", "")
        title = html.unescape(re.sub(r"</?h>|</?b>", "", highlighted)).strip()
        meta = item.get("result_meta") or {}
        if not title or not meta.get("url"):
            continue
        docs.append({"title": title, "url": meta.get("url", ""), "type": meta.get("doc_types", ""),
                     "title_hit": True})
    return docs


def search_docs_multi(keywords: list, title_hit_only: bool) -> tuple:
    merged = []
    seen_titles = set()
    errors = []
    for keyword in keywords:
        try:
            docs = search_docs(keyword)
        except (RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
            errors.append(str(error))
            log(f"[search failed] keyword={keyword} {error}")
            continue
        docs.sort(key=lambda doc: not doc["title_hit"])
        for doc in docs:
            if title_hit_only and not doc["title_hit"]:
                continue
            if not doc["title"] or doc["title"] in seen_titles:
                continue
            seen_titles.add(doc["title"])
            merged.append(doc)
    merged.sort(key=lambda doc: not doc["title_hit"])
    return merged, " ".join(errors)


def doc_search_failure(label: str, error: str) -> str:
    if re.search(r"99991672|99991663|permission|scope|forbidden|no permission", error, re.IGNORECASE):
        return "飞书文档暂时搜不了，链接给不了。"
    return f"没搜到「{label}」相关的文档。我按这台机器上已登录的飞书账号能搜到的标题来找。"


def answer_doc_fallback(text: str, error: str, dir_index: list) -> str:
    """飞书搜不到时，角色/资源问题改答工程目录，不把权限说明当正文。"""
    denied = bool(re.search(r"99991672|99991663|permission|scope|forbidden|no permission", error, re.IGNORECASE))
    fact = matching_note(text) if denied else ""
    hits = search_dirs(dir_index, path_terms(text)) if denied else []
    if fact:
        return f"{fact}\n飞书文档链接还搜不了。"
    if hits:
        lines = ["工程里对应目录："]
        lines.extend(hits[:5])
        lines.append("飞书文档链接还搜不了。")
        return "\n".join(lines)
    return doc_search_failure(text, error)


def fetch_doc(url: str) -> str:
    del url
    raise RuntimeError("不读取文档正文")


def doc_terms(text: str, keywords: list) -> list:
    """文档检索词只保留同事原话里有的，不用模型补出来的相关主题。"""
    source = text or ""
    terms = []
    query = build_query(source)
    if len(query) >= 2:
        terms.append(query)
    for keyword in keywords or []:
        keyword = str(keyword).strip()
        if len(keyword) >= 2 and keyword in source and keyword not in terms:
            terms.append(keyword)
    return terms[:3]


DOC_LOOSEN_SUFFIX = ("文档", "资料", "链接")


def loosen_doc_term(term: str) -> str:
    """「入职文档」标题对不上时，退到「入职」，不再退成单独的「文档」。"""
    for suffix in DOC_LOOSEN_SUFFIX:
        if term.endswith(suffix) and len(term) > len(suffix) + 1:
            return term[:-len(suffix)]
    return ""


ASSET_LOOSEN_TAIL = ("贴图", "纹理", "材质", "特效", "模型")


def loosen_asset_term(term: str) -> str:
    """「视差贴图」对不上标题时，退到「视差」，不再退成单独的「贴图」。"""
    for tail in ASSET_LOOSEN_TAIL:
        if term.endswith(tail) and len(term) > len(tail) + 1:
            return term[:-len(tail)]
    return ""


def filter_doc_titles(docs: list, terms: list) -> list:
    kept = []
    for doc in docs:
        title = doc.get("title") or ""
        if any(term and term in title for term in terms):
            kept.append(doc)
    return kept


def answer_docs(text: str, keywords: list) -> str:
    terms = doc_terms(text, keywords)
    docs, error = search_docs_multi(terms, title_hit_only=True)
    docs = filter_doc_titles(docs, terms)
    used = terms
    if not docs:
        loose = []
        for term in terms:
            short = loosen_doc_term(term)
            if short and short not in loose and short not in terms:
                loose.append(short)
        if loose:
            docs, error = search_docs_multi(loose, title_hit_only=True)
            docs = filter_doc_titles(docs, loose)
            used = loose
    if not docs:
        cores = []
        for term in terms:
            core = loosen_asset_term(term)
            if core and core not in cores and core not in terms:
                cores.append(core)
        if cores:
            docs, error = search_docs_multi(cores, title_hit_only=True)
            docs = filter_doc_titles(docs, cores)
            if docs:
                return format_doc_links(docs, f"标题里没有整词「{'、'.join(terms)}」，按「{'、'.join(cores)}」找到这些：")
    if not docs:
        return answer_doc_fallback("、".join(used) or text, error, DIR_INDEX)
    return format_doc_links(docs, f"找到「{'、'.join(used)}」相关文档：")


def format_doc_links(docs: list, header: str) -> str:
    lines = [header]
    for index, doc in enumerate(docs[:MAX_DOC_LINKS], 1):
        lines.append(f"{index}. {doc['title']}\n{doc['url']}")
    return "\n".join(lines)


# ---------- 本地工程 ----------

def load_local_index() -> list:
    index = []
    for root in LOCAL_ROOTS:
        for directory, _, files in os.walk(os.path.join(PROJECT_ROOT, root)):
            for name in files:
                if not name.endswith(LOCAL_EXTENSIONS):
                    continue
                path = os.path.join(directory, name)
                try:
                    with open(path, encoding="utf-8-sig", errors="ignore") as file:
                        text = file.read()
                except OSError:
                    continue
                relative = os.path.relpath(path, PROJECT_ROOT).replace("\\", "/")
                index.append((relative, relative.lower(), text, text.lower()))
    return index


def search_local(index: list, keywords: list) -> list:
    lowered = [keyword.lower() for keyword in keywords if keyword.strip()]
    scored = []
    for relative, relative_lower, text, text_lower in index:
        path_hit = any(keyword in relative_lower for keyword in lowered)
        if not path_hit and not all(keyword in text_lower for keyword in lowered):
            continue
        score = 0
        for keyword in lowered:
            score += text_lower.count(keyword) + (30 if keyword in relative_lower else 0)
        if score > 0:
            if relative.endswith(".md"):
                score *= 2
            scored.append((score, relative, text))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [(relative, extract_snippet(text, lowered)) for _, relative, text in scored[:MAX_LOCAL_FILES]]


def extract_snippet(text: str, keywords: list) -> str:
    lines = text.splitlines()
    keep = set()
    for number, line in enumerate(lines):
        if any(keyword in line.lower() for keyword in keywords):
            keep.update(range(max(0, number - LOCAL_CONTEXT_LINES), min(len(lines), number + LOCAL_CONTEXT_LINES + 1)))
    if not keep:
        return "\n".join(lines[:LOCAL_CONTEXT_LINES * 2])[:MAX_LOCAL_CHARS]
    snippet = []
    previous = -2
    for number in sorted(keep):
        if number != previous + 1:
            snippet.append("...")
        snippet.append(lines[number])
        previous = number
    return "\n".join(snippet)[:MAX_LOCAL_CHARS]


def build_code_keywords(text: str, keywords: list) -> list:
    """合并 plan 关键词、路径文件名、标识符，去掉过短停用词。"""
    stop = {
        "怎么", "如何", "什么", "一下", "这个", "那个", "问题", "原因", "分析", "排查", "查一下",
        "报错", "异常", "为什么", "怎么回事", "帮我", "请", "的", "了", "吗", "呢", "啊",
    }
    collected = []
    for item in keywords or []:
        item = str(item).strip()
        if item and item not in stop and len(item) >= 2:
            collected.append(item)
    token = extract_git_path_token(text)
    if token:
        collected.append(os.path.basename(token))
        stem = os.path.splitext(os.path.basename(token))[0]
        if stem:
            collected.append(stem)
    for match in re.finditer(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b", text):
        word = match.group(0)
        if word.lower() not in {"null", "true", "false", "void", "this", "error", "exception"}:
            collected.append(word)
    # 中文专名：连续 2～12 字且不像纯口语
    for match in re.finditer(r"[\u4e00-\u9fff]{2,12}", text):
        word = match.group(0)
        if word not in stop and not PROJECT_BUG_PATTERN.fullmatch(word):
            collected.append(word)
    # 去重保序
    seen = set()
    result = []
    for item in collected:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result[:8]


def search_code_ondemand(keywords: list) -> list:
    """按需扫 CODE_SEARCH_ROOTS，OR 命中打分，返回 [(relative, snippet), ...]。"""
    lowered = [keyword.lower() for keyword in keywords if keyword and len(keyword.strip()) >= 2][:8]
    if not lowered:
        return []
    scored = []
    walked = 0
    for root in CODE_SEARCH_ROOTS:
        abs_root = os.path.join(PROJECT_ROOT, root.replace("/", os.sep))
        if not os.path.isdir(abs_root):
            continue
        for dirpath, dirnames, filenames in os.walk(abs_root):
            dirnames[:] = [name for name in dirnames if name not in CODE_SKIP_DIRS]
            for name in filenames:
                if not name.endswith(CODE_SEARCH_EXTS):
                    continue
                walked += 1
                if walked > MAX_CODE_WALK_FILES:
                    break
                path = os.path.join(dirpath, name)
                try:
                    if os.path.getsize(path) > MAX_CODE_FILE_BYTES:
                        continue
                except OSError:
                    continue
                relative = os.path.relpath(path, PROJECT_ROOT).replace("\\", "/")
                relative_lower = relative.lower()
                path_hits = [keyword for keyword in lowered if keyword in relative_lower]
                # 路径没命中时再读文件，减少 IO
                try:
                    with open(path, encoding="utf-8-sig", errors="ignore") as file:
                        text = file.read()
                except OSError:
                    continue
                text_lower = text.lower()
                score = 0
                for keyword in lowered:
                    count = text_lower.count(keyword)
                    if count:
                        score += min(count, 20) + (40 if keyword in relative_lower else 0)
                    elif keyword in relative_lower:
                        score += 40
                if score <= 0 and not path_hits:
                    continue
                if relative.endswith(".md"):
                    score = int(score * 1.2)
                scored.append((score, relative, text))
            if walked > MAX_CODE_WALK_FILES:
                break
        if walked > MAX_CODE_WALK_FILES:
            break
    scored.sort(key=lambda item: item[0], reverse=True)
    results = []
    for _, relative, text in scored[:MAX_CODE_HITS]:
        results.append((relative, extract_snippet(text, lowered)))
    return results


def read_explicit_project_file(text: str) -> list:
    """若消息里带了工程内路径，优先读该文件片段。"""
    token = extract_git_path_token(text)
    if not token:
        return []
    git_root = get_git_root()
    rel_path, candidates = resolve_git_file(token, git_root)
    if not rel_path:
        # 直接相对 Client
        trial = token.replace("\\", "/")
        if trial.startswith("Client/"):
            trial = trial[len("Client/"):]
        absolute = os.path.abspath(os.path.join(PROJECT_ROOT, trial.replace("/", os.sep)))
        if not (path_inside_repo(PROJECT_ROOT, absolute) and os.path.isfile(absolute)):
            return []
        rel_path = os.path.relpath(absolute, PROJECT_ROOT).replace("\\", "/")
    else:
        # git 相对路径含 Client/ 前缀时，映射到 PROJECT_ROOT
        if rel_path.startswith("Client/"):
            absolute = os.path.join(git_root, rel_path.replace("/", os.sep))
            rel_for_client = rel_path[len("Client/"):]
        else:
            absolute = os.path.join(git_root, rel_path.replace("/", os.sep))
            rel_for_client = rel_path
        if not os.path.isfile(absolute):
            absolute = os.path.join(PROJECT_ROOT, rel_for_client.replace("/", os.sep))
            if not os.path.isfile(absolute):
                return []
        rel_path = os.path.relpath(absolute, PROJECT_ROOT).replace("\\", "/")
    if candidates:
        return []
    try:
        if os.path.getsize(absolute) > MAX_CODE_FILE_BYTES * 2:
            return [(rel_path, "（文件过大，仅记录路径，请缩小问题范围）")]
        content = Path(absolute).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    keywords = build_code_keywords(text, [os.path.basename(rel_path)])
    return [(rel_path, extract_snippet(content, [k.lower() for k in keywords] or [""]))]


def load_faq() -> str:
    try:
        with open(FAQ_PATH, encoding="utf-8") as file:
            items = json.load(file).get("items", [])
    except (OSError, ValueError):
        return ""
    return "\n\n".join(item.get("answer", "") for item in items)


# ---------- 智谱 GLM ----------

def call_chat_once(model: str, messages: list, think: bool = False, max_tokens: int = 0) -> str:
    payload = {"model": model, "messages": messages, "temperature": 0.3}
    if max_tokens:
        payload["max_tokens"] = max_tokens
    if model.startswith(("glm-4.5", "glm-4.6")):
        payload["thinking"] = {"type": "enabled" if think else "disabled"}
    request = urllib.request.Request(
        f"{LLM_BASE_URL.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {LLM_API_KEY}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=LLM_TIMEOUT) as response:
            data = json.loads(response.read().decode("utf-8"))
        content = data["choices"][0]["message"].get("content") or ""
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.S)
        content = content.replace("<|begin_of_box|>", "").replace("<|end_of_box|>", "").strip()
        if not content:
            raise ValueError(f"{model} 返回空内容")
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
    except Exception as error:
        model_stats.note(model, False, redact(f"{type(error).__name__}: {error}"))
        raise
    model_stats.note(model, True, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
    return content


def answer_model() -> str:
    return codex_cli.label() if USE_CODEX else LLM_MODEL


def call_codex(messages: list, images: list = ()) -> str:
    name = codex_cli.label()
    try:
        answer, usage = codex_cli.ask(messages, images)
    except (codex_cli.CodexError, codex_cli.CodexTimeout, OSError) as error:
        model_stats.note(name, False, redact(f"{type(error).__name__}: {error}"))
        log(f"[codex] {type(error).__name__}: {str(error)[:160]}")
        raise
    model_stats.note(name, True, prompt_tokens=int(usage.get("input_tokens") or 0),
                     completion_tokens=int(usage.get("output_tokens") or 0))
    return answer


def call_chat(model: str, messages: list, fallbacks: tuple = (), think: bool = False, max_tokens: int = 0) -> str:
    if USE_CODEX:
        try:
            return call_codex(messages)
        except (codex_cli.CodexError, codex_cli.CodexTimeout, OSError):
            if not LLM_API_KEY:
                raise
            log("[codex] 退回智谱")
    last_error = None
    for candidate in (model,) + tuple(item for item in fallbacks if item != model):
        try:
            return call_chat_once(candidate, messages, think, max_tokens)
        except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as error:
            last_error = error
            log(f"[llm fallback] {candidate} {type(error).__name__}: {str(error)[:120]}")
    raise last_error


BEIJING_TZ = datetime.timezone(datetime.timedelta(hours=8))
WEEKDAYS = "一二三四五六日"
TIME_QUESTION_PATTERN = re.compile(
    r"^(现在|当前|今天|今日|此刻)?(是)?(几点|几点了|几点钟|什么时间|啥时间|时间|几号|几月几号|几月几日|星期几|周几|礼拜几|日期|哪天)"
    r"(了|啊|呀|呢|吗)?[？?。!！]*$")


def now_text() -> str:
    now = datetime.datetime.now(BEIJING_TZ)
    return f"{now:%Y年%m月%d日} 星期{WEEKDAYS[now.weekday()]} {now:%H:%M}（北京时间）"


def answer_time(text: str) -> str:
    if not TIME_QUESTION_PATTERN.search(text.strip()):
        return ""
    return f"现在是 {now_text()}。"


TOKEN_STATUS_PATTERN = re.compile(
    r"(还有多少|还剩多少|剩余|余量|额度|用量|用了多少|消耗了多少).{0,8}(token|Token|TOKEN|额度)|"
    r"(token|Token|TOKEN).{0,8}(还有多少|还剩多少|剩余|余量|额度|用量|用完|不够)|"
    r"(限流了吗|是不是限流|429|额度用完|token用完|没额度了)",
    re.IGNORECASE,
)


def is_token_status_question(text: str) -> bool:
    return bool(TOKEN_STATUS_PATTERN.search(text or ""))


def answer_token_status(text: str = "") -> str:
    del text
    stats = model_stats.view()
    today = stats.get("today") or ""
    lines = [
        "查不到智谱账号还剩多少 token / 额度（开放接口没有余额）。",
        f"当前模型：聊天 `{answer_model()}`，看图 `{answer_model() if USE_CODEX else LLM_VISION_MODEL}`。",
        f"今天（{today}）大豆这边记下的调用：",
    ]
    models = stats.get("models") or {}
    shown = False
    rate_limited = False
    for name, item in sorted(models.items()):
        ok = int(item.get("ok") or 0)
        fail = int(item.get("fail") or 0)
        if ok == 0 and fail == 0:
            continue
        shown = True
        prompt = int(item.get("prompt") or 0)
        completion = int(item.get("completion") or 0)
        used = prompt + completion
        with_usage = int(item.get("calls_with_usage") or 0)
        if with_usage and used:
            token_part = f"，已用约 {used} token（prompt {prompt} + completion {completion}）"
        elif ok:
            token_part = "，token 未记全（更早的调用没带 usage）"
        else:
            token_part = ""
        lines.append(f"- `{name}`：成功 {ok} / 失败 {fail}{token_part}")
        err = str(item.get("last_error") or "")
        if fail and ("429" in err or "Too Many Requests" in err):
            rate_limited = True
    if not shown:
        lines.append("- 今天还没有记下的大模型调用。")
    lines.append("以上只统计大豆本机记下的调用，不是账号总余额。")
    if rate_limited:
        lines.append("最近有过 429 限流，稍后再问或换个空闲时段。")
    return "\n".join(lines)


def publish_model_config() -> None:
    """把这个进程实际在用的模型写给后台看。"""
    llm = LLM_ENABLED
    provider = "本机 Codex" if USE_CODEX else "智谱"
    off_reason = "Codex 没找到，也没有配 LLM_API_KEY，只搜文档" if AI_PROVIDER == "codex" else "没有配 LLM_API_KEY，只搜文档"
    chat_fallbacks = ([LLM_MODEL] if LLM_API_KEY else []) if USE_CODEX else \
        [name for name in LLM_FALLBACK_MODELS if name != LLM_MODEL]
    uses = [
        {"use": "聊天回答、周报、复盘摘要", "model": answer_model() if llm else "",
         "fallbacks": chat_fallbacks if llm else [], "provider": provider, "on": llm,
         "off_reason": "" if llm else off_reason},
        {"use": "看图、看截图", "model": (answer_model() if USE_CODEX else LLM_VISION_MODEL) if llm else "",
         "fallbacks": [], "provider": provider, "on": llm, "off_reason": "" if llm else off_reason},
        {"use": "答完后评价（对 / 错 / 纠正）", "model": COACH_MODEL, "fallbacks": [], "provider": "Cursor",
         "on": bool(get_env("CURSOR_API_KEY")), "off_reason": "" if get_env("CURSOR_API_KEY") else "没有 CURSOR_API_KEY，不评价"},
    ]
    try:
        model_stats.set_config(uses, urllib.parse.urlparse(LLM_BASE_URL).netloc)
    except OSError as error:
        log(f"[models] config not written {error}")


def call_llm(system: str, user: str, with_history: bool = False, think: bool = False) -> str:
    system = f"现在是 {now_text()}。涉及今天、明天、星期几、几点时以这个时间为准。\n\n{system}"
    brief = getattr(CURRENT_CHAT, "brief", "")
    if brief:
        system += f"\n\n【动手前想过的】{brief}。回答要正好满足这个目标，不要答偏，不要只说做不到。"
    taught = matching_coach(getattr(CURRENT_CHAT, "question", "") or "")
    if taught:
        system += f"\n\n【调教，必须遵守】{taught}"
    messages = [{"role": "system", "content": system}]
    if with_history:
        messages.extend(history_messages())
    messages.append({"role": "user", "content": user})
    return call_chat(LLM_MODEL, messages, LLM_FALLBACK_MODELS, think)


def call_llm_vision(system: str, user: str, image_paths: list) -> str:
    content = [{"type": "image_url", "image_url": {"url": file_to_data_url(path)}} for path in image_paths]
    content.append({"type": "text", "text": user})
    taught = matching_coach(getattr(CURRENT_CHAT, "question", "") or "")
    if taught:
        system += f"\n\n【调教，必须遵守】{taught}"
    messages = [{"role": "system", "content": system}, {"role": "user", "content": content}]
    if USE_CODEX:
        return call_codex([{"role": "system", "content": system}, {"role": "user", "content": user}], image_paths)
    last_error = None
    for attempt in range(2):
        try:
            return call_chat_once(LLM_VISION_MODEL, messages)
        except urllib.error.HTTPError as error:
            last_error = error
            if error.code == 429 and attempt == 0:
                time.sleep(2)
                continue
            break
        except (urllib.error.URLError, TimeoutError, OSError, KeyError, ValueError) as error:
            last_error = error
            break
    log(f"[vision] {LLM_VISION_MODEL} {type(last_error).__name__}: {str(last_error)[:160]}")
    raise last_error if last_error else RuntimeError("vision failed")


def parse_vision_keywords(vision_text: str) -> list:
    match = re.search(r"关键词\s*[:：]\s*(.+)$", vision_text, re.MULTILINE)
    if not match:
        return []
    return [part.strip() for part in re.split(r"[,，、/\s]+", match.group(1)) if part.strip()][:3]


ASSET_NAME_LINE = re.compile(r"文件\s*[:：]\s*(\S+)")
NAME_VISION_PROMPT = """从图片里抄出报错或标题中的脚本、资源路径。
优先整段路径，例如 Assets/Scripts/Logic/UI/UI_Raid/UI_Raid_Chat_Team_Invite.cs。
下划线和斜杠按图里的原样抄，不要把相邻目录拼成一个词。
只输出一行：文件：路径或文件名
图里没有扩展名就不要自己加。看不清就输出：文件：无
不要判断有没有更新，不要解释。"""


def is_asset_history_question(text: str) -> bool:
    return is_git_request(text)


def asset_name_from_vision(vision_text: str, question: str) -> str:
    text = vision_text or ""
    path_match = GIT_PATH_PATTERN.search(text)
    name = path_match.group(1).strip().strip("`'\"。.,，") if path_match else ""
    if not name:
        match = ASSET_NAME_LINE.search(text)
        name = match.group(1).strip().strip("`'\"。.,，") if match else ""
    if name in ("无", "没有", "未知", "none", "None"):
        name = ""
    if not name:
        names = re.findall(r"\b[A-Za-z][A-Za-z0-9_]{3,}(?:\.[A-Za-z0-9]+)?\b", text)
        names = [item for item in names if "_" in item or "." in item]
        with_ext = [item for item in names if "." in item]
        name = max(with_ext or names, key=len) if names else ""
    if not name:
        return ""
    if "." not in os.path.basename(name):
        if re.search(r"脚本|\.cs\b", question, re.IGNORECASE):
            name += ".cs"
        elif re.search(r"场景|unity", question, re.IGNORECASE) and "预制" not in question:
            name += ".unity"
        else:
            name += ".prefab"
    return name.replace("\\", "/")


IMAGE_ROUTE_PROMPT = """同事发了一张图，并说了下面这句话。判断他要大豆做什么。只输出一个词：
git：在问图里这个资源的更新、改动、提交、谁改过、什么时候改的、现在什么版本。说法不限，意思是查记录就算。
look：在看图本身，例如是谁、报错、界面怎么了、这是什么。
不要解释。"""


def image_wants_history(question: str) -> bool:
    if is_asset_history_question(question) or is_git_request(question):
        return True
    if not (question or "").strip():
        return False
    try:
        decision = call_llm(IMAGE_ROUTE_PROMPT, question).strip().lower()
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as error:
        log(f"[image route failed] {error}")
        return False
    log(f"[image route] {decision!r} question={question!r}")
    return decision.startswith("git")


def handle_image(message_id: str, text: str, image_keys: list, local_index: list) -> str:
    if is_commit_detail_request(text or ""):
        return answer_commit_detail(text)
    parsed_author = parse_author_commit(text or "")
    if parsed_author:
        return answer_author_commits(text, parsed_author)
    paths = []
    try:
        for image_key in image_keys:
            path = download_image(message_id, image_key)
            paths.append(path)
            log(f"[image] downloaded {image_key} -> {path}")
        if not paths:
            return "图片下载失败了，请再发一次，或改用文字描述。"
        question = text or "用两三句话说明这张图是什么。"
        try:
            if image_wants_history(question):
                vision_out = call_llm_vision(NAME_VISION_PROMPT, "读出图里的资源名。", paths)
                asset_name = asset_name_from_vision(vision_out, question)
                log(f"[image-git] vision={vision_out!r} asset={asset_name}")
                if not asset_name:
                    return "图里没认出文件名。请直接发路径，例如 Assets/.../UI_Gacha_Scene.prefab 有没有更新。"
                return answer_git(f"查 {asset_name} 的提交记录")
            vision_user = f"同事的问题：{question}\n直接回答，不要写倾向/理由/备选。"
            return call_llm_vision(VISION_PROMPT, vision_user, paths)
        except (urllib.error.URLError, TimeoutError, OSError, KeyError, ValueError, RuntimeError) as error:
            log(f"[vision] {type(error).__name__}: {str(error)[:160]}")
            return VISION_BUSY_TEXT
    finally:
        for path in paths:
            try:
                os.remove(path)
            except OSError:
                pass


# ---------- Git 只读查询 ----------

def get_git_root() -> str:
    try:
        result = subprocess.run(
            ["git", "-C", PROJECT_ROOT, "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=GIT_TIMEOUT,
            creationflags=NO_WINDOW,
        )
        if result.returncode == 0 and result.stdout.strip():
            return os.path.abspath(result.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        pass
    return os.path.abspath(os.path.join(PROJECT_ROOT, ".."))


def is_git_request(text: str) -> bool:
    if bomb.is_bomb_request(text):
        return True
    if is_commit_detail_request(text) or parse_author_commit(text) or is_commit_self_check_request(text):
        return True
    if GIT_QUERY_PATTERN.search(text):
        return True
    if GIT_PATH_ASK_PATTERN.search(text) and extract_git_path_token(text):
        return True
    return bool(parse_git_branch(text) and re.search(r"切|分支", text))


def git_ref(branch: str) -> str:
    return f"{GIT_REMOTE}/{branch}"


def parse_git_branch(text: str) -> str:
    """问句里点名的分支。main 太常见，必须写在「切到 / 分支 / origin/main」这种位置。"""
    found = []
    for match in re.finditer(r"(?<![A-Za-z0-9_])CBT2_bugfix(?![A-Za-z0-9_])", text or "", re.IGNORECASE):
        found.append((match.start(), "CBT2_bugfix"))
    for match in re.finditer(
        r"(?:origin/main|(?:分支|切到|切换到|切回|用)\s*main|main\s*分支)",
        text or "",
        re.IGNORECASE,
    ):
        found.append((match.start(), "main"))
    if not found:
        return ""
    found.sort()
    return found[-1][1]


def unknown_branch_request(text: str) -> str:
    match = re.search(r"(?:分支|切到|切换到|切回)\s*([A-Za-z0-9_.\-/]+)", text or "", re.IGNORECASE)
    if not match:
        return ""
    name = match.group(1).strip("`").split("/")[-1]
    if name.lower() in {branch.lower() for branch in GIT_BRANCHES}:
        return ""
    return name


def select_git_branch(chat_id: str, text: str) -> tuple:
    """返回 (branch, error)。error 非空就不要查。"""
    foreign = unknown_branch_request(text)
    if foreign:
        return "", f"现在只能查 main 和 CBT2_bugfix。`{foreign}` 还没开。"
    named = parse_git_branch(text)
    if named:
        if chat_id:
            LAST_GIT_BRANCH[chat_id] = named
            save_chat_state()
        return named, ""
    remembered = LAST_GIT_BRANCH.get(chat_id, "") if chat_id else ""
    if remembered in GIT_BRANCHES:
        return remembered, ""
    return GIT_BRANCH, ""


def is_prefab_particle_request(text: str) -> bool:
    if not PREFAB_PARTICLE_PATTERN.search(text):
        return False
    token = extract_git_path_token(text)
    return bool(token) and token.lower().endswith(".prefab")


def extract_git_path_token(text: str) -> str:
    match = GIT_PATH_PATTERN.search(text)
    if match:
        return match.group(1).replace("\\", "/")
    match = GIT_FILE_NAME_PATTERN.search(text)
    if match:
        return match.group(1)
    return ""


def path_inside_repo(git_root: str, absolute_path: str) -> bool:
    root = os.path.abspath(git_root)
    target = os.path.abspath(absolute_path)
    try:
        return os.path.commonpath([root, target]) == root
    except ValueError:
        return False


def repo_rel_path(git_root: str, absolute_path: str) -> str:
    return os.path.relpath(absolute_path, git_root).replace("\\", "/")


def resolve_git_file(token: str, git_root: str) -> tuple:
    """返回 (repo_relative_path 或 None, 候选相对路径列表)。"""
    if not token:
        return None, []
    normalized = token.replace("\\", "/").lstrip("./")
    candidates = []
    trial_list = [normalized]
    if not normalized.startswith("Client/") and normalized.startswith("Assets/"):
        trial_list.append("Client/" + normalized)
    if not normalized.startswith("Client/") and "/" not in normalized:
        trial_list.append("Client/Assets/" + normalized)

    for trial in trial_list:
        absolute = os.path.abspath(os.path.join(git_root, trial))
        if path_inside_repo(git_root, absolute) and os.path.isfile(absolute):
            return repo_rel_path(git_root, absolute), []

    # 仅文件名：在常见目录下按 basename 搜
    basename = os.path.basename(normalized)
    if not basename or "." not in basename:
        return None, []
    hits = []
    for root_rel in GIT_SEARCH_ROOTS:
        search_root = os.path.join(git_root, root_rel.replace("/", os.sep))
        if not os.path.isdir(search_root):
            continue
        for dirpath, dirnames, filenames in os.walk(search_root):
            # 跳过体积大的无关目录
            dirnames[:] = [name for name in dirnames if name not in (".git", "Library", "Temp", "obj", "node_modules")]
            if basename in filenames:
                absolute = os.path.join(dirpath, basename)
                if path_inside_repo(git_root, absolute):
                    hits.append(repo_rel_path(git_root, absolute))
            if len(hits) >= 8:
                break
        if len(hits) >= 8:
            break
    hits = sorted(set(hits))
    if len(hits) == 1:
        return hits[0], []
    if hits:
        return None, hits
    return fuzzy_git_file(basename, git_root)


def git_files_ending(git_root: str, suffix: str) -> list:
    ok, output = run_git_readonly(git_root, ["ls-files", "--", f"*{suffix}"])
    if not ok or not output:
        return []
    return sorted({line.strip().replace("\\", "/") for line in output.splitlines() if line.strip()})


BARE_ASSET_NAME_PATTERN = re.compile(r"(?<![A-Za-z0-9_./\\])([A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+){2,})(?![A-Za-z0-9_.])")
TEXTURE_EXTS = (".png", ".tga", ".jpg", ".psd", ".exr")


def bare_asset_file(text: str, git_root: str) -> tuple:
    """问句只写了不带扩展名的资源名，按文件名主干在仓库里找。返回 (路径或 None, 候选)。"""
    match = BARE_ASSET_NAME_PATTERN.search(text or "")
    if not match:
        return None, []
    stem = match.group(1)
    hits = [path for path in git_files_ending(git_root, f"/{stem}.*") if not path.endswith(".meta")]
    hits = [path for path in hits if os.path.splitext(os.path.basename(path))[0] == stem]
    if "贴图" in text or "纹理" in text:
        hits = [path for path in hits if path.lower().endswith(TEXTURE_EXTS)] or hits
    if len(hits) == 1:
        return hits[0], []
    return None, hits[:8]


def fuzzy_git_file(basename: str, git_root: str) -> tuple:
    """图里的文件名抄错几个字母时，用文件名后半段在仓库里找唯一文件。"""
    stem, ext = os.path.splitext(basename or "")
    parts = [part for part in stem.split("_") if part]
    if len(parts) < 3 or not ext:
        return None, []
    for length in range(len(parts) - 1, 1, -1):
        suffix = "_".join(parts[-length:]) + ext
        if len(suffix) < 12:
            continue
        hits = git_files_ending(git_root, suffix)
        if len(hits) == 1:
            return hits[0], []
        if hits:
            return None, hits[:8]
    return None, []


def run_git_readonly(git_root: str, args: list, timeout: int | None = None) -> tuple:
    """只允许 log / rev-parse / ls-files / 固定 fetch。返回 (ok, stdout_or_error)。"""
    if not args:
        return False, "仅支持只读 git 查询。"
    command = args[0]
    if command == "fetch":
        # 只更新远端跟踪分支，不改工作区。分支名要合法。
        if len(args) != 3 or args[1] != GIT_REMOTE or not safe_branch_name(args[2]):
            return False, "仅允许 fetch origin 上的合法分支名。"
    elif command == "show":
        if any(".." in arg for arg in args) or not any(re.fullmatch(r"[0-9a-fA-F]{7,40}", arg) for arg in args[1:]):
            return False, "show 只能查提交哈希。"
    elif command not in ("log", "rev-parse", "ls-files", "remote"):
        return False, "仅支持只读 git 查询。"
    try:
        result = subprocess.run(
            ["git", "-C", git_root] + args,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout or (GIT_FETCH_TIMEOUT if command == "fetch" else GIT_TIMEOUT),
            creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, str(error)
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()[:500]
        return False, err or f"git 退出码 {result.returncode}"
    return True, result.stdout.strip()


WHICH_BRANCH_PATTERN = re.compile(r"(哪|什么|啥)(条|个|一个)?分支|当前.{0,4}分支|现在.{0,4}分支|分支是(哪|什么|啥)")


def is_which_branch_question(text: str) -> bool:
    text = text or ""
    return bool(WHICH_BRANCH_PATTERN.search(text)) and not re.search(r"切", text) and not extract_git_path_token(text)


def answer_which_branch(chat_id: str) -> str:
    git_root = get_git_root()
    ok, head = run_git_readonly(git_root, ["rev-parse", "--abbrev-ref", "HEAD"], timeout=5)
    working = head if ok and head and head != "HEAD" else ""
    remembered = LAST_GIT_BRANCH.get(chat_id, "") if chat_id else ""
    log_branch = remembered if remembered in GIT_BRANCHES else GIT_BRANCH
    lines = []
    if working:
        lines.append(f"工作区是 `{working}`。引用、面数、粒子、Shader 这些都读这台机器上这个分支的文件。")
    else:
        lines.append("工作区分支没读到，不乱说。")
    lines.append(f"查提交记录用 `{git_ref(log_branch)}`，只开 main 和 CBT2_bugfix。")
    lines.append("朱辉说「切换到某分支」会真切工作区；别人说不会切。")
    return "\n".join(lines)


def fetch_origin_branch(git_root: str, branch: str) -> tuple:
    """fetch origin <branch>。返回 (ok, tip_hash_or_error, note)。"""
    ref = git_ref(branch)
    ok, output = run_git_readonly(git_root, ["fetch", GIT_REMOTE, branch])
    if not ok:
        return False, output, f"fetch {GIT_REMOTE} {branch} 失败，将尽量用本地已有的 {ref}"
    tip_ok, tip = run_git_readonly(git_root, ["rev-parse", "--short", ref])
    return True, (tip if tip_ok else ""), f"已同步 {ref}"


def get_remote_url(git_root: str) -> str:
    ok, output = run_git_readonly(git_root, ["remote", "get-url", GIT_REMOTE])
    return output if ok else ""


PROJECT_ADDRESS_PATTERN = re.compile(
    r"(项目|工程|仓库|代码|git).{0,4}(地址|链接|url|URL|在哪拉|从哪拉|怎么拉)|(拉|clone).{0,4}(项目|工程|代码)", re.IGNORECASE)


PULL_PATTERN = re.compile(
    r"拉新|拉(一下|下)?最新|拉一下(代码|项目|工程)?|更新(一下)?(代码|项目|工程)|git\s*pull|(?<![A-Za-z])pull(?![A-Za-z])",
    re.IGNORECASE)
PULL_OWNER_OPEN_IDS = ("ou_c37360dac36cf63d325710343fc960c7",)
PULL_TIMEOUT = 900
_pull_lock = threading.Lock()


def is_pull_request(text: str) -> bool:
    text = text or ""
    if is_pull_schedule_request(text):
        return False
    if not PULL_PATTERN.search(text) or extract_git_path_token(text):
        return False
    return not re.search(r"哪|怎么|地址|clone|链接", text, re.IGNORECASE)


PULL_SCHEDULE_PATH = os.path.join(DATA_DIR, "pull_schedule.json")
PULL_SCHEDULE_MIN_SEC = 3600
PULL_SCHEDULE_MAX_SEC = 7 * 24 * 3600
PULL_SCHEDULE_POLL_SEC = 20
_PULL_INTERVAL_PATTERN = re.compile(r"(?:每隔|间隔|每)\s*(\d+)\s*(小时|分钟)")
_schedule_lock = threading.Lock()
_schedule_wakeup = threading.Event()


def is_pull_schedule_request(text: str) -> bool:
    text = text or ""
    if not re.search(r"拉新|拉取|拉一下|(?<![A-Za-z])pull(?![A-Za-z])", text, re.IGNORECASE):
        return False
    if re.search(r"定时", text):
        return True
    return bool(re.search(r"每隔|间隔", text) and _PULL_INTERVAL_PATTERN.search(text))


def parse_pull_interval(text: str) -> int:
    match = _PULL_INTERVAL_PATTERN.search(text or "")
    if not match:
        return 0
    value = int(match.group(1))
    return value * 3600 if match.group(2) == "小时" else value * 60


def format_pull_interval(seconds: int) -> str:
    if seconds > 0 and seconds % 3600 == 0:
        return f"{seconds // 3600} 小时"
    return f"{max(seconds, 0) // 60} 分钟"


def load_pull_schedule() -> dict:
    try:
        with open(PULL_SCHEDULE_PATH, encoding="utf-8") as file:
            saved = json.load(file)
    except (OSError, ValueError):
        return {}
    return saved if isinstance(saved, dict) else {}


def write_pull_schedule_file(schedule: dict) -> None:
    temporary = PULL_SCHEDULE_PATH + ".tmp"
    with open(temporary, "w", encoding="utf-8", newline="\n") as file:
        json.dump(schedule, file, ensure_ascii=False, indent=2)
        file.write("\n")
    os.replace(temporary, PULL_SCHEDULE_PATH)


def save_pull_schedule(schedule: dict, wake: bool = True) -> None:
    with _schedule_lock:
        write_pull_schedule_file(schedule)
    if wake:
        _schedule_wakeup.set()


def parse_schedule_time(value) -> datetime.datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=BEIJING_TZ)
    return parsed


def plan_pull_next(enabled: bool, old_interval: int, new_interval: int, old_next, now: datetime.datetime):
    """关着就保留原时间。开着时，间隔变了或下次时间已过，就从现在重新算。"""
    if not enabled:
        return old_next
    if old_interval != new_interval or old_next is None or old_next <= now:
        return now + datetime.timedelta(seconds=new_interval)
    return old_next


def pull_schedule_public(schedule: dict | None = None) -> dict:
    schedule = load_pull_schedule() if schedule is None else schedule
    interval = int(schedule.get("interval_sec") or 0)
    hours = (interval // 3600) if interval and interval % 3600 == 0 else (interval / 3600 if interval else 5)
    return {
        "enabled": bool(schedule.get("enabled")),
        "hours": hours,
        "next_at": str(schedule.get("next_at") or "").replace("T", " ")[:16],
        "run_requested": bool(schedule.get("run_requested")),
        "last_at": str(schedule.get("last_at") or "").replace("T", " ")[:16],
        "last_result": str(schedule.get("last_result") or "")[:400],
    }


def control_pull_schedule(enabled: bool, hours: int, run_now: bool = False) -> dict:
    if not isinstance(hours, int) or not 1 <= hours <= 168:
        raise ValueError("间隔要在 1 到 168 小时之间。")
    now = datetime.datetime.now(BEIJING_TZ)
    with _schedule_lock:
        current = load_pull_schedule()
        old_interval = int(current.get("interval_sec") or 0)
        new_interval = hours * 3600
        planned = plan_pull_next(bool(enabled), old_interval, new_interval, parse_schedule_time(current.get("next_at")), now)
        current["enabled"] = bool(enabled)
        current["interval_sec"] = new_interval
        if planned is not None:
            current["next_at"] = planned.isoformat(timespec="seconds")
        if not current.get("sender"):
            current["sender"] = PULL_OWNER_OPEN_IDS[0]
        if run_now:
            current["run_requested"] = True
        current["revision"] = int(current.get("revision") or 0) + 1
        write_pull_schedule_file(current)
    _schedule_wakeup.set()
    return pull_schedule_public(current)


def pull_schedule_status(schedule: dict) -> str:
    if not schedule.get("enabled"):
        return "定时拉新关着。要开就说：定时拉新 每隔5小时 拉新一次。"
    interval = format_pull_interval(int(schedule.get("interval_sec") or 0))
    when = str(schedule.get("next_at") or "").replace("T", " ")[:16]
    return f"定时拉新开着，每 {interval} 一次。下次 {when}。说「取消定时拉新」就停。"


def answer_pull_schedule(text: str, sender: str, chat_id: str) -> str:
    if sender not in PULL_OWNER_OPEN_IDS:
        return "定时拉代码只认朱辉本人发。"
    if re.search(r"取消|停止|关掉|关闭|不要|别", text or ""):
        current = load_pull_schedule()
        current["enabled"] = False
        current["run_requested"] = False
        current["revision"] = int(current.get("revision") or 0) + 1
        save_pull_schedule(current)
        return "定时拉新已关掉。"
    seconds = parse_pull_interval(text)
    if seconds <= 0:
        return pull_schedule_status(load_pull_schedule())
    if seconds < PULL_SCHEDULE_MIN_SEC or seconds > PULL_SCHEDULE_MAX_SEC:
        return "间隔要在 1 小时到 7 天之间。例如：定时拉新 每隔5小时 拉新一次。"
    now = datetime.datetime.now(BEIJING_TZ)
    current = load_pull_schedule()
    current.update({
        "enabled": True,
        "interval_sec": seconds,
        "chat_id": chat_id,
        "sender": sender,
        "next_at": (now + datetime.timedelta(seconds=seconds)).isoformat(timespec="seconds"),
        "run_requested": False,
        "revision": int(current.get("revision") or 0) + 1,
    })
    save_pull_schedule(current)
    when = (now + datetime.timedelta(seconds=seconds)).strftime("%Y-%m-%d %H:%M")
    return (f"已设定：每 {format_pull_interval(seconds)} 拉一次当前分支。下次 {when}。"
            "只快进，有本地修改会先清掉。结果私聊朱辉。说「取消定时拉新」就停。")


def send_owner_pull(text: str) -> None:
    if not text:
        return
    owner = PULL_OWNER_OPEN_IDS[0]
    try:
        stdout = run_cli(["im", "+messages-send", "--as", "bot", "--user-id", owner, "--markdown", redact(text),
                          "--idempotency-key", f"pull-{int(time.time())}"])
    except (RuntimeError, subprocess.TimeoutExpired) as error:
        log(f"[pull] dm failed {error}")
        return
    sent_id = sent_message_id(stdout, "")
    if sent_id:
        SENT_TEXT[sent_id] = (text or "")[:HISTORY_CHARS]
    log(f"[pull] dm owner id={sent_id or '-'}")


def schedule_due(schedule: dict, now: datetime.datetime) -> bool:
    if schedule.get("run_requested"):
        return True
    if not schedule.get("enabled"):
        return False
    next_at = parse_schedule_time(schedule.get("next_at"))
    return next_at is not None and next_at <= now


def pull_schedule_loop() -> None:
    logged = ""
    while True:
        schedule = load_pull_schedule()
        now = datetime.datetime.now(BEIJING_TZ)
        if not schedule_due(schedule, now):
            shown = str(schedule.get("next_at") or "").replace("T", " ")[:16] if schedule.get("enabled") else ""
            if shown != logged:
                logged = shown
                log(f"[pull-schedule] next {shown}" if shown else "[pull-schedule] off")
            if _schedule_wakeup.wait(PULL_SCHEDULE_POLL_SEC):
                _schedule_wakeup.clear()
            continue
        logged = ""
        revision = int(schedule.get("revision") or 0)
        requested = bool(schedule.get("run_requested"))
        sender = str(schedule.get("sender") or "") or PULL_OWNER_OPEN_IDS[0]
        result = answer_pull(sender)
        if result.startswith("正在拉"):
            log("[pull-schedule] busy, retry in 120s")
            time.sleep(120)
            continue
        interval = int(schedule.get("interval_sec") or PULL_SCHEDULE_MIN_SEC)
        following = datetime.datetime.now(BEIJING_TZ) + datetime.timedelta(seconds=interval)
        with _schedule_lock:
            current = load_pull_schedule()
            current["last_result"] = (result or "")[:400]
            current["last_at"] = datetime.datetime.now(BEIJING_TZ).isoformat(timespec="seconds")
            if int(current.get("revision") or 0) == revision:
                if current.get("enabled"):
                    current["next_at"] = following.isoformat(timespec="seconds")
                if requested:
                    current["run_requested"] = False
            write_pull_schedule_file(current)
        title = "后台拉新" if requested else "定时拉新"
        log(f"[pull-schedule] ran next={following:%Y-%m-%d %H:%M}")
        send_owner_pull(f"{title}：\n{result}")


WATCH_PATH = os.path.join(DATA_DIR, "watch.json")
WATCH_MIN_SEC = 5 * 60
WATCH_MAX_SEC = 24 * 3600
WATCH_DEFAULT_SEC = 30 * 60
WATCH_MIN_DAYS = 1
WATCH_MAX_DAYS = 7
WATCH_DEFAULT_DAYS = 1
WATCH_DEFAULT_RULES = ({"path": "Assets/Bundle/Shader", "exts": [".shader", ".hlsl"]},)
_watch_lock = threading.Lock()


def watch_rules_text(rules: list) -> str:
    lines = []
    for rule in rules or []:
        if not isinstance(rule, dict):
            continue
        path = str(rule.get("path") or "").strip()
        exts = [str(ext) for ext in rule.get("exts") or [] if str(ext).strip()]
        if path and exts:
            lines.append(path + " " + " ".join(exts))
    return "\n".join(lines)


def parse_watch_rules(text: str) -> list:
    rules = []
    for raw in (text or "").splitlines():
        parts = raw.split()
        if not parts:
            continue
        path = parts[0].replace("\\", "/").strip("/")
        if not path or ":" in path or any(part == ".." for part in path.split("/")):
            raise ValueError(f"目录不合法：{parts[0]}")
        if len(parts) < 2:
            raise ValueError(f"「{path}」要带上后缀，例如 .shader .hlsl")
        exts = []
        for ext in parts[1:]:
            ext = ext.lower() if str(ext).startswith(".") else "." + str(ext).lower()
            if not re.fullmatch(r"\.[a-z0-9]{1,8}", ext):
                raise ValueError(f"后缀不合法：{ext}")
            if ext not in exts:
                exts.append(ext)
        rules.append({"path": path, "exts": exts})
    if not rules:
        raise ValueError("至少写一行：目录 后缀，例如 Assets/Bundle/Shader .shader .hlsl")
    if len(rules) > 8:
        raise ValueError("最多 8 个目录。")
    return rules


def clamp_watch_days(value) -> int:
    try:
        days = int(value)
    except (TypeError, ValueError):
        return WATCH_DEFAULT_DAYS
    return min(max(days, WATCH_MIN_DAYS), WATCH_MAX_DAYS)


def watch_report_full(run_now: bool, told_days, days: int) -> bool:
    """手动查，或天数变了，要列出这段时间里的全部提交。定时重复查才跳过已经报过的。"""
    if run_now:
        return True
    try:
        previous = int(told_days)
    except (TypeError, ValueError):
        return True
    return previous != days


def default_watch() -> dict:
    now = datetime.datetime.now(BEIJING_TZ)
    return {
        "enabled": True,
        "interval_sec": WATCH_DEFAULT_SEC,
        "lookback_days": WATCH_DEFAULT_DAYS,
        "rules": [dict(item) for item in WATCH_DEFAULT_RULES],
        "seen": {},
        "told": {},
        "next_at": now.isoformat(timespec="seconds"),
        "revision": 1,
    }


def load_watch() -> dict:
    try:
        with open(WATCH_PATH, encoding="utf-8") as file:
            saved = json.load(file)
    except (OSError, ValueError):
        return {}
    return saved if isinstance(saved, dict) else {}


def write_watch_file(schedule: dict) -> None:
    temporary = WATCH_PATH + ".tmp"
    with open(temporary, "w", encoding="utf-8", newline="\n") as file:
        json.dump(schedule, file, ensure_ascii=False, indent=2)
        file.write("\n")
    os.replace(temporary, WATCH_PATH)


def watch_public(schedule: dict | None = None) -> dict:
    schedule = load_watch() if schedule is None else schedule
    interval = int(schedule.get("interval_sec") or WATCH_DEFAULT_SEC)
    rules = schedule.get("rules") if isinstance(schedule.get("rules"), list) else list(WATCH_DEFAULT_RULES)
    return {
        "enabled": bool(schedule.get("enabled")),
        "minutes": max(interval // 60, 1),
        "days": clamp_watch_days(schedule.get("lookback_days")),
        "rules_text": watch_rules_text(rules),
        "next_at": str(schedule.get("next_at") or "").replace("T", " ")[:16],
        "run_requested": bool(schedule.get("run_requested")),
        "last_at": str(schedule.get("last_at") or "").replace("T", " ")[:16],
        "last_result": str(schedule.get("last_result") or "")[:500],
        "seen": bool(schedule.get("seen")),
    }


def control_watch(enabled: bool, minutes: int, rules_text: str, run_now: bool = False, days: int = WATCH_DEFAULT_DAYS) -> dict:
    if not isinstance(minutes, int) or not 5 <= minutes <= 1440:
        raise ValueError("间隔要在 5 到 1440 分钟之间。")
    if isinstance(days, bool) or not isinstance(days, int) or not WATCH_MIN_DAYS <= days <= WATCH_MAX_DAYS:
        raise ValueError("天数要在 1 到 7 之间。")
    rules = parse_watch_rules(rules_text)
    now = datetime.datetime.now(BEIJING_TZ)
    with _watch_lock:
        current = load_watch() or default_watch()
        old_interval = int(current.get("interval_sec") or 0)
        new_interval = minutes * 60
        planned = plan_pull_next(bool(enabled), old_interval, new_interval, parse_schedule_time(current.get("next_at")), now)
        current["enabled"] = bool(enabled)
        current["interval_sec"] = new_interval
        current["lookback_days"] = days
        current["rules"] = rules
        if planned is not None:
            current["next_at"] = planned.isoformat(timespec="seconds")
        if run_now:
            current["run_requested"] = True
        current["revision"] = int(current.get("revision") or 0) + 1
        write_watch_file(current)
    return watch_public(current)


def watch_hit(path: str, rules: list) -> bool:
    normal = (path or "").replace("\\", "/").strip().strip('"').lstrip("./")
    lowered = normal.lower()
    for rule in rules or []:
        if not isinstance(rule, dict):
            continue
        folder = str(rule.get("path") or "").replace("\\", "/").strip("/").lower()
        exts = [str(ext).lower() for ext in rule.get("exts") or []]
        if not folder or not exts:
            continue
        inside = lowered == folder or lowered.startswith(folder + "/") or ("/" + folder + "/") in ("/" + lowered)
        if inside and any(lowered.endswith(ext) for ext in exts):
            return True
    return False


def parse_name_log(text: str) -> list:
    commits = []
    current = None
    for line in (text or "").splitlines():
        if line.startswith("--") and "\x1f" in line:
            if current:
                commits.append(current)
            parts = line[2:].split("\x1f", 3)
            current = {
                "hash": parts[0] if parts else "",
                "author": parts[1] if len(parts) > 1 else "",
                "date": parts[2] if len(parts) > 2 else "",
                "subject": parts[3] if len(parts) > 3 else "",
                "files": [],
            }
            continue
        if current and line.strip():
            current["files"].append(line.strip().strip('"').replace("\\", "/"))
    if current:
        commits.append(current)
    return commits


def format_watch_commits(branch: str, commits: list, rules: list, shown_hashes: set | None = None) -> str:
    shown = []
    for commit in commits or []:
        digest = str(commit.get("hash") or "").lower()
        if shown_hashes is not None and digest and digest in shown_hashes:
            continue
        files = [path for path in commit.get("files") or [] if watch_hit(path, rules)]
        if not files:
            continue
        shown.append((commit, files))
        if shown_hashes is not None and digest:
            shown_hashes.add(digest)
    if not shown:
        return ""
    lines = [f"`{GIT_REMOTE}/{branch}`："]
    extra = len(shown) - 8
    for commit, files in shown[:8]:
        lines.append(f"- `{commit.get('hash')}` {commit.get('date')} {commit.get('author')}")
        subject = str(commit.get("subject") or "").strip()
        if subject:
            lines.append(f"  {subject}")
        more = len(files) - 20
        for path in files[:20]:
            lines.append(f"  {path}")
        if more > 0:
            lines.append(f"  ……另外 {more} 个文件")
    if extra > 0:
        lines.append(f"另外还有 {extra} 个提交。")
    return "\n".join(lines)


def watch_title(days: int) -> str:
    return f"盯目录 · 最近 {days} 天"


def send_owner_watch(text: str, key: str, title: str = "") -> bool:
    if not text:
        return True
    owner = PULL_OWNER_OPEN_IDS[0]
    try:
        if title:
            payload = {"zh_cn": {"title": title, "content": [[{"tag": "md", "text": redact(text)[:3500]}]]}}
            run_cli(["im", "+messages-send", "--as", "bot", "--user-id", owner, "--msg-type", "post",
                     "--content", json.dumps(payload, ensure_ascii=False), "--idempotency-key", key[:50]])
        else:
            run_cli(["im", "+messages-send", "--as", "bot", "--user-id", owner, "--markdown", redact(text)[:3500],
                     "--idempotency-key", key[:50]])
    except (RuntimeError, subprocess.TimeoutExpired) as error:
        log(f"[watch] dm failed {error}")
        return False
    log("[watch] dm owner")
    return True


def send_chat_post(chat_id: str, text: str, key: str, title: str = "") -> bool:
    if not text or not chat_id:
        return True
    try:
        if title:
            payload = {"zh_cn": {"title": title, "content": [[{"tag": "md", "text": redact(text)[:3500]}]]}}
            run_cli(["im", "+messages-send", "--as", "bot", "--chat-id", chat_id, "--msg-type", "post",
                     "--content", json.dumps(payload, ensure_ascii=False), "--idempotency-key", key[:50]])
        else:
            run_cli(["im", "+messages-send", "--as", "bot", "--chat-id", chat_id, "--markdown", redact(text)[:3500],
                     "--idempotency-key", key[:50]])
    except (RuntimeError, subprocess.TimeoutExpired) as error:
        log(f"[group] send failed chat={chat_id} {error}")
        return False
    log(f"[group] sent chat={chat_id}")
    return True


CHAT_ID_PATTERN = re.compile(r"^oc_[0-9a-f]{32}$")


def parse_chat_ids(text) -> tuple:
    """后台填的群 ID，一行一个或逗号分隔。返回 (合法的去重列表, 不合法的)。"""
    items = text if isinstance(text, list) else re.split(r"[\s,，;；]+", str(text or ""))
    good = []
    bad = []
    for item in items:
        item = str(item).strip()
        if not item:
            continue
        if CHAT_ID_PATTERN.match(item):
            if item not in good:
                good.append(item)
        else:
            bad.append(item)
    return good, bad


def send_groups(chat_ids: list, text: str, key: str, title: str) -> int:
    """发到多个群，返回发成功的群数。幂等键按群拆开，避免多群共用同一 key。"""
    sent = 0
    for chat_id in chat_ids:
        suffix = hashlib.sha1(chat_id.encode("utf-8")).hexdigest()[:6]
        if send_chat_post(chat_id, text, f"{key}-{suffix}"[:50], title):
            sent += 1
    return sent


BOMB_JUDGE_MAX = 6          # 一次最多让 Codex 判断几次合并，剩下的下回再判
BOMB_JUDGE_TIMEOUT = 300    # 单次判断超时（秒）；Codex 可能要自己跑几条 git 命令
BOMB_JUDGED_KEY = "judged_v2"  # v1 判断时没给到 diff 和提交号，结论多是「看不出」，作废重判
BOMB_JUDGED_KEEP = 300


def judge_one_merge(branch: str, item: dict) -> dict:
    """让 Codex 看丢掉那侧的 diff，返回 {路径: "像是误丢：理由"}。失败返回空。"""
    material = bomb.judge_material(bomb_git, item, branch)
    name = codex_cli.label()
    try:
        answer, usage = codex_cli.ask([{"role": "system", "content": bomb.JUDGE_PROMPT},
                                       {"role": "user", "content": redact(material)}], timeout=BOMB_JUDGE_TIMEOUT,
                                      project_dir=get_git_root(), branches=list(GIT_BRANCHES))
    except (codex_cli.CodexError, codex_cli.CodexTimeout, OSError) as error:
        model_stats.note(name, False, redact(f"{type(error).__name__}: {error}"))
        log(f"[bomb] judge {item.get('merge')} failed {type(error).__name__}: {str(error)[:160]}")
        return {}
    model_stats.note(name, True, prompt_tokens=int(usage.get("input_tokens") or 0),
                     completion_tokens=int(usage.get("output_tokens") or 0))
    verdicts = bomb.parse_judgement(answer, [detail["path"] for detail in item.get("files") or []])
    log(f"[bomb] judge {item.get('merge')} files={len(verdicts)}")
    return verdicts


def judge_bomb_results(results: list) -> int:
    """给结果里每个文件填 detail["judge"]（Codex 判断）。判过的存在 bomb.json 的 judged_v2 和看板记录里，
    下次直接用，不再调 Codex。
    没装 Codex 时什么都不做。返回这次新判断的合并数。"""
    if not USE_CODEX:
        return 0
    cache = load_bomb_state().get(BOMB_JUDGED_KEY) or {}
    todo = []
    for branch, merges, _forced in results:
        for item in merges:
            cached = cache.get(item["merge"])
            files = item.get("files") or []
            if cached is None and files and all(detail.get("judge") for detail in files):
                continue  # 看板记录里已经有判断（缓存被清掉也不重判）
            if cached is None and len(todo) < BOMB_JUDGE_MAX and files:
                todo.append((branch, item))
    fresh = {}
    if todo:
        with ThreadPoolExecutor(max_workers=2) as pool:
            for (branch, item), verdicts in zip(todo, pool.map(lambda pair: judge_one_merge(*pair), todo)):
                if verdicts:
                    fresh[item["merge"]] = verdicts
    if fresh:
        with _bomb_lock:
            state = load_bomb_state()
            merged = dict(state.get(BOMB_JUDGED_KEY) or {})
            merged.update(fresh)
            state[BOMB_JUDGED_KEY] = dict(list(merged.items())[-BOMB_JUDGED_KEEP:])
            state.pop("judged", None)
            save_bomb_state(state)
        for merge, verdicts in fresh.items():
            try:
                bomb_board.set_judges(merge, verdicts)
            except Exception as error:
                log(f"[bomb] judge save failed {type(error).__name__}: {error}")
        cache = dict(cache, **fresh)
    for _branch, merges, _forced in results:
        for item in merges:
            verdicts = cache.get(item["merge"]) or {}
            for detail in item.get("files") or []:
                if detail["path"] in verdicts:
                    detail["judge"] = verdicts[detail["path"]]
    return len(fresh)


def bomb_people() -> dict:
    """炸线卡片用：自检名单里的 git 作者 → 真名、open_id（有 open_id 就在卡片里 @ 他）。"""
    people = {}
    for sub in load_review_subs():
        open_id = str(sub.get("open_id") or "")
        people[str(sub["git_author"]).lower()] = {
            "name": str(sub.get("name") or "").strip(), "open_id": open_id if open_id.startswith("ou_") else ""}
    return people


def send_card(target_flag: str, target: str, card: dict, key: str, label: str) -> bool:
    try:
        run_cli(["im", "+messages-send", "--as", "bot", target_flag, target, "--msg-type", "interactive",
                 "--content", card_content(card), "--idempotency-key", key[:50]])
    except (RuntimeError, subprocess.TimeoutExpired) as error:
        log(f"[{label}] card failed {target} {error}")
        return False
    log(f"[{label}] card sent {target}")
    return True


def send_bomb_alert(card: dict, key: str) -> bool:
    """私聊朱辉，并同步发到后台配的炸线提醒群（交互卡片，带表格）。任一成功就算发出，避免单边失败导致每半小时重报。"""
    if not card:
        return True
    dm_ok = send_card("--user-id", PULL_OWNER_OPEN_IDS[0], card, f"bomb-dm-{key}", "bomb")
    chat_ok = False
    for chat_id in bomb_groups(load_bomb_state()):
        suffix = hashlib.sha1(chat_id.encode("utf-8")).hexdigest()[:6]
        chat_ok = send_card("--chat-id", chat_id, card, f"bomb-g-{key}-{suffix}", "bomb") or chat_ok
    return dm_ok or chat_ok


def unseen_watch_commits(commits: list, told: set) -> list:
    fresh = []
    for commit in commits or []:
        if str(commit.get("hash") or "") not in told:
            fresh.append(commit)
    return fresh


def watch_pathspecs(rules: list) -> list:
    specs = []
    for rule in rules or []:
        if not isinstance(rule, dict):
            continue
        folder = str(rule.get("path") or "").replace("\\", "/").strip("/")
        if not folder:
            continue
        for spec in (folder, "Client/" + folder):
            if spec not in specs:
                specs.append(spec)
    return specs


def _watch_sha(git_root: str, branch: str) -> str:
    ok, output = run_git_readonly(git_root, ["rev-parse", f"{GIT_REMOTE}/{branch}"])
    sha = (output or "").strip()
    if ok and re.fullmatch(r"[0-9a-fA-F]{7,40}", sha):
        return sha.lower()
    return ""


def scan_watch_branch(git_root: str, branch: str, rules: list, since: datetime.datetime, told: set, shown_hashes: set | None = None) -> tuple:
    """返回 (新提交, 通知正文, 错误, 这次要记过的提交号)。只看 since 之后还没报过的提交。"""
    fetched, fetch_error = run_git_readonly(git_root, ["fetch", GIT_REMOTE, branch])
    if not fetched:
        return "", "", fetch_error or "fetch 失败", []
    new_sha = _watch_sha(git_root, branch)
    if not new_sha:
        return "", "", f"读不到 {GIT_REMOTE}/{branch}", []
    since_text = since.strftime("%Y-%m-%d %H:%M:%S")
    ok, log_text = run_git_readonly(git_root, [
        "log", f"{GIT_REMOTE}/{branch}", f"--since={since_text}", "--name-only",
        "--date=format:%Y-%m-%d %H:%M",
        "--pretty=format:--%h%x1f%an%x1f%ad%x1f%s",
        "--", *watch_pathspecs(rules),
    ])
    if not ok:
        return new_sha, "", f"{branch} 的提交记录读失败。", []
    commits = unseen_watch_commits(parse_name_log(log_text), told)
    hashes = []
    for commit in commits:
        if commit.get("hash") and any(watch_hit(path, rules) for path in commit.get("files") or []):
            hashes.append(str(commit["hash"]))
    notice = format_watch_commits(branch, commits, rules, shown_hashes)
    return new_sha, notice, "", hashes


def run_watch(schedule: dict, full: bool = False) -> tuple:
    """返回 (seen, told, 标题, 给朱辉的正文, 记在后台的结果, 幂等键)。full 时不跳过已经报过的提交。"""
    git_root = get_git_root()
    rules = schedule.get("rules") if isinstance(schedule.get("rules"), list) else []
    seen = dict(schedule.get("seen") or {})
    raw_told = schedule.get("told") if isinstance(schedule.get("told"), dict) else {}
    told = {}
    for branch in GIT_BRANCHES:
        items = raw_told.get(branch) or []
        told[branch] = [str(item) for item in items if str(item)]
    days = clamp_watch_days(schedule.get("lookback_days"))
    since = datetime.datetime.now(BEIJING_TZ) - datetime.timedelta(days=days)
    notices = []
    notes = []
    shown_hashes = set()
    for branch in GIT_BRANCHES:
        known = set() if full else set(told.get(branch) or [])
        new_sha, notice, error, hashes = scan_watch_branch(git_root, branch, rules, since, known, shown_hashes)
        if new_sha:
            seen[branch] = new_sha
        if error:
            notes.append(error)
            continue
        if full:
            told[branch] = hashes[-200:]
        elif hashes:
            merged = list(dict.fromkeys((told.get(branch) or []) + hashes))
            told[branch] = merged[-200:]
        if notice:
            notices.append(notice)
    title = ""
    if notices:
        title = watch_title(days)
        body = "\n".join(notices)
        result = title + "\n" + (body if not notes else body + "\n" + "\n".join(notes))
    elif notes:
        result = "\n".join(notes)[:500]
        body = ""
    else:
        result = f"最近 {days} 天没有新的匹配文件。"
        body = ""
    prefix = "watch-full-" if full else "watch-"
    digest = hashlib.sha1((title + "\n" + body).encode("utf-8")).hexdigest()[:12] if body else "empty"
    key = (prefix + digest)[:50]
    return seen, told, title, body, result[:500], key


def watch_loop() -> None:
    while True:
        if not load_watch():
            with _watch_lock:
                if not load_watch():
                    write_watch_file(default_watch())
        schedule = load_watch()
        now = datetime.datetime.now(BEIJING_TZ)
        if not schedule_due(schedule, now):
            time.sleep(PULL_SCHEDULE_POLL_SEC)
            continue
        revision = int(schedule.get("revision") or 0)
        requested = bool(schedule.get("run_requested"))
        days = clamp_watch_days(schedule.get("lookback_days"))
        full = watch_report_full(requested, schedule.get("told_days"), days)
        try:
            seen, told, title, body, result, key = run_watch(schedule, full)
        except Exception as error:
            log(f"[watch] failed {error}")
            seen, told, title, body, result, key = dict(schedule.get("seen") or {}), dict(schedule.get("told") or {}), "", "", str(error)[:200], ""
        interval = int(schedule.get("interval_sec") or WATCH_DEFAULT_SEC)
        following = datetime.datetime.now(BEIJING_TZ) + datetime.timedelta(seconds=interval)
        sent = True
        if body:
            sent = send_owner_watch(body, key or f"watch-{int(time.time())}", title)
        with _watch_lock:
            current = load_watch()
            if sent:
                current["seen"] = seen
                current["told"] = told
                if full:
                    current["told_days"] = days
            current["last_result"] = result
            current["last_at"] = datetime.datetime.now(BEIJING_TZ).isoformat(timespec="seconds")
            if int(current.get("revision") or 0) == revision:
                if current.get("enabled"):
                    current["next_at"] = following.isoformat(timespec="seconds")
                if requested and sent:
                    current["run_requested"] = False
            write_watch_file(current)
        log(f"[watch] ran sent={sent} next={following:%Y-%m-%d %H:%M}")


BOMB_PATH = os.path.join(DATA_DIR, "bomb.json")
BOMB_INTERVAL_SEC = 1800
BOMB_LOOP_DAYS = 2
BOMB_TOLD_KEEP = 500
BOMB_TITLE = "疑似炸线"
# 疑似炸线自动提醒：私聊朱辉之外，同步发到这些群。
BOMB_ALERT_CHAT_IDS = (
    "oc_87199fa15e8bc352fb9c928332d8528e",
    "oc_d7aa3ebf93e3bf0dc5a1b4b6c3459737",
)


BOMB_DIFF_HASH = re.compile(r"[0-9a-f]{7,40}")


def bomb_diff_allowed(args: list) -> bool:
    """炸线判断只放行一种 diff：diff --no-color -U2 <哈希> <哈希> -- <仓库内路径>。不写文件、不读仓库外。"""
    if len(args) != 7 or args[:3] != ["diff", "--no-color", "-U2"] or args[5] != "--":
        return False
    path = args[6]
    return bool(BOMB_DIFF_HASH.fullmatch(args[3]) and BOMB_DIFF_HASH.fullmatch(args[4]) and path and not path.startswith(("-", "/", ":"))
                and ".." not in path.split("/"))


def bomb_git(args: list) -> tuple:
    if args and args[0] == "diff":
        if not bomb_diff_allowed(args):
            return False, "仅支持只读 git 查询。"
        try:
            result = subprocess.run(["git", "-C", get_git_root()] + args, capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=GIT_TIMEOUT, creationflags=NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired) as error:
            return False, str(error)
        return result.returncode == 0, result.stdout if result.returncode == 0 else (result.stderr or "")[:500]
    return run_git_readonly(get_git_root(), args)


def bomb_since(days: int) -> str:
    now = datetime.datetime.now(BEIJING_TZ)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0) if days == 0 else now - datetime.timedelta(days=days)
    return start.isoformat(timespec="seconds")


def scan_bomb(branches: list, days: int, bases: dict = None, tips: dict = None) -> tuple:
    """返回 [(分支, 合并列表, 强推列表)], 查过的合并数, 出错说明。

    bases={分支: 上次扫到的最新提交} 时只查那之后新进来的合并；tips 传一个空 dict 进来，会填上这次各分支的最新提交。"""
    git_root = get_git_root()
    results = []
    scanned = 0
    notes = []
    for branch in branches:
        fetch_ok, _tip, sync_note = fetch_origin_branch(git_root, branch)
        if not fetch_ok:
            notes.append(sync_note)
        ref = git_ref(branch)
        merges, count = bomb.scan_merges(bomb_git, ref, bomb_since(days), (bases or {}).get(branch, ""))
        if count < 0:
            notes.append(f"{branch} 的合并记录没查到，下次从原位置再查")
        elif tips is not None:
            tips[branch] = bomb.tip_of(bomb_git, ref)
        scanned += max(count, 0)
        results.append((branch, merges, bomb.scan_forced(bomb_git, ref)))
    return results, scanned, "；".join(notes)


def bomb_ignore_paths(state: dict = None) -> list:
    """提醒忽略的路径前缀。状态里没配过时用默认（BattleLibrary/output、Assets/DataTable）。"""
    payload = state if state is not None else load_bomb_state()
    if "ignore_paths" not in payload:
        return list(bomb.DEFAULT_IGNORE_PATHS)
    return bomb.parse_ignore_paths(payload.get("ignore_paths") or [])


def alert_bomb_results(results: list, ignore_paths: list = None) -> tuple:
    """提醒/口头回答用：合并按忽略路径过滤；强推原样。看板仍用原始 results。"""
    prefixes = ignore_paths if ignore_paths is not None else bomb_ignore_paths()
    alerted = []
    dropped = 0
    for branch, merges, forced in results:
        kept, lost = bomb.filter_merges_for_alert(merges, prefixes)
        dropped += lost
        if kept or forced:
            alerted.append((branch, kept, forced))
    return alerted, dropped


def answer_bomb(text: str) -> str:
    days = bomb.parse_days(text)
    branches = bomb.parse_branches(text, list(GIT_BRANCHES))
    label = "今天" if days == 0 else ("昨天到现在" if days == 1 else f"最近 {days} 天")
    log(f"[bomb] branches={branches} days={days}")
    results, scanned, note = scan_bomb(branches, days)
    record_bomb_history(results, load_bomb_state().get("told"))
    ignore_paths = bomb_ignore_paths()
    alerted, dropped = alert_bomb_results(results, ignore_paths)
    alerted = [(branch, merges, forced) for branch, merges, forced in alerted if merges or forced]
    judge_bomb_results(alerted)
    reports = [bomb.format_report(branch, merges, forced) for branch, merges, forced in alerted]
    names = "、".join(branches)
    tail = f"\n（{note}）" if note else ""
    if dropped:
        tail += f"\n（提醒已忽略 {dropped} 个自动上传目录下的文件，完整结果看炸线看板）"
    if not reports:
        empty = f"{label} {names} 没查到疑似炸线，查了 {scanned} 个合并，也没有强推记录。"
        if dropped:
            empty = (f"{label} {names} 查了 {scanned} 个合并，提醒侧没有需要报的"
                     f"（忽略了 {dropped} 个自动上传目录下的文件），完整结果看炸线看板。")
        return f"{empty}{tail}".rstrip()
    intro = f"{label} {names} 查了 {scanned} 个合并："
    text = f"{intro}\n\n" + "\n\n".join(reports) + f"\n\n{bomb.BOMB_NOTE}{tail}"
    card = bomb.build_card(BOMB_TITLE, intro, alerted, bomb.BOMB_NOTE + tail.replace("\n", " "), bomb_people())
    return CardReply.make(text, card)


def load_bomb_state() -> dict:
    try:
        with open(BOMB_PATH, encoding="utf-8") as state_file:
            state = json.load(state_file)
    except (OSError, ValueError):
        return {"told": []}
    return state if isinstance(state, dict) else {"told": []}


def save_bomb_state(state: dict) -> None:
    temporary = BOMB_PATH + ".tmp"
    with open(temporary, "w", encoding="utf-8", newline="\n") as state_file:
        json.dump(state, state_file, ensure_ascii=False, indent=2)
    os.replace(temporary, BOMB_PATH)


BOMB_POLL_SEC = 60
BOMB_RECENT_KEEP = 20
_bomb_lock = threading.Lock()


def bomb_settings(state: dict) -> tuple:
    """返回 (开着没, 间隔分钟, 扫几天)。旧状态文件没有这些字段时按默认值。"""
    enabled = state.get("enabled", True) is not False
    try:
        minutes = min(max(int(state.get("minutes") or BOMB_INTERVAL_SEC // 60), 5), 1440)
    except (TypeError, ValueError):
        minutes = BOMB_INTERVAL_SEC // 60
    try:
        days = min(max(int(state.get("days") or BOMB_LOOP_DAYS), 1), bomb.MAX_DAYS)
    except (TypeError, ValueError):
        days = BOMB_LOOP_DAYS
    return enabled, minutes, days


def bomb_groups(state: dict) -> list:
    """炸线提醒群。状态文件里没配过时用代码里的默认群。"""
    if "groups" not in state:
        return list(BOMB_ALERT_CHAT_IDS)
    return parse_chat_ids(state.get("groups") or [])[0]


def bomb_view() -> dict:
    state = load_bomb_state()
    enabled, minutes, days = bomb_settings(state)
    return {
        "enabled": enabled, "minutes": minutes, "days": days, "last_at": state.get("last_at", ""),
        "last_result": state.get("last_result", ""), "next_at": state.get("next_at", "") if enabled else "",
        "run_requested": bool(state.get("run_requested")), "told": len(state.get("told") or []),
        "groups": bomb_groups(state), "ignore_paths": bomb_ignore_paths(state),
        "branches": list(GIT_BRANCHES), "recent": (state.get("recent") or [])[:10],
    }


def control_bomb(action: str, minutes=None, days=None, groups_text: str = "", ignore_text: str = "") -> dict:
    with _bomb_lock:
        state = load_bomb_state()
        if action == "groups":
            good, bad = parse_chat_ids(groups_text)
            if bad:
                return dict(bomb_view(), ok=False, message="这些不是群 ID（要 oc_ 开头 32 位）：" + "、".join(bad[:5]))
            state["groups"] = good
            save_bomb_state(state)
            message = f"已保存，炸线提醒发到 {len(good)} 个群。" if good else "已保存，不发群，只私聊朱辉。"
            return dict(bomb_view(), ok=True, message=message)
        if action == "ignore_paths":
            paths = bomb.parse_ignore_paths(ignore_text)
            state["ignore_paths"] = paths
            save_bomb_state(state)
            message = (f"已保存，提醒忽略 {len(paths)} 条路径前缀；看板仍显示完整结果。"
                       if paths else "已保存，提醒不再按路径过滤（看板与提醒一致）。")
            return dict(bomb_view(), ok=True, message=message)
        if action in ("on", "off"):
            state["enabled"] = action == "on"
            if minutes is not None:
                state["minutes"] = min(max(int(minutes), 5), 1440)
            if days is not None:
                state["days"] = min(max(int(days), 1), bomb.MAX_DAYS)
            state["next_at"] = ""
            message = "已开启，大约一分钟内先查一次。" if action == "on" else "已关闭，不再自动查。问「今天有没有炸线」仍然能查。"
        elif action == "now":
            state["run_requested"] = True
            message = "已排队，一分钟内查一次。有新的会私聊朱辉并发到群里。"
        else:
            raise ValueError("不认识的操作。")
        save_bomb_state(state)
    return dict(bomb_view(), ok=True, message=message)


BOMB_REMIND_WORK_HOURS = (9, 21)  # 没解决的旧炸线只在白天再提醒（每次检查都报）；新发现的随时报


def bomb_remind_due(_state: dict, now: datetime.datetime) -> bool:
    """没解决的就一直报：白天每次检查都报，不看上次什么时候报过。"""
    return BOMB_REMIND_WORK_HOURS[0] <= now.hour < BOMB_REMIND_WORK_HOURS[1]


def bomb_board_url() -> str:
    return review_web_base().rsplit("/review", 1)[0] + "/bomb/"


def bomb_keys(results: list) -> list:
    return [item["merge"] for _b, merges, _f in results for item in merges] + \
           [f"force:{item['new']}" for _b, _m, forced in results for item in forced]


def run_bomb_once(days: int) -> None:
    """增量查新合并 → 新的先让 Codex 判断并记进看板 → 看板里所有没标记解决的拿来提醒。

    新发现的马上报；之前报过、还没解决的白天每次检查都再报，直接用看板记录，不重新查 git。"""
    state = load_bomb_state()
    known = {str(item) for item in state.get("told") or []}
    ignore_paths = bomb_ignore_paths(state)
    bases = {str(k): str(v) for k, v in (state.get("scan_tips") or {}).items() if v}
    tips = {}
    results, scanned, note = scan_bomb(list(GIT_BRANCHES), days, bases, tips)
    alerted, dropped = alert_bomb_results(results, ignore_paths)
    judge_bomb_results([(branch, [item for item in merges if item["merge"] not in known], forced)
                        for branch, merges, forced in alerted])
    record_bomb_history(results, list(known))
    pending, _dropped_old = alert_bomb_results(bomb_board.open_results(), ignore_paths)
    judge_bomb_results(pending)  # 旧记录缺判断的顺手补上（有缓存的不再调 Codex）
    pending_keys = bomb_keys(pending)
    fresh_keys = [key for key in pending_keys if key not in known]
    old_keys = [key for key in pending_keys if key in known]
    now = datetime.datetime.now(BEIJING_TZ)
    remind = bool(old_keys) and bomb_remind_due(state, now)
    sent = True
    if fresh_keys or remind:
        intro_bits = []
        if fresh_keys:
            intro_bits.append(f"新发现 **{len(fresh_keys)}** 条")
        if old_keys:
            intro_bits.append(f"之前报过、还没标记解决 **{len(old_keys)}** 条")
        intro = "，".join(intro_bits) + f"。处理完到 [炸线看板]({bomb_board_url()}) 点「标记已解决」，就不再提醒。"
        card = bomb.build_card(BOMB_TITLE, intro, pending, bomb.BOMB_NOTE, bomb_people())
        key = hashlib.sha1(("|".join(pending_keys) + now.strftime("%Y%m%d%H%M")).encode("utf-8")).hexdigest()[:12]
        sent = send_bomb_alert(card, key)
        body = intro + "\n\n" + "\n\n".join(bomb.format_report(b, m, f) for b, m, f in pending)
    now_text = now.isoformat(timespec="seconds")
    with _bomb_lock:
        state = load_bomb_state()
        if fresh_keys or remind:
            recent = [{"at": now_text, "sent": sent, "text": redact(body)[:4000]}] + list(state.get("recent") or [])
            state["recent"] = recent[:BOMB_RECENT_KEEP]
            if sent:
                told = [str(item) for item in state.get("told") or []]
                state["told"] = list(dict.fromkeys(told + fresh_keys))[-BOMB_TOLD_KEEP:]
                state["last_remind_at"] = now_text
        state["scan_tips"] = dict(state.get("scan_tips") or {}, **{b: t for b, t in tips.items() if t})
        state["last_at"] = now_text
        skip = f"忽略路径滤掉 {dropped} 个文件。" if dropped else ""
        window = "从上次查到的位置起" if bases else f"最近 {days} 天"
        state["last_result"] = (f"{window}查了 {scanned} 个合并，新发现 {len(fresh_keys)} 条，"
                                f"没解决的 {len(old_keys)} 条{'（这次再提醒了）' if remind and sent else ''}。{skip}{note}")[:300]
        save_bomb_state(state)
    if sent and fresh_keys:
        bomb_board.mark_alerted(fresh_keys)
    log(f"[bomb] loop scanned={scanned} new={len(fresh_keys)} open={len(old_keys)} remind={remind} dropped={dropped} sent={sent}")


def record_bomb_history(results: list, told, backfill: bool = False) -> None:
    """写炸线看板的历史。只记录，不发消息；写失败不影响提醒。"""
    try:
        added = bomb_board.record_scan(results, told, backfill)
    except Exception as error:
        log(f"[bomb] history failed {type(error).__name__}: {error}")
        return
    if added or backfill:
        log(f"[bomb] history added={added} backfill={backfill}")


def backfill_bomb_history() -> None:
    """看板第一次上线时补最近 7 天，之后不再补。"""
    if not bomb_board.needs_backfill():
        return
    results, scanned, note = scan_bomb(list(GIT_BRANCHES), bomb.MAX_DAYS)
    record_bomb_history(results, load_bomb_state().get("told"), backfill=True)
    log(f"[bomb] backfill scanned={scanned} {note}".rstrip())


def bomb_loop() -> None:
    try:
        backfill_bomb_history()
    except Exception as error:
        log(f"[bomb] backfill failed {type(error).__name__}: {error}")
    while True:
        try:
            state = load_bomb_state()
            enabled, minutes, days = bomb_settings(state)
            now = datetime.datetime.now(BEIJING_TZ)
            next_at = state.get("next_at") or ""
            due = enabled and (not next_at or next_at <= now.isoformat(timespec="seconds"))
            if state.get("run_requested") or due:
                run_bomb_once(days)
                with _bomb_lock:
                    state = load_bomb_state()
                    state["run_requested"] = False
                    following = datetime.datetime.now(BEIJING_TZ) + datetime.timedelta(minutes=minutes)
                    state["next_at"] = following.isoformat(timespec="seconds") if enabled else ""
                    save_bomb_state(state)
        except Exception as error:
            log(f"[bomb] loop failed {type(error).__name__}: {error}")
        time.sleep(BOMB_POLL_SEC)


REVIEW_SUBS_PATH = os.path.join(DATA_DIR, "review_subs.json")
REVIEW_STATE_PATH = os.path.join(DATA_DIR, "review_state.json")
REVIEW_DIR = os.path.join(DATA_DIR, "reviews")
REVIEW_KEEP_DAYS = 90
REVIEW_WEB_PORT = int(os.environ.get("CONSOLE_PORT", "80"))  # 复盘挂在大豆后台 /review 下
REVIEW_POLL_SEC = 60
REVIEW_AUTHOR_PATTERN = re.compile(r"^[A-Za-z0-9\u4e00-\u9fff][A-Za-z0-9_.\-\u4e00-\u9fff ]{0,39}$")
REVIEW_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_review_lock = threading.Lock()


def review_file_name(author: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.\-\u4e00-\u9fff]", "_", author or "")[:40] or "_"


def load_review_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as json_file:
            data = json.load(json_file)
    except (OSError, ValueError):
        return default
    return data if isinstance(data, type(default)) else default


def save_review_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8", newline="\n") as json_file:
        json.dump(data, json_file, ensure_ascii=False, indent=2)
    os.replace(temporary, path)


def load_review_subs() -> list:
    subs = load_review_json(REVIEW_SUBS_PATH, {}).get("subs") or []
    return [item for item in subs if isinstance(item, dict) and item.get("git_author")]


def git_author_names() -> list:
    git_root = get_git_root()
    names = set()
    for branch in GIT_BRANCHES:
        ok, out = run_git_readonly(git_root, ["log", git_ref(branch), "--format=%an", "-n", "8000"])
        if ok:
            names.update(line.strip() for line in out.splitlines() if line.strip())
    return sorted(names)


def git_author_exists(author: str) -> bool:
    lowered = author.casefold()
    return any(name.casefold() == lowered for name in git_author_names())


def bot_api_get(path: str, params: dict) -> dict:
    """以机器人身份 GET 飞书接口，返回 data；失败抛 RuntimeError。"""
    payload = json.loads(run_cli(["api", "GET", path, "--as", "bot", "--params", json.dumps(params)]))
    if not payload.get("ok", payload.get("code") == 0):
        raise RuntimeError(str(payload.get("error") or payload.get("msg") or payload)[:300])
    return payload.get("data") or {}


def bot_api_pages(path: str, params: dict) -> list:
    items = []
    page_token = ""
    for _ in range(50):
        data = bot_api_get(path, {**params, "page_size": 100, **({"page_token": page_token} if page_token else {})})
        items.extend(data.get("items") or [])
        page_token = data.get("page_token") or ""
        if not data.get("has_more") or not page_token:
            break
    return items


_bot_members_cache = {"at": 0.0, "people": {}}
_bot_members_lock = threading.Lock()


def bot_known_people(max_age: float = 600) -> dict:
    """机器人所在各群的成员：{姓名: {open_id, ...}}。open_id 是本应用视角的，能直接私聊。"""
    with _bot_members_lock:
        if time.time() - _bot_members_cache["at"] < max_age and _bot_members_cache["people"]:
            return _bot_members_cache["people"]
        people = {}
        for chat in bot_api_pages("/open-apis/im/v1/chats", {}):
            chat_id = str(chat.get("chat_id") or "")
            if not chat_id.startswith("oc_"):
                continue
            try:
                members = bot_api_pages(f"/open-apis/im/v1/chats/{chat_id}/members", {"member_id_type": "open_id"})
            except (RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
                log(f"[people] members {chat_id} failed {error}")
                continue
            for member in members:
                member_name = str(member.get("name") or "").strip()
                member_id = str(member.get("member_id") or "")
                if member_name and member_id.startswith("ou_"):
                    people.setdefault(member_name, set()).add(member_id)
        _bot_members_cache.update(at=time.time(), people=people)
        return people


def find_open_id_by_name(name: str) -> tuple:
    """按飞书姓名找 open_id。返回 (open_id, 说明)；找不到或重名时 open_id 为空。"""
    try:
        ids = bot_known_people().get(name.strip()) or set()
    except (RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
        log(f"[people] lookup {name} failed {error}")
        return "", "查飞书成员失败，先只出网页。"
    if len(ids) == 1:
        return next(iter(ids)), ""
    if len(ids) > 1:
        return "", f"大豆所在的群里有 {len(ids)} 个「{name}」，重名了，先只出网页。"
    return "", f"大豆所在的群里没找到「{name}」（姓名要和飞书里一致，且要和大豆同在一个群），先只出网页。"


def control_review_sub(action: str, name: str = "", git_author: str = "", open_id: str = "") -> dict:
    """后台登记名单。返回 {"ok", "message", "subs"}。"""
    name = (name or "").strip()[:20]
    git_author = (git_author or "").strip()
    open_id = (open_id or "").strip()
    if action == "remove":
        with _review_lock:
            subs = load_review_subs()
            kept = [item for item in subs if item.get("git_author") != git_author]
            save_review_json(REVIEW_SUBS_PATH, {"subs": kept})
            return {"ok": len(kept) < len(subs), "message": f"已移除 {git_author}。" if len(kept) < len(subs) else "名单里没有这个人。", "subs": kept}
    if not name:
        return {"ok": False, "message": "姓名要填。", "subs": load_review_subs()}
    if not REVIEW_AUTHOR_PATTERN.match(git_author):
        return {"ok": False, "message": "git 作者名只能是字母、数字、中文、下划线、点、短横线。", "subs": load_review_subs()}
    if open_id and not re.fullmatch(r"ou_[0-9a-f]{20,40}", open_id):
        return {"ok": False, "message": "open_id 要以 ou_ 开头；不知道就留空，只出网页不私聊。", "subs": load_review_subs()}
    authors = git_author_names()
    matched = next((item for item in authors if item.casefold() == git_author.casefold()), "")
    if not matched:
        hint = [item for item in authors if git_author.casefold() in item.casefold() or item.casefold() in git_author.casefold()][:5]
        tip = (" 相近的有：" + "、".join(hint) + "。") if hint else " 打开终端跑 git shortlog -sn origin/main 能看到作者名。"
        return {"ok": False, "message": f"main 和 CBT2_bugfix 上找不到作者 {git_author} 的提交。{tip}", "subs": load_review_subs()}
    with _review_lock:
        subs = load_review_subs()
        old = next((item for item in subs if item.get("git_author") == matched), {})
        kept = [item for item in subs if item.get("git_author") != matched]
        kept.append({"name": name, "git_author": matched, "open_id": open_id, "dm": bool(old.get("dm"))})
        save_review_json(REVIEW_SUBS_PATH, {"subs": kept})
        tip = "，会私聊。" if old.get("dm") else "。要私聊就在名单里勾上「私聊」。"
        return {"ok": True, "message": f"已登记 {name}（{matched}）{tip}", "subs": kept}


def control_review_dm(git_author: str, on: bool) -> dict:
    """勾选/取消某人的私聊。勾上时按姓名找 open_id，找不到就不勾。"""
    git_author = (git_author or "").strip()
    sub = next((item for item in load_review_subs() if item.get("git_author") == git_author), None)
    if not sub:
        return {"ok": False, "message": "名单里没有这个人。", "subs": load_review_subs()}
    open_id = sub.get("open_id") or ""
    if on and not open_id:
        open_id, why = find_open_id_by_name(str(sub.get("name") or ""))
        if not open_id:
            return {"ok": False, "message": why.replace("，先只出网页。", "，没法私聊。"), "subs": load_review_subs()}
    with _review_lock:
        subs = load_review_subs()
        for item in subs:
            if item.get("git_author") == git_author:
                item["dm"] = bool(on)
                if open_id:
                    item["open_id"] = open_id
        save_review_json(REVIEW_SUBS_PATH, {"subs": subs})
    name = sub.get("name") or git_author
    return {"ok": True, "message": f"{name} 以后每天会收到私聊摘要。" if on else f"{name} 不再私聊，只出网页。", "subs": subs}


def fill_review_open_ids(subs: list) -> list:
    """勾了私聊但没 open_id 的，按姓名再找一次（人后来进了群就能补上），找到就写回。"""
    missing = [item for item in subs if item.get("dm") and not item.get("open_id") and item.get("name")]
    if not missing:
        return subs
    found = {}
    for item in missing:
        open_id, _why = find_open_id_by_name(item["name"])
        if open_id:
            found[item["git_author"]] = open_id
    if found:
        with _review_lock:
            fresh = load_review_subs()
            for item in fresh:
                if not item.get("open_id") and item.get("git_author") in found:
                    item["open_id"] = found[item["git_author"]]
            save_review_json(REVIEW_SUBS_PATH, {"subs": fresh})
        log(f"[review] open_id filled {', '.join(found)}")
    return [{**item, "open_id": item.get("open_id") or found.get(item.get("git_author"), "")} for item in subs]


def review_day_bounds(day: datetime.date) -> tuple:
    start = datetime.datetime.combine(day, datetime.time.min, BEIJING_TZ)
    return start.isoformat(timespec="seconds"), (start + datetime.timedelta(days=1)).isoformat(timespec="seconds")


REVIEW_SKIP_SUBJECTS = {"auto update output", "auto sync svc from devops"}


def is_review_skip_subject(subject: str) -> bool:
    """说明里含打包输出、DevOps 同步这类字样的提交都不进复盘。"""
    text = " ".join((subject or "").split()).lower()
    return any(item in text for item in REVIEW_SKIP_SUBJECTS)


def review_day_commits(author: str, day: datetime.date) -> list:
    """返回 [(hash, branch, line)]，两条分支去重，不含合并提交和自动提交。"""
    git_root = get_git_root()
    since, until = review_day_bounds(day)
    rows = []
    seen = set()
    for branch in GIT_BRANCHES:
        ok, out = run_git_readonly(git_root, [
            "log", git_ref(branch), "--no-merges", f"--since={since}", f"--until={until}",
            "--author=" + re.escape(author), "--date=format:%m-%d %H:%M", "--pretty=format:%h|%an|%ad|%s",
        ])
        for line in (out.splitlines() if ok else []):
            short = line.split("|", 1)[0]
            if is_review_skip_subject(line.split("|", 3)[-1]):
                continue
            if line.strip() and short not in seen:
                seen.add(short)
                rows.append((short, branch, line))
    return rows


def review_bomb_sections(author: str, day_merges: list) -> tuple:
    """返回 (他合丢了别人的, 别人合丢了他的) 两段 markdown。"""
    lower = author.lower()
    outgoing = []
    incoming = []
    for branch, merges in day_merges:
        mine = [item for item in merges if item["author"].lower() == lower]
        theirs = [item for item in merges if item["author"].lower() != lower and any(
            line.split("|", 2)[1].lower() == lower for detail in item["files"] for line in detail["commits"] if "|" in line)]
        if mine:
            outgoing.append(bomb.format_report(branch, mine, []))
        if theirs:
            incoming.append(bomb.format_report(branch, theirs, []))
    return "\n\n".join(outgoing), "\n\n".join(incoming)


def review_web_base() -> str:
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("10.234.80.148", 80))
        host = probe.getsockname()[0]
        probe.close()
    except OSError:
        host = "127.0.0.1"
    port = "" if REVIEW_WEB_PORT == 80 else f":{REVIEW_WEB_PORT}"
    return f"http://{host}{port}/review"


def review_brief(report: str, commit_count: int, outgoing: str, incoming: str) -> str:
    parts = [f"{commit_count} 条提交"]
    if "没看出明显问题" in report:
        parts.append("没看出明显问题")
    elif "需要留意" in report:
        parts.append("有需要留意的地方")
    if outgoing:
        parts.append("你的合并疑似丢了别人的修改")
    if incoming:
        parts.append("你的修改疑似被别人合并丢了")
    return "，".join(parts) + "。"


def review_needs_notice(record: dict) -> bool:
    """有提交且看出问题（报告不是「没看出明显问题」、炸线、生成失败）才发私聊和群。"""
    status = record.get("status")
    if status == "failed":
        return True
    if status != "ok":
        return False
    if record.get("bomb_out_md") or record.get("bomb_in_md"):
        return True
    report = record.get("report_md") or ""
    return bool(record.get("commits")) and bool(report) and "没看出明显问题" not in report


REVIEW_DEFAULT_GROUPS = ("oc_d7aa3ebf93e3bf0dc5a1b4b6c3459737",)


def review_groups(state: dict = None) -> list:
    """复盘汇总要发的群。状态文件里没配过时用默认群。"""
    state = load_review_json(REVIEW_STATE_PATH, {}) if state is None else state
    if "groups" not in state:
        return list(REVIEW_DEFAULT_GROUPS)
    return parse_chat_ids(state.get("groups") or [])[0]


def control_review_groups(text: str) -> dict:
    good, bad = parse_chat_ids(text)
    if bad:
        return {"ok": False, "message": "这些不是群 ID（要 oc_ 开头 32 位）：" + "、".join(bad[:5])}
    with _review_lock:
        state = load_review_json(REVIEW_STATE_PATH, {})
        state["groups"] = good
        save_review_json(REVIEW_STATE_PATH, state)
    return {"ok": True, "message": f"已保存，每天复盘汇总发到 {len(good)} 个群。" if good else "已保存，不发群。"}


def review_group_text(day_text: str, records: list, base: str) -> str:
    """只列有问题的人；没问题和没提交的不点名。"""
    active = [record for record in records if record.get("status") != "empty"]
    flagged = [record for record in active if review_needs_notice(record)]
    total = sum(len(record.get("commits") or []) for record in active)
    lines = [f"{len(records)} 人里 {len(active)} 人有提交，共 {total} 条，{len(flagged)} 人有需要留意的地方。"
             f"[看当天全部]({base}/?d={day_text})"]
    for record in flagged:
        link = f"{base}/r/{day_text}/{review_file_name(record['git_author'])}"
        if record.get("status") == "failed":
            brief = "复盘生成失败，网页上有说明。"
        else:
            brief = review_brief(record.get("report_md", ""), len(record.get("commits") or []),
                                 record.get("bomb_out_md", ""), record.get("bomb_in_md", ""))
        lines.append(f"**{record.get('name')}**：{brief}[详情]({link})")
    lines.append("内容来自真实 diff。")
    return "\n".join(lines)


REVIEW_INFO_PATTERN = re.compile(r"复盘.*(几点|什么时候|啥时候|何时|多久|怎么|如何|哪|网页|链接|地址|名单|登记|规则|发到|群)"
                                 r"|(几点|什么时候|啥时候|何时|哪|怎么|如何).*复盘")


def is_review_info_question(text: str) -> bool:
    return bool(REVIEW_INFO_PATTERN.search(text or "")) and not commit_hash_in(text or "")


def answer_review_info() -> str:
    names = "、".join(str(sub.get("name") or sub.get("git_author")) for sub in load_review_subs()) or "还没有人"
    groups = review_groups()
    base = review_web_base()
    lines = [
        "每天 0 点（北京时间）复盘名单里每个人**前一天**在 main 和 CBT2_bugfix 上的提交，按真实 diff 写改动、影响和隐患。",
        f"- 结果网页：[{base}]({base})，可以按日期看所有人",
        f"- 汇总发到 {len(groups)} 个群" if groups else "- 现在不发群",
        "- 只有看出问题（需要留意、炸线、生成失败）才私聊和发群，没提交或没问题不打扰；说明里含「Auto update output」「Auto sync svc from devops」的提交不算",
        f"- 名单：{names}。要加人找朱辉在后台登记（git 作者名要和提交里一致）",
        "想现在就看某人的提交自检，说「zhuhui 最近 10 条提交 自检」。",
    ]
    return "\n".join(lines)


WEEKLY_NAME_PATTERN = re.compile(r"^\s*(?:帮我|给我)?(?:写|生成|出)?(?:一下|个|份)?\s*([\u4e00-\u9fa5A-Za-z][\u4e00-\u9fa5A-Za-z0-9_.\-]{0,19}?)\s*的?\s*(?:这周|本周)?的?\s*周报\s*$"
                                 r"|^\s*周报\s*([\u4e00-\u9fa5A-Za-z][\u4e00-\u9fa5A-Za-z0-9_.\-]{0,19})\s*$")
WEEKLY_NOT_NAMES = {"帮我", "给我", "写", "写个", "写一份", "我", "我的", "这周", "本周", "上周", "一份", "个"}
WEEKLY_REPORT_PROMPT = (
    "下面是一位同事这周在 git 上的真实提交，按天列出提交说明和复盘里的改动摘要。\n"
    "把这周做的事整理成周报里「本周完成」的条目：只输出编号列表，不要标题和其他段落。\n"
    "合并同类事项，3～6 条，每条一句话，像人写的工作汇报，不列提交号、链接和文件名细节。\n"
    "只写做了什么，不写提升、优化效果、能力增强这类材料里没有的结果和评价，不要编造功能或数据。"
)


def weekly_report_target(text: str):
    """命中「X周报」时返回 (sub 或 None, 名字)；不是点名周报返回 None。"""
    match = WEEKLY_NAME_PATTERN.match(text or "")
    if not match:
        return None
    name = (match.group(1) or match.group(2) or "").strip()
    if not name or name in WEEKLY_NOT_NAMES:
        return None
    for sub in load_review_subs():
        if name in (sub.get("name"), ) or name.lower() == str(sub.get("git_author", "")).lower():
            return sub, name
    return None, name


def _report_change_lines(report: str) -> list:
    lines = []
    for raw in (report or "").splitlines():
        line = raw.strip()
        if line.startswith("改动：") and not _REPORT_EMPTY_LINE.search(line):
            lines.append(line[3:])
    return lines


def _report_risk_lines(report: str) -> list:
    risks = []
    inside = False
    for raw in (report or "").splitlines():
        line = raw.strip()
        if "需要留意" in line:
            inside = True
            continue
        if inside and line.startswith("**"):
            break
        if inside and line:
            risks.append(line.lstrip("-•· "))
    return risks


def _commit_subject(line: str) -> str:
    subject = line.split("|", 3)[-1]
    return re.sub(r"\s*https?://\S+", "", subject).strip()


def weekly_material(author: str, today: datetime.date) -> tuple:
    """返回 (按天的材料文本, 提交条数, 日期范围文字, 提交说明列表, 需要留意列表)。周一到昨天读复盘，今天现查 git。"""
    monday = today - datetime.timedelta(days=today.weekday())
    blocks = []
    all_subjects = []
    all_risks = []
    total = 0
    day = monday
    while day <= today:
        if day < today:
            record = load_review_json(os.path.join(REVIEW_DIR, f"{day:%Y-%m-%d}", review_file_name(author) + ".json"), {})
            subjects = [_commit_subject(item.get("line", "")) for item in record.get("commits") or []]
            changes = _report_change_lines(record.get("report_md", ""))
            risks = _report_risk_lines(record.get("report_md", ""))
            if not record:
                subjects = [_commit_subject(line) for _short, _branch, line in review_day_commits(author, day)]
        else:
            subjects = [_commit_subject(line) for _short, _branch, line in review_day_commits(author, day)]
            changes = []
            risks = []
        all_risks += risks
        if subjects:
            total += len(subjects)
            all_subjects += subjects
            block = [f"{day:%m-%d}（{len(subjects)} 条）", "提交说明：", *[f"- {item}" for item in subjects]]
            if changes:
                block += ["改动摘要：", *[f"- {item}" for item in changes]]
            blocks.append("\n".join(block))
        day += datetime.timedelta(days=1)
    return "\n\n".join(blocks), total, f"{monday:%m-%d} ~ {today:%m-%d}", all_subjects, all_risks


def answer_weekly_report(target: tuple) -> str:
    sub, name = target
    if not sub:
        names = "、".join(str(item.get("name") or item.get("git_author")) for item in load_review_subs()) or "还没有人"
        return f"{name} 不在复盘名单里，写不了周报。现在名单：{names}。要加人找朱辉在后台登记。"
    author = sub["git_author"]
    display = sub.get("name") or author
    git_root = get_git_root()
    for branch in GIT_BRANCHES:
        fetch_origin_branch(git_root, branch)
    today = datetime.datetime.now(BEIJING_TZ).date()
    material, total, span, subjects, risks = weekly_material(author, today)
    head = f"**{display} 本周周报**（{span}，共 {total} 条提交）"
    if not total:
        return f"{head}\n\n这周在 {' 和 '.join(GIT_BRANCHES)} 上还没有提交，没法写。"
    items = []
    if LLM_ENABLED:
        try:
            body = call_chat(LLM_MODEL, [
                {"role": "system", "content": WEEKLY_REPORT_PROMPT},
                {"role": "user", "content": material[:8000]},
            ], LLM_FALLBACK_MODELS)
            for line in (body or "").splitlines():
                item = re.sub(r"^\s*(?:\d+[.、)）]|[-*•·])\s*", "", line).strip().strip("*")
                if item and not item.endswith("：") and "本周完成" not in item:
                    items.append(item)
        except (urllib.error.URLError, TimeoutError, OSError, KeyError, ValueError, RuntimeError) as error:
            log(f"[weekly] llm failed {error}")
    if not items:
        items = list(dict.fromkeys(subjects))[:8]
    risk_items = list(dict.fromkeys(risks))[:3] or ["无"]
    lines = [head, "", "**本周完成**"]
    lines += [f"{index}. {item}" for index, item in enumerate(items[:6], 1)]
    lines += ["", "**问题与风险**"]
    lines += [f"{index}. {item}" for index, item in enumerate(risk_items, 1)]
    base = review_web_base()
    lines += ["", f"按 git 提交和每日复盘整理，今天的提交还没复盘。[看每日复盘]({base}/p/{review_file_name(author)})"]
    return redact("\n".join(lines))


def generate_daily_reviews(day: datetime.date, notify: bool = True) -> str:
    subs = load_review_subs()
    if not subs:
        return "名单是空的，没跑。"
    if notify:
        subs = fill_review_open_ids(subs)
    git_root = get_git_root()
    for branch in GIT_BRANCHES:
        fetch_origin_branch(git_root, branch)
    since, _until = review_day_bounds(day)
    day_key = f"{day:%m-%d}"
    day_merges = []
    for branch in GIT_BRANCHES:
        merges, _count = bomb.scan_merges(bomb_git, git_ref(branch), since)
        day_merges.append((branch, [item for item in merges if item["date"].startswith(day_key)]))
    base = review_web_base()
    done = []
    records = []
    for sub in subs:
        author = sub["git_author"]
        record = {"name": sub.get("name") or author, "git_author": author, "date": f"{day:%Y-%m-%d}",
                  "generated_at": datetime.datetime.now(BEIJING_TZ).isoformat(timespec="seconds")}
        try:
            commits = review_day_commits(author, day)
            outgoing, incoming = review_bomb_sections(author, day_merges)
            report, diffs = commit_report(git_root, "\n".join(line for _short, _branch, line in commits)) if commits else ("", {})
            record["commits"] = [
                {"hash": short, "branch": branch, "line": line, "diff": redact(diffs.get(short, ""))}
                for short, branch, line in commits
            ]
            record["report_md"] = redact(report)
            record["bomb_out_md"] = redact(outgoing)
            record["bomb_in_md"] = redact(incoming)
            record["status"] = "ok" if commits or outgoing or incoming else "empty"
            if commits and not report:
                record["status"] = "failed"
        except Exception as error:
            log(f"[review] {author} failed {type(error).__name__}: {error}")
            record["status"] = "failed"
            record["error"] = str(error)[:200]
        save_review_json(os.path.join(REVIEW_DIR, record["date"], review_file_name(author) + ".json"), record)
        if notify and sub.get("dm") and sub.get("open_id") and review_needs_notice(record):
            link = f"{base}/r/{record['date']}/{review_file_name(author)}"
            brief = "复盘生成失败，网页上有说明。" if record["status"] == "failed" else review_brief(
                record.get("report_md", ""), len(record.get("commits") or []), record.get("bomb_out_md", ""), record.get("bomb_in_md", ""))
            send_review_dm(sub["open_id"], f"{record['date']} 自检复盘", f"{brief}\n[看完整复盘]({link})", f"review-{record['date']}-{author}")
        done.append(f"{record['name']}:{record['status']}")
        records.append(record)
    clean_old_reviews()
    group_note = ""
    groups = review_groups() if notify else []
    if groups and not any(review_needs_notice(record) for record in records):
        group_note = "；没看出问题，不发群"
    elif groups:
        day_text = f"{day:%Y-%m-%d}"
        stamp = datetime.datetime.now(BEIJING_TZ).strftime("%H%M")
        sent = send_groups(groups, review_group_text(day_text, records, base), f"review-g-{day_text}-{stamp}", f"{day_text} 自检复盘")
        group_note = f"；发群 {sent}/{len(groups)}"
    return f"{day:%Y-%m-%d} 复盘完成：" + "，".join(done) + group_note


def send_review_dm(open_id: str, title: str, text: str, key: str) -> bool:
    payload = {"zh_cn": {"title": title, "content": [[{"tag": "md", "text": redact(text)[:3000]}]]}}
    try:
        run_cli(["im", "+messages-send", "--as", "bot", "--user-id", open_id, "--msg-type", "post",
                 "--content", json.dumps(payload, ensure_ascii=False), "--idempotency-key", key[:50]])
    except (RuntimeError, subprocess.TimeoutExpired) as error:
        log(f"[review] dm failed {open_id} {error}")
        return False
    return True


def clean_old_reviews() -> None:
    if not os.path.isdir(REVIEW_DIR):
        return
    cutoff = datetime.datetime.now(BEIJING_TZ).date() - datetime.timedelta(days=REVIEW_KEEP_DAYS)
    for name in os.listdir(REVIEW_DIR):
        if REVIEW_DATE_PATTERN.match(name) and name < f"{cutoff:%Y-%m-%d}":
            shutil.rmtree(os.path.join(REVIEW_DIR, name), ignore_errors=True)


def request_review_run(day_text: str) -> dict:
    if not REVIEW_DATE_PATTERN.match(day_text or ""):
        return {"ok": False, "message": "日期格式要 2026-11-03。"}
    with _review_lock:
        state = load_review_json(REVIEW_STATE_PATH, {})
        state["run_requested"] = day_text
        save_review_json(REVIEW_STATE_PATH, state)
    return {"ok": True, "message": f"已排队补跑 {day_text}，一两分钟内开始，跑完会私聊勾了「私聊」的人，并发到复盘群。"}


def review_loop() -> None:
    while True:
        try:
            state = load_review_json(REVIEW_STATE_PATH, {})
            yesterday = datetime.datetime.now(BEIJING_TZ).date() - datetime.timedelta(days=1)
            done_dates = [str(item) for item in state.get("done_dates") or [] if str(item) <= f"{yesterday:%Y-%m-%d}"]
            requested = str(state.get("run_requested") or "")
            target = None
            if requested and REVIEW_DATE_PATTERN.match(requested):
                target = datetime.date.fromisoformat(requested)
            elif f"{yesterday:%Y-%m-%d}" not in done_dates:
                target = yesterday
            if target:
                result = generate_daily_reviews(target)
                log(f"[review] {result}")
                with _review_lock:
                    state = load_review_json(REVIEW_STATE_PATH, {})
                    if state.get("run_requested") == requested:
                        state["run_requested"] = ""
                    today_text = f"{datetime.datetime.now(BEIJING_TZ).date():%Y-%m-%d}"
                    dates = [str(item) for item in state.get("done_dates") or [] if str(item) < today_text]
                    if f"{target:%Y-%m-%d}" < today_text:
                        dates.append(f"{target:%Y-%m-%d}")
                    state["done_dates"] = list(dict.fromkeys(dates))[-120:]
                    state["last_result"] = result[:500]
                    state["last_at"] = datetime.datetime.now(BEIJING_TZ).isoformat(timespec="seconds")
                    save_review_json(REVIEW_STATE_PATH, state)
        except Exception as error:
            log(f"[review] loop failed {type(error).__name__}: {error}")
        time.sleep(REVIEW_POLL_SEC)


BRANCH_SAFE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-/]{0,80}$")
CHECKOUT_BRANCH_PATTERN = re.compile(
    r"(?:切换到|切到|切回|checkout\s+)\s*[`\"']?([A-Za-z0-9][A-Za-z0-9_.\-/]{0,80})",
    re.IGNORECASE,
)


def safe_branch_name(name: str) -> str:
    text = (name or "").strip().strip("`\"'")
    if text.lower().startswith("origin/"):
        text = text[7:]
    if not BRANCH_SAFE_PATTERN.fullmatch(text):
        return ""
    if ".." in text or text.startswith("-") or text.endswith("/") or text.endswith("\\"):
        return ""
    return text


def parse_checkout_branch(text: str) -> str:
    match = CHECKOUT_BRANCH_PATTERN.search(text or "")
    if not match:
        return ""
    return safe_branch_name(match.group(1))


def is_checkout_request(text: str) -> bool:
    return bool(parse_checkout_branch(text))


def git_write_allowed(args: list) -> bool:
    if args == ["status", "--porcelain", "--untracked-files=no"]:
        return True
    if args == ["reset", "--hard", "HEAD"]:
        return True
    if len(args) == 4 and args[0] == "checkout" and args[1] == "-B":
        branch = safe_branch_name(args[2])
        return bool(branch) and args[3] == f"{GIT_REMOTE}/{branch}"
    if len(args) == 4 and args[:3] == ["merge-base", "--is-ancestor", "HEAD"]:
        return args[3] in {f"{GIT_REMOTE}/{branch}" for branch in GIT_BRANCHES}
    if len(args) == 4 and args[:3] == ["pull", "--ff-only", GIT_REMOTE]:
        return args[3] in GIT_BRANCHES
    return False


def run_git_write(git_root: str, args: list, timeout: int) -> tuple:
    """只允许查状态、判断能否快进、清掉本地修改、快进拉取、切到远端分支。返回 (ok, 输出)。"""
    if not git_write_allowed(args):
        return False, "这个写操作不在允许范围内。"
    try:
        result = subprocess.run(
            ["git", "-C", git_root] + args,
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
            creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, str(error)
    output = (result.stdout or "") + (result.stderr or "")
    return result.returncode == 0, output.strip()


def switch_worktree_branch(branch: str) -> str:
    """真切工作区到远端分支。与拉代码共用锁。"""
    target = safe_branch_name(branch)
    if not target:
        return "分支名不合法。只用字母数字、下划线、点、斜杠。"
    if not _pull_lock.acquire(blocking=False):
        return "正在拉代码或切分支，等这次做完再说。"
    try:
        git_root = get_git_root()
        ok, fetched = run_git_readonly(git_root, ["fetch", GIT_REMOTE, target], timeout=GIT_FETCH_TIMEOUT)
        if not ok:
            return f"远端没有 `{target}`，或 fetch 失败。没切。\n{(fetched or '')[-300:]}"
        ok, tip = run_git_readonly(git_root, ["rev-parse", "--verify", f"{GIT_REMOTE}/{target}"], timeout=10)
        if not ok:
            return f"远端没有 `{target}`。没切。"
        ok, current = run_git_readonly(git_root, ["rev-parse", "--abbrev-ref", "HEAD"], timeout=10)
        current = current if ok and current and current != "HEAD" else ""
        ok, status = run_git_write(git_root, ["status", "--porcelain", "--untracked-files=no"], 180)
        if not ok:
            return f"读不到工作区状态，没切。\n{(status or '')[-300:]}"
        discarded = len([line for line in status.splitlines() if line.strip()]) if status else 0
        cleaned = ""
        if discarded:
            log(f"[checkout] discard {discarded} dirty files before {target}")
            ok, reset_out = run_git_write(git_root, ["reset", "--hard", "HEAD"], 180)
            if not ok:
                return f"本地有 {discarded} 个文件改过，清理失败，没切。\n{(reset_out or '')[-300:]}"
            cleaned = f"已清掉 {discarded} 个本地修改。\n"
        ok, output = run_git_write(git_root, ["checkout", "-B", target, f"{GIT_REMOTE}/{target}"], 180)
        if not ok:
            log(f"[checkout] failed {output[-300:]}")
            return f"{cleaned}切到 `{target}` 失败，工作区还在 `{current or '原分支'}`。\n{(output or '')[-300:]}"
        ok, short = run_git_readonly(git_root, ["rev-parse", "--short", "HEAD"], timeout=10)
        tip = short if ok and short else tip[:12]
        from_text = f"从 `{current}` " if current and current != target else ""
        log(f"[checkout] {current or '-'} -> {target} {tip}")
        return (f"{cleaned}工作区已{from_text}切到 `{target}`，当前提交 `{tip}`。"
                "引用、面数、粒子、Shader 都读这个分支。"
                "查提交仍只开 main / CBT2_bugfix；盯目录和定时拉也不跟这个分支走。")
    finally:
        _pull_lock.release()


def answer_checkout(text: str, sender: str) -> str:
    if sender not in PULL_OWNER_OPEN_IDS:
        return "切工作区只认朱辉本人发。会动这台机器的检出，别人发我不切。"
    target = parse_checkout_branch(text)
    if not target:
        return "说清楚分支名，例如：切换到 CBT2_bugfix_shader。"
    if PREVIEWING:
        return f"（试跑）会切工作区到 `{target}`，有本地修改会先清掉。"
    return switch_worktree_branch(target)


def control_checkout(branch: str) -> dict:
    target = safe_branch_name(branch)
    if not target:
        raise ValueError("分支名不合法。")
    message = switch_worktree_branch(target)
    failed = message.startswith("远端没有") or "失败" in message or "没切" in message or "不合法" in message or "正在拉" in message
    return {"ok": not failed, "branch": target, "message": message}


def answer_pull(sender: str) -> str:
    if sender not in PULL_OWNER_OPEN_IDS:
        return "拉代码只认朱辉本人发。会动这台机器的工作区，别人发我不拉。"
    if not _pull_lock.acquire(blocking=False):
        return "正在拉，等这次拉完再说。"
    try:
        git_root = get_git_root()
        ok, branch = run_git_readonly(git_root, ["rev-parse", "--abbrev-ref", "HEAD"], timeout=10)
        if not ok or branch not in GIT_BRANCHES:
            return f"工作区在 `{branch or '读不到'}`，只在 main 或 CBT2_bugfix 上拉。没拉。"
        ok, fetched = run_git_readonly(git_root, ["fetch", GIT_REMOTE, branch], timeout=GIT_FETCH_TIMEOUT)
        if not ok:
            return f"没拉到远端 `{branch}`，本地修改没动。\n{fetched[-300:]}"
        remote_ref = f"{GIT_REMOTE}/{branch}"
        ff, ff_out = run_git_write(git_root, ["merge-base", "--is-ancestor", "HEAD", remote_ref], 30)
        if not ff:
            if ff_out:
                return f"判断能不能快进失败，本地修改没动，没拉。\n{ff_out[-300:]}"
            return f"`{branch}` 本地有远端没有的提交，不能快进。本地修改没动，没拉。"
        ok, status = run_git_write(git_root, ["status", "--porcelain", "--untracked-files=no"], 180)
        if not ok:
            return f"读不到工作区状态，没拉。\n{status[-300:]}"
        discarded = len([line for line in status.splitlines() if line.strip()]) if status else 0
        if discarded:
            log(f"[pull] discard {discarded} dirty files on {branch}")
            ok, reset_out = run_git_write(git_root, ["reset", "--hard", "HEAD"], 180)
            if not ok:
                return f"本地有 {discarded} 个文件改过，清理失败，没拉。\n{reset_out[-300:]}"
        _, old = run_git_readonly(git_root, ["rev-parse", "--short", "HEAD"], timeout=10)
        cleaned = f"已清掉 {discarded} 个本地修改。\n" if discarded else ""
        log(f"[pull] start branch={branch} head={old} discarded={discarded}")
        ok, output = run_git_write(git_root, ["pull", "--ff-only", GIT_REMOTE, branch], PULL_TIMEOUT)
        if not ok:
            log(f"[pull] failed {output[-300:]}")
            if discarded:
                return f"{cleaned}拉取失败，提交还停在 `{old}`。\n{output[-300:]}"
            return f"拉取失败，工作区没改。\n{output[-300:]}"
        _, new = run_git_readonly(git_root, ["rev-parse", "--short", "HEAD"], timeout=10)
        if new == old:
            return f"{cleaned}`{branch}` 已经是最新，`{new}`。"
        _, commits = run_git_readonly(git_root, ["log", "--oneline", "--format=%h %an %s", f"{old}..{new}"], timeout=30)
        lines = [line for line in commits.splitlines() if line.strip()]
        body = "\n".join(f"- {line}" for line in lines[:5])
        more = f"\n……另外还有 {len(lines) - 5} 条" if len(lines) > 5 else ""
        log(f"[pull] done {old}->{new} commits={len(lines)}")
        return (f"{cleaned}`{branch}` 已拉到最新：`{old}` → `{new}`，{len(lines)} 个提交。\n{body}{more}\n"
                "Unity 开着的话会自动重新导入。")
    finally:
        _pull_lock.release()


def answer_project_address() -> str:
    git_root = get_git_root()
    url = get_remote_url(git_root) if git_root else ""
    if not url:
        return "这边没读到仓库地址，找项目管理员要一下。"
    return f"仓库地址：{url}\n主分支：{GIT_BRANCH}\n拉代码：git clone {url}\n账号和权限找管理仓库的同事开，我这边不发账号密码。"


def format_git_log(rel_path: str, log_text: str, *, remote_url: str, tip: str, sync_note: str, ref: str) -> str:
    if not log_text:
        return (f"「{rel_path}」在 {ref} 上没有找到提交记录（可能未入库，或路径不对）。\n"
                f"（{sync_note}）")
    lines = [line for line in log_text.splitlines() if line.strip()]
    source = remote_url or GIT_REMOTE
    tip_part = f"，当前 tip `{tip}`" if tip else ""
    latest = ""
    first = lines[0].split("|", 3) if lines else []
    if len(first) == 4:
        latest = f"最近一次：{first[2]} {first[1]} — {first[3]}"
    blocks = [
        f"「{rel_path}」在 `{ref}` 最近 {len(lines)} 条提交/上传记录：",
        latest,
        f"仓库：{source}{tip_part}",
        f"同步：{sync_note}",
        "",
    ]
    for index, line in enumerate(lines, 1):
        parts = line.split("|", 3)
        if len(parts) == 4:
            short, author, date, subject = parts
            blocks.append(f"{index}. `{short}` {date} {author}\n   {subject}")
        else:
            blocks.append(f"{index}. {line}")
    blocks.append("\n说明：只读查询（fetch + log），不会 commit / push / 改你本地分支。")
    return "\n".join(blocks)


def earlier_git_path_question() -> str:
    """本轮之前，最近一条带文件路径的提问。"""
    for item in reversed(history_messages()):
        if item.get("role") != "user":
            continue
        content = item.get("content") or ""
        if extract_git_path_token(content):
            return content
    return ""


def git_query_text(text: str) -> str:
    """只点了分支名时，沿用本会话上一次的文件，换到这个分支再查。"""
    if extract_git_path_token(text) or not parse_git_branch(text):
        return text
    earlier = earlier_git_path_question()
    if not earlier:
        return text
    return f"{earlier}\n{text}"


_AUTHOR_COMMIT_SKIP = (
    "今天", "昨天", "最近", "提交", "内容", "什么", "一下", "帮我", "查下", "看看", "大豆",
    "这个", "那个", "记录", "历史", "分支", "几条", "十条", "条",
)


_COMMIT_WORD = r"提交|commit|修改|改动"
_COMMIT_SUMMARY_PATTERN = re.compile(r"总结|分析|概括|归纳|趋势|梳理")


def _author_from_commit_question(raw: str) -> str:
    cleaned = re.sub(r"今天|昨天|最近的?\s*\d{0,2}\s*条?", " ", raw)
    cleaned = re.sub(r"提交内容|提交了什么|提交了啥|的提交|提交|commit|修改|改动", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"帮我|查一下|看一下|内容|什么|啥|吗|呢|的|了|一下|记录|几条|十条|分析|总结|概括|归纳|趋势|梳理|报告|影响|隐患|风险|提升|意见|建议|评审|自检", " ", cleaned)
    latin = [item for item in re.findall(r"[A-Za-z][A-Za-z0-9._-]{1,40}", cleaned)
             if item.lower() not in ("main", "cbt2_bugfix", "origin", "git")]
    chinese = [word for word in re.findall(r"[\u4e00-\u9fff]{2,4}", cleaned) if word not in _AUTHOR_COMMIT_SKIP]
    return latin[0] if latin else (chinese[0] if chinese else "")


def parse_author_commit(text: str):
    """问某人今天/昨天/最近N条提交。返回 (作者, mode, value)；mode 为 day 或 recent。带文件路径的仍走文件记录。"""
    raw = text or ""
    if extract_git_path_token(raw) or not re.search(_COMMIT_WORD, raw, re.IGNORECASE):
        return None
    counted = re.search(r"最近的?\s*(\d{1,2})\s*条", raw)
    if counted:
        mode, value = "recent", min(max(int(counted.group(1)), 1), 20)
    elif re.search(r"最近.{0,8}(" + _COMMIT_WORD + ")", raw, re.IGNORECASE):
        mode, value = "recent", 10
    elif "今天" in raw:
        mode, value = "day", 0
    elif "昨天" in raw:
        mode, value = "day", 1
    else:
        return None
    return _author_from_commit_question(raw), mode, value


def format_author_commits(author: str, label: str, ref: str, log_text: str) -> str:
    lines = [line for line in (log_text or "").splitlines() if line.strip()]
    whose = f" {author} 的" if author else ""
    if not lines:
        return f"{label}在 `{ref}` 上没有{whose}提交。"
    who = f"{author} " if author else ""
    blocks = [f"{who}{label}在 `{ref}` 上有 {len(lines)} 条提交：", ""]
    for index, line in enumerate(lines, 1):
        parts = line.split("|", 3)
        if len(parts) == 4:
            short, name, date, subject = parts
            blocks.append(f"{index}. `{short}` {date} {name}\n   {subject}")
        else:
            blocks.append(f"{index}. {line}")
    if len(lines) >= 20:
        blocks.append("只列了最近 20 条。")
    return "\n".join(blocks)


def answer_author_commits(text: str, parsed: tuple) -> str:
    author, mode, value = parsed
    git_root = get_git_root()
    chat_id = getattr(CURRENT_CHAT, "id", "")
    branch, branch_error = select_git_branch(chat_id, text)
    if branch_error:
        return branch_error
    ref = git_ref(branch)
    fetch_ok, tip_or_err, sync_note = fetch_origin_branch(git_root, branch)
    if not fetch_ok:
        log(f"[git fetch failed] {tip_or_err}")
        tip_ok, _tip = run_git_readonly(git_root, ["rev-parse", "--short", ref])
        if not tip_ok:
            return f"无法同步远端，且本地没有 {ref}：{tip_or_err}"
    args = [
        "log", ref,
        "--date=format:%Y-%m-%d %H:%M",
        "--pretty=format:%h|%an|%ad|%s",
    ]
    if mode == "day":
        now = datetime.datetime.now(BEIJING_TZ)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0) - datetime.timedelta(days=value)
        end = start + datetime.timedelta(days=1)
        day_name = "昨天" if value else "今天"
        label = f"{day_name}（{start:%Y-%m-%d}）"
        args.extend(["-n20", f"--since={start:%Y-%m-%dT%H:%M:%S}+08:00", f"--until={end:%Y-%m-%dT%H:%M:%S}+08:00"])
        log(f"[git] author={author or '*'} ref={ref} day={start:%Y-%m-%d}")
    else:
        label = f"最近 {value} 条"
        args.append(f"-n{value}")
        log(f"[git] author={author or '*'} ref={ref} recent={value}")
    if author:
        args.append("--author=" + re.escape(author))
    ok, output = run_git_readonly(git_root, args)
    if not ok:
        return f"查 {ref} 失败：{output}"
    listing = format_author_commits(author, label, ref, output)
    if not output.strip():
        return listing
    if _COMMIT_REPORT_PATTERN.search(text or "") and LLM_ENABLED:
        report, _diffs = commit_report(git_root, output)
        if report:
            return f"{report}\n\n范围：`{ref}` {author or ''} {label}。同步：{sync_note}"
    if _COMMIT_SUMMARY_PATTERN.search(text or "") and output.strip() and LLM_ENABLED:
        summary = summarize_commit_list(output)
        if summary:
            return f"{summary}\n\n{listing}\n同步：{sync_note}"
    return listing + f"\n同步：{sync_note}"


COMMIT_SUMMARY_PROMPT = (
    "下面是 git log 的真实输出，每行是 提交号|作者|时间|说明。\n"
    "只根据这些说明写 2～4 句中文总结：主要改了哪几类东西、各几条。\n"
    "只能用说明里出现的词，不要猜角色名、模块或效果。合并提交单独算一类，写成「合并 N 条」。\n"
    "不要写趋势、意义、闭环这类评价。"
)


_COMMIT_REPORT_PATTERN = re.compile(r"报告|影响|隐患|风险|提升|意见|建议|评审|自检|review", re.IGNORECASE)
_REPORT_DIFF_SUFFIX = (".cs", ".shader", ".hlsl", ".cginc", ".compute", ".py", ".lua", ".json", ".bat", ".ps1", ".md", ".asset")
_REPORT_DIFF_PER_FILE = 9000
_REPORT_DIFF_PER_COMMIT = 9000
_REPORT_MAX_COMMITS = 10
COMMIT_FACTS_PROMPT = (
    "下面是一次真实 git show：提交号、说明、文件统计、代码类文件的 diff（资源文件只有统计）。\n"
    "只根据 diff 写评审要点，按下面四行输出，每行不超过 120 字：\n"
    "改动：具体改了哪些函数、宏、属性、Pass、关键字、数值（写出名字和前后值）。\n"
    "影响：会影响哪些 Shader/材质/系统/平台，依据是什么。\n"
    "提升：diff 能直接证明的收益，例如少了采样、删了分支、去掉重复代码；证明不了就写「看不出」。\n"
    "隐患：diff 里能看到的具体风险，例如删了仍可能被引用的属性或关键字、新增 keyword 变体、精度或平台差异、"
    "魔法数、缺判空、只改一边的 include；修改合理、没有具体风险就写「无」，不要用「可能」凑一条。\n"
    "规则：不要复述提交说明，不要写「可能需要测试」「更流畅」这类空话；只有统计没有 diff 时，四行都按统计写，并注明「只看到文件统计」。"
)
COMMIT_REPORT_PROMPT = (
    "下面是若干提交的评审要点，均出自真实 diff，每段开头【】里是提交号。\n"
    "只挑出有具体依据的问题：例如删了仍可能被引用的属性/宏/常量、新增 keyword 变体、平台或精度差异、魔法数、缺判空。\n"
    "有问题时写一节 **需要留意**，最多 5 条，每条写清问题和怎么查，末尾括号照抄该要点【】里的提交号，不得张冠李戴。\n"
    "修改合理、没有具体问题的提交不要提，也不要硬给意见。如果所有提交都没有具体问题，只输出一行：没看出明显问题，修改合理。\n"
    "规则：只能用要点里出现的信息，不得新增文件、函数、数值或效果；"
    "不写「可能需要测试」「兼容性测试」「确保按预期工作」「更流畅」这类空话。"
)
_REPORT_EMPTY_LINE = re.compile(r"^(提升|隐患)[：:](\s*(看不出|无|没有)[。.]?\s*$|.*(可能|看不出|难以|不确定|确保|只看到文件统计|无法确定))")
_REPORT_RISK_LINE = re.compile(r"^隐患[：:]")
REPORT_NO_ISSUE = "没看出明显问题，修改合理。"


def commit_overview(commits: list, merges: int) -> str:
    counts = {}
    for _short, _date, subject in commits:
        tag = re.match(r"\s*【([^】]{1,12})】", subject)
        name = tag.group(1) if tag else "其他"
        counts[name] = counts.get(name, 0) + 1
    parts = [f"{name} {count}" for name, count in sorted(counts.items(), key=lambda item: -item[1])]
    if merges:
        parts.append(f"合并 {merges}（无自身改动，未分析）")
    return f"共 {len(commits) + merges} 条：" + "，".join(parts) + "。"


def _commit_facts(git_root: str, short: str, date: str, subject: str) -> tuple:
    """返回 (要点文案, 裁剪后的代码类 diff)。模型失败时要点为空，diff 仍尽量保留。"""
    stat_ok, stat = run_git_readonly(git_root, ["show", "--stat=120", "--format=", short])
    diff_ok, diff = run_git_readonly(git_root, ["show", "--format=", "--unified=3", short])
    diff_text = _report_diff_text(diff if diff_ok else "")
    material = (
        f"提交 {short} {date}\n说明：{subject}\n文件统计：\n{(stat if stat_ok else '（没有）')[:2500]}\n"
        f"diff：\n{diff_text}"
    )
    try:
        body = call_chat(LLM_MODEL, [
            {"role": "system", "content": COMMIT_FACTS_PROMPT},
            {"role": "user", "content": material},
        ], LLM_FALLBACK_MODELS, max_tokens=1024)
    except (urllib.error.URLError, TimeoutError, OSError, KeyError, ValueError, RuntimeError) as error:
        log(f"[git report] facts failed {short} {error}")
        return "", diff_text
    lines = [line for line in (body or "").strip().splitlines() if not _REPORT_EMPTY_LINE.match(line.strip())]
    if not lines:
        return "", diff_text
    return f"**【{short}】** {date} {subject}\n" + "\n".join(lines), diff_text


def _report_diff_text(diff: str) -> str:
    kept = []
    for chunk in re.split(r"(?m)^(?=diff --git )", diff or ""):
        head = chunk.split("\n", 1)[0]
        match = re.match(r"diff --git a/(\S+)", head)
        if not match or not match.group(1).lower().endswith(_REPORT_DIFF_SUFFIX):
            continue
        piece = chunk if len(chunk) <= _REPORT_DIFF_PER_FILE else chunk[:_REPORT_DIFF_PER_FILE] + "\n…（截断）"
        kept.append(piece)
    text = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), "\n".join(kept))
    return text[:_REPORT_DIFF_PER_COMMIT] if text else "（没有代码类文本 diff，只看统计）"


def commit_report(git_root: str, log_text: str) -> tuple:
    """按真实 diff 写分析报告。返回 (报告正文, {提交号: 裁剪 diff})。合并提交只计数，不送模型。"""
    commits = []
    merges = 0
    for line in (log_text or "").splitlines():
        parts = line.split("|", 3)
        if len(parts) < 4:
            continue
        short, _name, date, subject = parts
        parents_ok, parents = run_git_readonly(git_root, ["show", "-s", "--pretty=format:%P", short])
        if parents_ok and len((parents or "").split()) > 1:
            merges += 1
            continue
        commits.append((short, date, subject))
    commits = commits[:_REPORT_MAX_COMMITS]
    if not commits:
        return "", {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda c: _commit_facts(git_root, *c), commits))
    diffs = {short: (diff or "") for (short, _date, _subject), (_facts, diff) in zip(commits, results)}
    facts = [item for item, _diff in results if item]
    log(f"[git report] commits={len(commits)} facts={len(facts)} merges={merges}")
    if not facts:
        return "", diffs
    risky = [item for item in facts if any(_REPORT_RISK_LINE.match(line) for line in item.splitlines())]
    body = REPORT_NO_ISSUE
    if risky:
        try:
            body = call_chat(LLM_MODEL, [
                {"role": "system", "content": COMMIT_REPORT_PROMPT},
                {"role": "user", "content": "\n\n".join(risky)},
            ], LLM_FALLBACK_MODELS, max_tokens=2048)
        except (urllib.error.URLError, TimeoutError, OSError, KeyError, ValueError, RuntimeError) as error:
            log(f"[git report] llm failed {error}")
            body = ""
    missed = len(commits) - len(facts)
    miss_note = f"\n有 {missed} 条模型没读成，未列出。" if missed else ""
    blocks = [
        f"**分析报告**（按真实 diff）\n{commit_overview(commits, merges)}{miss_note}",
        "**逐条要点**\n\n" + "\n\n".join(facts),
    ]
    if (body or "").strip():
        blocks.append(body.strip())
    return "\n\n".join(blocks), diffs


def summarize_commit_list(log_text: str) -> str:
    try:
        body = call_chat(LLM_MODEL, [
            {"role": "system", "content": COMMIT_SUMMARY_PROMPT},
            {"role": "user", "content": log_text[:6000]},
        ], LLM_FALLBACK_MODELS)
    except (urllib.error.URLError, TimeoutError, OSError, KeyError, ValueError, RuntimeError) as error:
        log(f"[git summary] llm failed {error}")
        return ""
    return (body or "").strip()


SELF_CHECK_PATTERN = re.compile(r"自检|检查一下这些|评审这些|看看这些修改|这些修改.*(问题|对不对|怎么样)")
SELF_CHECK_PROMPT = (
    "下面是一次真实 git show。只根据提交说明、文件统计和 diff 做简短自检。\n"
    "先用一两句说改了什么，再写有没有明显问题。看不出来就写「从这次改动里看不出明确问题」。\n"
    "不要编造 diff 里没有的文件、函数或数值。"
)


def is_commit_self_check_request(text: str) -> bool:
    return bool(SELF_CHECK_PATTERN.search(text or ""))


def previous_turn_text(role: str) -> str:
    chat_id = getattr(CURRENT_CHAT, "id", "")
    with history_lock:
        turns = list(CHAT_HISTORY.get(chat_id, []))
    if turns and turns[-1].get("role") == "user":
        turns = turns[:-1]
    for turn in reversed(turns):
        if turn.get("role") == role:
            return str(turn.get("content") or "")
    return ""


def author_commit_log_lines(parsed: tuple, text: str) -> tuple:
    """返回 (ref, lines, error)。lines 每项是 hash|author|date|subject。"""
    author, mode, value = parsed
    git_root = get_git_root()
    chat_id = getattr(CURRENT_CHAT, "id", "")
    branch, branch_error = select_git_branch(chat_id, text)
    if branch_error:
        return "", [], branch_error
    ref = git_ref(branch)
    fetch_origin_branch(git_root, branch)
    args = ["log", ref, "--date=format:%Y-%m-%d %H:%M", "--pretty=format:%h|%an|%ad|%s"]
    if mode == "day":
        now = datetime.datetime.now(BEIJING_TZ)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0) - datetime.timedelta(days=value)
        end = start + datetime.timedelta(days=1)
        args.extend(["-n20", f"--since={start:%Y-%m-%dT%H:%M:%S}+08:00", f"--until={end:%Y-%m-%dT%H:%M:%S}+08:00"])
    else:
        args.append(f"-n{value}")
    if author:
        args.append("--author=" + re.escape(author))
    ok, output = run_git_readonly(git_root, args)
    if not ok:
        return ref, [], f"查 {ref} 失败：{output}"
    return ref, [line for line in (output or "").splitlines() if line.strip()], ""


def answer_commit_self_check(text: str) -> str:
    git_root = get_git_root()
    digests = []
    for item in _COMMIT_HASH.findall(text_without_urls(text or "")):
        if item.lower() not in {d.lower() for d in digests}:
            digests.append(item)
    if not digests:
        for item in _COMMIT_HASH.findall(text_without_urls(previous_turn_text("assistant"))):
            if item.lower() not in {d.lower() for d in digests}:
                digests.append(item)
    verified = []
    missing = []
    for digest in digests[:10]:
        ok, meta = run_git_readonly(git_root, [
            "show", "-s", "--date=format:%Y-%m-%d %H:%M",
            "--pretty=format:%h|%an|%ad|%s", digest,
        ])
        if ok and meta.strip():
            verified.append(meta.strip())
        else:
            missing.append(digest)
    note = ""
    if missing and digests:
        note = f"上一条里有 {len(missing)} 个提交号在仓库对不上，已丢掉，不拿来评。\n"
    if not verified:
        prev_user = previous_turn_text("user")
        parsed = parse_author_commit(prev_user)
        if parsed:
            ref, lines, error = author_commit_log_lines(parsed, prev_user)
            if error:
                return note + error
            verified = lines[:10]
            if verified:
                note += f"上一条提交号对不上仓库，已按 `{ref}` 上的真实记录重查后再自检。\n"
        if not verified:
            return (note + "没有可自检的真实提交。先说「zhuhui最近10条提交」或贴真实提交号，再让我自检。")
    blocks = [note + f"按仓库里的真实提交自检，共 {len(verified)} 条：", ""]
    for index, line in enumerate(verified[:5], 1):
        parts = line.split("|", 3)
        if len(parts) < 4:
            blocks.append(f"{index}. {line}")
            continue
        short, name, date, subject = parts
        head = f"`{short}` {date} {name}\n{subject}"
        stat_ok, stat = run_git_readonly(git_root, ["show", "--stat", "--format=", short])
        diff_ok, diff = run_git_readonly(git_root, ["show", "--format=", "--unified=1", short])
        parents_ok, parents = run_git_readonly(git_root, ["show", "-s", "--pretty=format:%P", short])
        if parents_ok and len((parents or "").split()) > 1:
            blocks.append(f"{index}. {head}\n这是合并提交，没有自身 diff，跳过。")
            continue
        if not LLM_ENABLED:
            blocks.append(f"{index}. {head}\n{(stat if stat_ok else '没有统计。')[:800]}")
            continue
        question = (
            f"问题：自检这次改动\n\n文件统计：\n{(stat if stat_ok else '（没有）')[:3000]}\n\n"
            f"diff：\n{(diff if diff_ok else '（没有文本 diff）')[:5000]}"
        )
        try:
            body = call_chat(LLM_MODEL, [
                {"role": "system", "content": SELF_CHECK_PROMPT},
                {"role": "user", "content": question},
            ], LLM_FALLBACK_MODELS)
        except (urllib.error.URLError, TimeoutError, OSError, KeyError, ValueError, RuntimeError) as error:
            log(f"[self-check] llm failed {error}")
            body = (stat if stat_ok else "模型没评成，只留统计。")[:800]
        blocks.append(f"{index}. {head}\n{body}")
    if len(verified) > 5:
        blocks.append(f"另外还有 {len(verified) - 5} 条，这次先评了前 5 条。")
    log(f"[self-check] verified={len(verified)} missing={len(missing)}")
    return "\n".join(blocks)


_COMMIT_HASH = re.compile(r"(?<![A-Za-z0-9])([0-9a-fA-F]{7,40})(?![A-Za-z0-9])")
_URL_IN_TEXT = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
COMMIT_DIFF_PROMPT = (
    "下面是一次 git show 的真实结果。只根据提交说明、文件统计和 diff 回答。\n"
    "先用几句话说明改了哪些文件、关键逻辑或数值。\n"
    "问题里如果问 bug：只写 diff 里能直接看出来的问题。看不出来就写「从这次改动里看不出明确的 bug」。\n"
    "不要编造 diff 里没有的文件、函数或数值，不要复述整段 diff。"
)


def text_without_urls(text: str) -> str:
    return _URL_IN_TEXT.sub(" ", text or "")


def commit_hash_in(text: str) -> str:
    # 链接参数里的十六进制（如 bilibili vd_source）不算提交号
    match = _COMMIT_HASH.search(text_without_urls(text))
    return match.group(1) if match else ""


def is_commit_detail_request(text: str) -> bool:
    digest = commit_hash_in(text)
    if not digest:
        return False
    if re.fullmatch(r"\s*[0-9a-fA-F]{7,40}\s*", text or ""):
        return True
    return re.search(r"改了|改的|什么内容|内容|bug|这条|这个提交|变更|diff", text or "", re.IGNORECASE) is not None


def answer_commit_detail(text: str) -> str:
    digest = commit_hash_in(text)
    git_root = get_git_root()
    meta_args = ["show", "-s", "--date=format:%Y-%m-%d %H:%M", "--pretty=format:%h%x1f%an%x1f%ad%x1f%P%x1f%s", digest]
    ok, meta = run_git_readonly(git_root, meta_args)
    if not ok:
        chat_id = getattr(CURRENT_CHAT, "id", "")
        branch, branch_error = select_git_branch(chat_id, text)
        if branch_error:
            return branch_error
        fetch_origin_branch(git_root, branch)
        ok, meta = run_git_readonly(git_root, meta_args)
    if not ok:
        return f"仓库里没有 `{digest}` 这条提交。"
    fields = meta.split("\x1f")
    if len(fields) < 5:
        return f"读到了 `{digest}`，但说明解析失败。"
    short, author, date, parents, subject = fields[0], fields[1], fields[2], fields[3], fields[4]
    head = f"`{short}` {date} {author}\n{subject}"
    if len(parents.split()) > 1:
        return head + "\n这是合并提交，没有自身的代码改动。看不出是不是这次带进了 bug。"
    stat_ok, stat = run_git_readonly(git_root, ["show", "--stat", "--format=", digest])
    diff_ok, diff = run_git_readonly(git_root, ["show", "--format=", "--unified=2", digest])
    stat = (stat if stat_ok else "")[:4000]
    diff = (diff if diff_ok else "")[:8000]
    if not LLM_ENABLED:
        return head + "\n" + (stat or "没有文本改动。") + "\n模型没开，不能判断 bug。"
    question = f"问题：{text}\n\n文件统计：\n{stat or '（没有）'}\n\ndiff：\n{diff or '（没有文本 diff）'}"
    try:
        body = call_chat(LLM_MODEL, [
            {"role": "system", "content": COMMIT_DIFF_PROMPT},
            {"role": "user", "content": question},
        ], LLM_FALLBACK_MODELS)
    except (urllib.error.URLError, TimeoutError, OSError, KeyError, ValueError, RuntimeError) as error:
        log(f"[git show] llm failed {error}")
        return head + "\n" + (stat or "没有文本改动。") + "\n这次没能判断 bug。"
    log(f"[git show] {short}")
    return head + "\n" + (body or stat or "没有文本改动。")


def answer_git(text: str) -> str:
    if bomb.is_bomb_request(text):
        return answer_bomb(text)
    if is_commit_self_check_request(text):
        parsed = parse_author_commit(text)
        if parsed and not commit_hash_in(text):
            return answer_author_commits(text, parsed)
        return answer_commit_self_check(text)
    if is_commit_detail_request(text):
        return answer_commit_detail(text)
    parsed_author = parse_author_commit(text)
    if parsed_author:
        return answer_author_commits(text, parsed_author)
    git_root = get_git_root()
    chat_id = getattr(CURRENT_CHAT, "id", "")
    branch, branch_error = select_git_branch(chat_id, text)
    if branch_error:
        return branch_error
    ref = git_ref(branch)
    text = git_query_text(text)
    token = extract_git_path_token(text)
    if not token:
        bare_path, bare_hits = bare_asset_file(text, git_root)
        if bare_path:
            token = bare_path
        elif bare_hits:
            preview = "\n".join(f"- {item}" for item in bare_hits)
            return f"找到多个同名文件，请用更完整路径再说一次：\n{preview}"
    if not token:
        if parse_git_branch(text):
            return (f"之后这个会话查 Git 用 `{ref}`。\n"
                    "把文件路径或文件名一起发来即可。不会改你本地当前分支。")
        return ("要查 Git 上传/提交记录，请带上文件路径或文件名。\n"
                "例如：@大豆 查 Client/Assets/Editor/Scene/SceneModelPreviewBrowser.cs 的上传记录\n"
                "或：@大豆 SceneModelPreviewBrowser.cs 提交历史\n"
                "查提交只开 main 和 CBT2_bugfix。朱辉说「切换到某分支」会真切工作区。查记录没写分支就用这个会话上次的，默认 main。")
    rel_path, candidates = resolve_git_file(token, git_root)
    if candidates:
        preview = "\n".join(f"- {item}" for item in candidates[:8])
        return f"找到多个同名文件，请用更完整路径再说一次：\n{preview}"
    if not rel_path:
        return (f"没在仓库里找到「{token}」。\n"
                "请确认路径（可用 Client/Assets/... 或完整文件名），例如：\n"
                "Assets/Editor/Scene/SceneModelPreviewBrowser.cs")

    fetch_ok, tip_or_err, sync_note = fetch_origin_branch(git_root, branch)
    tip = tip_or_err if fetch_ok else ""
    if not fetch_ok:
        log(f"[git fetch failed] {tip_or_err}")
        tip_ok, tip = run_git_readonly(git_root, ["rev-parse", "--short", ref])
        if not tip_ok:
            return f"无法同步远端，且本地没有 {ref}：{tip_or_err}"

    remote_url = get_remote_url(git_root)
    log(f"[git] root={git_root} ref={ref} path={rel_path} tip={tip} fetch_ok={fetch_ok}")
    ok, output = run_git_readonly(
        git_root,
        ["log", ref, f"-n{GIT_LOG_COUNT}", "--date=short", "--pretty=format:%h|%an|%ad|%s", "--", rel_path],
    )
    if not ok:
        return f"查 {ref} 失败：{output}"
    return format_git_log(rel_path, output, remote_url=remote_url, tip=tip, sync_note=sync_note, ref=ref)


def parse_prefab_particle_systems(prefab_text: str) -> list:
    """返回粒子系统挂载节点名列表（按文件出现顺序）。"""
    go_names = {}
    for go_id, body in PREFAB_GO_BLOCK.findall(prefab_text):
        name_match = re.search(r"^\s*m_Name:\s*(.*)\s*$", body, re.MULTILINE)
        name = (name_match.group(1).strip() if name_match else "") or "(未命名)"
        go_names[go_id] = name
    names = []
    for _ps_id, body in PREFAB_PS_BLOCK.findall(prefab_text):
        go_match = re.search(r"m_GameObject:\s*\{fileID:\s*(\d+)\}", body)
        go_id = go_match.group(1) if go_match else ""
        names.append(go_names.get(go_id, f"(未知节点:{go_id or '?'})"))
    return names


def answer_prefab_particles(text: str) -> str:
    git_root = get_git_root()
    token = extract_git_path_token(text)
    if not token or not token.lower().endswith(".prefab"):
        return ("要查粒子系统，请带上 `.prefab` 路径，例如：\n"
                "@大豆 Assets/Bundle/Sfx/Role_Luffy/Role_Luffy_attack_01.prefab 有几个粒子系统")
    rel_path, candidates = resolve_git_file(token, git_root)
    if candidates:
        preview = "\n".join(f"- {item}" for item in candidates[:8])
        return f"找到多个同名 prefab，请用更完整路径再说一次：\n{preview}"
    if not rel_path:
        return f"没在仓库里找到「{token}」。请确认 Assets/... 路径或文件名。"
    absolute = os.path.join(git_root, rel_path.replace("/", os.sep))
    if not os.path.isfile(absolute):
        return f"文件不存在：{rel_path}"
    try:
        # Unity prefab 可能很大，按文本读即可
        content = Path(absolute).read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        return f"读取失败：{error}"
    names = parse_prefab_particle_systems(content)
    log(f"[prefab] path={rel_path} particles={len(names)}")
    if not names:
        return (f"「{rel_path}」里没有找到 `ParticleSystem` 组件。\n"
                "说明：只统计本 prefab 内嵌组件，不含嵌套引用的外部 prefab。")
    lines = [f"「{rel_path}」共有 **{len(names)}** 个粒子系统（ParticleSystem）：", ""]
    for index, name in enumerate(names, 1):
        lines.append(f"{index}. {name}")
    lines.append("")
    lines.append("说明：只读 YAML 统计本文件内嵌组件；嵌套的其它 prefab 未展开。")
    return "\n".join(lines)


# ---------- 目录索引 / 纠正记忆 ----------

def load_dir_index() -> list:
    """Assets 下若干层目录，返回 Assets/... 相对路径列表。"""
    assets_root = os.path.join(PROJECT_ROOT, "Assets")
    dirs = []
    for dirpath, dirnames, _ in os.walk(assets_root):
        relative = os.path.relpath(dirpath, PROJECT_ROOT).replace("\\", "/")
        depth = relative.count("/")
        dirnames[:] = [name for name in dirnames if name not in DIR_INDEX_SKIP and not name.startswith(".")]
        if depth >= DIR_INDEX_DEPTH:
            dirnames[:] = []
        dirs.append(relative)
    return dirs


def load_notes() -> list:
    try:
        with open(NOTES_PATH, encoding="utf-8") as file:
            return json.load(file).get("items", [])
    except (OSError, ValueError):
        return []


def save_note(question: str, fact: str) -> None:
    if PREVIEWING:
        return
    items = load_notes()
    items.append({"question": question[:200], "fact": fact[:300]})
    items = items[-MAX_NOTES:]
    with open(NOTES_PATH, "w", encoding="utf-8") as file:
        json.dump({"items": items}, file, ensure_ascii=False, indent=2)


PEOPLE_PATH = os.path.join(DATA_DIR, "people.json")
PEOPLE_ROLES = ("项目管理员", "管理员", "美术总监", "主美", "制作人", "主策", "主程", "技术美术", "TA", "项目经理", "PM",
                "运维", "QA", "测试负责人", "负责人", "对接人", "组长", "主管")
_ROLE_ALT = "|".join(re.escape(role) for role in PEOPLE_ROLES)
PEOPLE_ASK_PATTERN = re.compile(
    rf"(?P<a>(?:[\u4e00-\u9fffA-Za-z0-9]{{0,6}}?)(?:{_ROLE_ALT}))(?:是谁|是哪位|叫什么|找谁|问谁)|"
    rf"谁是(?P<b>(?:[\u4e00-\u9fffA-Za-z0-9]{{0,6}}?)(?:{_ROLE_ALT}))", re.IGNORECASE)
PEOPLE_SET_PATTERN = re.compile(
    rf"^(?:不对|不是|错了|纠正|更正)?[，,\s]*(?:我们|项目的?|咱们)?(?P<role>(?:[\u4e00-\u9fffA-Za-z0-9]{{0,6}}?)(?:{_ROLE_ALT}))"
    rf"(?:是|叫|为)\s*@?(?P<name>[^\s，,。？?！!谁哪@]{{2,12}})[。！!\s]*$", re.IGNORECASE)


def load_people() -> dict:
    try:
        with open(PEOPLE_PATH, encoding="utf-8") as file:
            return json.load(file)
    except (OSError, ValueError):
        return {}


def save_person(role: str, name: str, by: str) -> None:
    if PREVIEWING:
        return
    people = load_people()
    people[role] = {"name": name, "by": by}
    with open(PEOPLE_PATH, "w", encoding="utf-8") as file:
        json.dump(people, file, ensure_ascii=False, indent=2)


def answer_people(text: str, sender: str) -> str:
    """人员问题只按记录回答，禁止模型编人名。"""
    setting = PEOPLE_SET_PATTERN.search(text.strip())
    if setting and re.search(r"不|吗|呢|吧|了|么|什么|怎么|多少|几", setting.group("name")):
        setting = None
    if setting:
        role, name = setting.group("role"), setting.group("name")
        save_person(role, name, sender)
        log(f"[people] {role}={name} by={sender}")
        return f"记住了：{role}是{name}。"
    asking = PEOPLE_ASK_PATTERN.search(text)
    if not asking:
        return ""
    role = asking.group("a") or asking.group("b")
    people = load_people()
    record = people.get(role) or next((value for key, value in people.items() if key.endswith(role) or role.endswith(key)), None)
    if record:
        return f"{role}是{record['name']}。"
    return f"我这边没有{role}的记录，不乱说名字。知道的同事直接告诉我「{role}是某某」，我记下来。"


CANNOT_DO_PATTERN = re.compile(
    r"(能|会|可以).{0,8}(帮我|给我)?(建模|建个模|做模型|绑骨|刷权重|K帧|k帧)|"
    r"(帮我|给我).{0,4}(建模|建个模|做个?模型|绑骨|刷权重)",
)
CANNOT_DO_ANSWERS = (
    ("建模", "不能。我做不了建模，也改不了工程里的模型。"),
    ("建个模", "不能。我做不了建模，也改不了工程里的模型。"),
    ("做模型", "不能。我做不了建模，也改不了工程里的模型。"),
    ("绑骨", "不能。我做不了绑骨。"),
    ("刷权重", "不能。我做不了刷权重。"),
    ("K帧", "不能。我做不了动画。"),
    ("k帧", "不能。我做不了动画。"),
)


DOC_POLICY_TEXT = ("不会。普通问答不搜、不引用内部文档。只有明确说要某份文档链接时，才用这台机器上已登录的飞书账号搜标题，只回标题和链接，不贴正文。"
                   "密码、薪资、绩效、合同、身份证这类内容不查。")
SECRET_DOC_PATTERN = re.compile(r"密码|口令|token|薪资|薪酬|工资|绩效|身份证|劳动合同|保密协议", re.IGNORECASE)
DOC_POLICY_PATTERN = re.compile(r"引用.{0,8}(内部|飞书|保密|机密).{0,6}(文档|资料|数据)|内部文档|保密数据|机密文档")


def doc_policy_answer(text: str) -> str:
    if SECRET_DOC_PATTERN.search(text) and re.search(r"查|搜|发|给|看|文档|内容|引用|读取", text):
        return "不能查。密码、薪资、绩效、合同、身份证这类内容我不搜，也不发到聊天里。"
    if DOC_POLICY_PATTERN.search(text) or re.search(r"会不会引用|会引用内部|引用内部", text):
        return DOC_POLICY_TEXT
    if DOC_RISK_PATTERN.search(text) and last_assistant_turn().startswith(DOC_POLICY_TEXT[:10]):
        return DOC_RISK_TEXT
    return ""


DOC_RISK_PATTERN = re.compile(r"危险|泄露|泄漏|安全吗|不安全|保密|机密|风险")
DOC_RISK_TEXT = ("不会把正文贴出来。搜文档用的是这台机器上已登录的飞书账号，能搜到的范围等于这个账号自己能搜到的文档，只回标题和链接。"
                 "打开链接还要看对方自己有没有权限。保密文档不要分享给无关账号。")


def last_assistant_turn() -> str:
    chat_id = getattr(CURRENT_CHAT, "id", "")
    with history_lock:
        turns = list(CHAT_HISTORY.get(chat_id, []))
    for turn in reversed(turns):
        if turn["role"] == "assistant":
            return turn["content"]
    return ""


def cannot_do_answer(text: str) -> str:
    if re.search(r"(发到|发去|同步到|推到).{0,8}(组|群)|P0.{0,8}任务|任务.{0,10}(发到|发给|推送|通知)", text):
        return "不能。我不会把任务发到群里，也改不了飞书任务列表。"
    if not CANNOT_DO_PATTERN.search(text):
        return ""
    for word, answer in CANNOT_DO_ANSWERS:
        if word in text:
            return answer
    return "不能。这个我做不了。"


def matching_note(text: str) -> str:
    """只有当前问题和纠正主题对得上，才返回那条事实。"""
    for item in load_notes()[-30:]:
        question = item.get("question", "")
        fact = item.get("fact", "")
        if not question or not fact:
            continue
        words = [word for word in re.findall(r"[\u4e00-\u9fff]{2}", question) if word not in ("什么", "哪个", "路径", "在哪", "模型")]
        if words and any(word in text for word in words):
            return fact
    return ""


def notes_context(text: str = "") -> str:
    fact = matching_note(text)
    return f"【目录事实】{fact}" if fact else ""


def path_terms(text: str) -> list:
    terms = []
    for word, mapped in PATH_SYNONYMS.items():
        if word in text:
            terms.extend(mapped)
    terms.extend(match.group(0) for match in re.finditer(r"[A-Za-z][A-Za-z0-9_]{2,}", text))
    seen = set()
    return [term for term in terms if not (term.lower() in seen or seen.add(term.lower()))]


def search_dirs(dir_index: list, terms: list) -> list:
    lowered = [term.lower() for term in terms]
    scored = []
    for relative in dir_index:
        parts = [part.lower() for part in relative.split("/")]
        hits = sum(1 for term in lowered if any(part == term or part.startswith(term) for part in parts))
        if hits == 0:
            continue
        # 命中词越多越靠前，同分时路径越短越靠前
        scored.append((-hits, relative.count("/"), relative))
    scored.sort()
    best = scored[0][0] if scored else 0
    return [relative for hits, _, relative in scored if hits == best][:MAX_PATH_HITS]


def answer_path(text: str, dir_index: list) -> str:
    fact = matching_note(text)
    if fact and PATH_QUESTION_PATTERN.search(text):
        return fact
    terms = path_terms(text)
    hits = search_dirs(dir_index, terms) if terms else []
    log(f"[path] terms={terms} hits={hits}")
    if not hits:
        return "工程里没找到对应目录。告诉我更具体的名字（比如角色英文名 Luffy），我再查。"
    user = f"同事问：{text}\n\n【工程里真实存在的目录】\n" + "\n".join(hits)
    return call_llm(PATH_PROMPT, user)


def think_plan(text: str) -> dict | None:
    try:
        content = call_llm(PLAN_PROMPT, f"同事这句话：{with_previous_turn(text)}", with_history=True)
    except (urllib.error.URLError, KeyError, ValueError, TimeoutError) as error:
        log(f"[plan failed] {error}")
        return None
    match = re.search(r"\{.*\}", content, re.DOTALL)
    try:
        raw = json.loads(match.group(0)) if match else {}
    except ValueError:
        raw = {}
    if not isinstance(raw, dict):
        return None

    def clean_list(value, limit):
        items = value if isinstance(value, list) else []
        return [str(item).strip() for item in items if str(item).strip()][:limit]

    plan_result = {
        "intent": raw.get("intent"),
        "keywords": clean_list(raw.get("keywords"), 3),
        "queries": clean_list(raw.get("queries"), 2),
        "goal": str(raw.get("goal") or "").strip()[:120],
        "shape": str(raw.get("shape") or "").strip()[:120],
    }
    plan_result["fresh"] = raw.get("fresh") is True or str(raw.get("fresh")).lower() == "true"
    set_brief(plan_result["goal"], plan_result["shape"])
    CURRENT_CHAT.queries = plan_result["queries"]
    CURRENT_CHAT.fresh = plan_result["fresh"]
    log(f"[think] goal={plan_result['goal']!r} shape={plan_result['shape']!r} queries={plan_result['queries']}")
    return plan_result


def set_brief(goal: str, shape: str) -> None:
    parts = []
    if goal:
        parts.append(f"同事真正想要：{goal}")
    if shape:
        parts.append(f"回答形式：{shape}")
    CURRENT_CHAT.brief = "；".join(parts)


def plan(text: str) -> dict:
    query = build_query(text)
    fallback = {"intent": "question" if query else "help", "keywords": [query] if query else []}
    if is_git_request(text):
        token = extract_git_path_token(text)
        fallback = {"intent": "git", "keywords": [token] if token else ([query] if query else ["git"])}
    elif WRITE_PATTERN.search(text):
        fallback = {"intent": "chat", "keywords": ["周报"] if "周报" in text else [query or "写作"]}
    elif ADVICE_PATTERN.search(text):
        fallback = {"intent": "question", "keywords": [query] if query else ["建议"]}
    result = think_plan(text)
    if result is None:
        return fallback
    keywords = result["keywords"]
    intent = result["intent"]
    if intent not in ("docs", "question", "chat", "git", "help", "web") or (not keywords and intent != "help"):
        return fallback
    if intent == "web":
        return result
    # 规则优先：明显 Git 查询不要被模型改成 question
    if is_git_request(text):
        return {"intent": "git", "keywords": keywords or fallback["keywords"]}
    # 模型判 git 但消息里没有文件名，不算查记录
    if intent == "git" and not extract_git_path_token(text):
        return {"intent": "question", "keywords": keywords}
    # 误判 help 但有主题 → 意见/问题走 question，写作走 chat
    if intent == "help" and keywords and not answer_capability(text, keywords):
        if WRITE_PATTERN.search(text):
            return {"intent": "chat", "keywords": keywords}
        return {"intent": "question", "keywords": keywords}
    # 征求意见却被标成 chat：改走 question，直接给建议（不再挂文档）
    if intent == "chat" and ADVICE_PATTERN.search(text) and not WRITE_PATTERN.search(text):
        return {"intent": "question", "keywords": keywords or ([query] if query else ["建议"])}
    return {"intent": intent, "keywords": keywords}


def should_analyze_project(text: str, keywords: list) -> bool:
    if PROJECT_BUG_PATTERN.search(text):
        return True
    token = extract_git_path_token(text)
    if token and any(token.lower().endswith(ext) for ext in (".cs", ".hlsl", ".shader", ".prefab", ".asset")):
        return True
    # 关键词像类名 / 文件名
    for item in keywords or []:
        if re.search(r"[A-Za-z]{3,}", item) and ("." in item or re.search(r"[A-Z][a-z]+[A-Z]", item)):
            return True
    return False


def answer_project_analysis(text: str, keywords: list, local_index: list) -> str:
    code_keys = build_code_keywords(text, keywords)
    materials = []
    # 1) 显式路径优先
    for relative, snippet in read_explicit_project_file(text):
        materials.append(f"【工程摘录】{relative}\n{redact(snippet)}")
    # 2) 按需搜 Scripts/Editor/Shader/MD~
    hits = search_code_ondemand(code_keys)
    seen = {item.split("\n", 1)[0].replace("【工程摘录】", "") for item in materials}
    for relative, snippet in hits:
        if relative in seen:
            continue
        materials.append(f"【工程摘录】{relative}\n{redact(snippet)}")
        if len(materials) >= MAX_CODE_HITS:
            break
    # 3) 回退到启动时本地索引（MD~/Editor/Scripts）
    if len(materials) < 2 and local_index:
        for relative, snippet in search_local(local_index, code_keys):
            if any(relative in item for item in materials):
                continue
            materials.append(f"【工程摘录】{relative}\n{redact(snippet)}")
            if len(materials) >= MAX_CODE_HITS:
                break
    log(f"[code-analyze] keys={code_keys} materials={len(materials)}")
    if materials:
        user = f"同事的问题：{text}\n\n检索词：{'、'.join(code_keys) or '（无）'}\n\n" + "\n\n".join(materials)
        return call_llm(CODE_ANALYZE_PROMPT, user)
    user = (f"同事的问题：{text}\n\n"
            "【说明】工程目录中未检索到明显相关代码摘录。"
            "请基于 Unity/C# 通用经验给出排查步骤，并标明「无工程直接证据」；不要编造本仓库路径。")
    return call_llm(CODE_ANALYZE_PROMPT, user)


LAZY_ANSWER_PATTERN = re.compile(
    r"搜索结果(中|里)?(没有|未|并未)|没有找到|未找到|找不到相关|没有记录|没有相关(的)?(信息|资料|内容)|"
    r"无法提供|无法回答|无法获取|我无法访问|作为(一个)?(AI|人工智能)|"
    r"建议(您|你)?(自行)?(查阅|查看|关注|访问|前往).{0,12}(官网|网站|平台|博客|社区|渠道)")
RETRY_HINT = "这次直接给出有用的内容。确实查不到的部分，说清原因并给一个可行的下一步，不要只说没有、不要把问题推回给对方。"


INTERNAL_PATTERN = re.compile(r"我们|咱们|本项目|项目里|项目中|工程|组里|群|同事|谁|负责人|公司|内部|仓库|航海王")


def review_answer(question: str, answer: str, allow_short: bool = False) -> str:
    body = (answer or "").strip()
    if not body:
        return "回答是空的"
    lazy = LAZY_ANSWER_PATTERN.search(body)
    if lazy and lazy.group(0) == "没有记录" and INTERNAL_PATTERN.search(question):
        lazy = None
    if lazy:
        return f"回答里有敷衍话「{lazy.group(0)}」"
    if not allow_short and len(question.strip()) >= 6 and len(body) < 15:
        return "回答太短，等于没答"
    if body.rstrip("？?。.!！") == question.strip().rstrip("？?。.!！"):
        return "照抄了问题"
    return ""


def answer_with_review(prompt: str, user: str, question: str, think: bool = False, allow_short: bool = False) -> tuple:
    answer = call_llm(prompt, user, with_history=True, think=think)
    problem = review_answer(question, answer, allow_short)
    if not problem:
        return answer, ""
    log(f"[review] {problem}，重答一次")
    retry = call_llm(prompt, f"{user}\n\n【上一版不合格：{problem}】{RETRY_HINT}", with_history=True, think=True)
    retry_problem = review_answer(question, retry, allow_short)
    if not retry_problem:
        return retry, ""
    return retry, retry_problem


CHAT_OPINION_PATTERN = re.compile(r"谁(更|比较|最)?(强|厉害|帅|好看|可爱)|你(最)?(喜欢|爱)(谁|哪个|哪位)|你(最)?喜欢.{1,12}(吗|么)")
PROJECT_WORD_PATTERN = re.compile(r"我们|咱们|项目|工程|组里|群里|同事|负责人|公司|内部|仓库|资源|贴图|模型|Shader|shader")


def is_chat_opinion(text: str) -> bool:
    return bool(CHAT_OPINION_PATTERN.search(text or "")) and not PROJECT_WORD_PATTERN.search(text or "")


def answer_question(text: str, keywords: list, local_index: list) -> str:
    if is_chat_opinion(text):
        return answer_chat(text)
    if should_analyze_project(text, keywords):
        return answer_project_analysis(text, keywords, local_index)
    log(f"[answer] text={text!r} keywords={keywords} (no code)")
    answer, problem = answer_with_review(ANSWER_PROMPT, f"同事的问题：{with_previous_turn(text)}", text)
    if not problem or INTERNAL_PATTERN.search(text):
        return answer
    # 凭已有知识答不上来：主动联网查一次
    log(f"[review] 本地答不好（{problem}），改为联网")
    web_answer = answer_web_general(text, keywords)
    if web_answer.startswith(WEB_BLOCKED_PREFIX) or review_answer(text, web_answer):
        return answer
    return web_answer


WEEKLY_TEMPLATE = ("本周完成：\n1. 【填写：完成的资源或功能，例如 某角色贴图返修】\n\n"
                   "问题与风险：\n1. 【填写：卡住的点和需要谁配合】\n\n"
                   "下周计划：\n1. 【填写：计划交付的内容和时间】\n\n"
                   "把这周做了什么发给我，我帮你整理成正文。")


def answer_chat(text: str, keywords: list | None = None, local_index: list | None = None) -> str:
    log(f"[chat] text={text!r} (no docs)")
    detail = re.sub(r"(帮我|给我|写|一份|一下|个|周报|吧|[，,。！!？?\s])", "", text)
    if "周报" in text and len(detail) < 6:
        return WEEKLY_TEMPLATE
    answer, _ = answer_with_review(CHAT_PROMPT, f"同事说：{with_previous_turn(text)}", text, allow_short=True)
    return tame_chat_emoji(text, answer)


def is_web_question(text: str) -> bool:
    return bool(WEB_QUERY_PATTERN.search(text))


def strip_tags(value: str) -> str:
    text = re.sub(r"<[^>]+>", "", value)
    return html.unescape(re.sub(r"\s+", " ", text)).strip()


APPLE_GAME_CHARTS = (
    ("https://itunes.apple.com/cn/rss/topgrossingapplications/limit=10/genre=6014/json",
     "中国区 App Store 游戏畅销榜"),
    ("https://itunes.apple.com/cn/rss/toppaidapplications/limit=10/genre=6014/json",
     "中国区 App Store 游戏付费榜"),
)
WEB_BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"


def apple_chart_text(url: str, label: str) -> dict | None:
    request = urllib.request.Request(url, headers={"User-Agent": WEB_BROWSER_UA})
    with urllib.request.urlopen(request, timeout=25) as response:
        feed = json.loads(response.read().decode("utf-8")).get("feed") or {}
    entries = feed.get("entry") or []
    if isinstance(entries, dict):
        entries = [entries]
    if not entries:
        return None
    updated = (feed.get("updated") or {}).get("label", "")
    lines = [f"更新时间：{updated}" if updated else "更新时间：接口未给",
             "说明：苹果公开榜。畅销榜按收入排序，但接口不提供流水金额、下载量和留存。价格「获取」表示免费下载。"]
    for index, entry in enumerate(entries[:10], 1):
        name = (entry.get("im:name") or {}).get("label", "").strip()
        artist = (entry.get("im:artist") or {}).get("label", "").strip()
        price = (entry.get("im:price") or {}).get("label", "").strip()
        release_node = entry.get("im:releaseDate") or {}
        release_attr = release_node.get("attributes")
        if isinstance(release_attr, dict):
            release = str(release_attr.get("label") or "")
        elif isinstance(release_attr, str):
            release = release_attr
        else:
            release = str(release_node.get("label") or "")
        summary = (entry.get("summary") or {}).get("label", "").replace("\r", " ").replace("\n", " ")
        summary = " ".join(summary.split())[:40]
        if not name:
            continue
        lines.append(f"{index}. {name}｜{artist}｜{release}｜{price}｜商店简介：{summary}")
    if len(lines) <= 2:
        return None
    return {"title": label, "url": url, "snippet": "\n".join(lines)}


def fetch_apple_game_charts() -> list:
    charts = []
    for url, label in APPLE_GAME_CHARTS:
        try:
            chart = apple_chart_text(url, label)
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as error:
            log(f"[web chart failed] {label} {error}")
            continue
        if chart:
            charts.append(chart)
    return charts


WEB_TOPIC_PATTERN = re.compile(
    r"(米哈游|mihoyo|腾讯|网易|叠纸|库洛|鹰角|原神|崩坏|星穹铁道|绝区零|鸣潮|明日方舟|GDC|Unite|SIGGRAPH|业界|行业|别家|其他游戏|网上|公开)"
    r".{0,20}(分享|演讲|讲座|怎么做|做法|技术|渲染|方案|管线|流程|教程)", re.IGNORECASE)
WEB_NEWS_PATTERN = re.compile(r"(最近|最新|近期|本月|这个月|这周|本周|今年).{0,8}(游戏|新游|手游|端游|版本|上线|发布|测试|新闻|动态)")
WEB_SKIP_HOSTS = ("baike.baidu.com", "zhihu.com/question", "dictionary", "so.com/link", "so.com/s?")


RECENT_DAYS = 60
RESULT_DATE_PATTERNS = (
    re.compile(r"(20\d\d)\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"),
    re.compile(r"(20\d\d)[-/.](\d{1,2})[-/.](\d{1,2})"),
)
URL_DATE_PATTERNS = (
    re.compile(r"/(20\d\d)/(\d{1,2})/(\d{1,2})/"),
    re.compile(r"[/_](20\d\d)(\d\d)(\d\d)(?=[A-Za-z_/.]|\d{0,4}[A-Za-z])"),
    re.compile(r"/(20\d\d)/(\d{1,2})/"),
    re.compile(r"[/_](20\d\d)(\d\d)/"),
)


def make_date(year: str, month: str, day: str = "1") -> datetime.date | None:
    try:
        return datetime.date(int(year), int(month), int(day))
    except ValueError:
        return None


BILIBILI_DATES = {}
VIDEO_PATTERN = re.compile(r"视频|实机|预告|PV|演示|首曝|首爆|直播|录像|教程视频|B站|b站|哔哩哔哩", re.IGNORECASE)


def is_bilibili(url: str) -> bool:
    return "bilibili.com" in url or "b23.tv" in url


BILIBILI_URL_PATTERN = re.compile(
    r"https?://(?:www\.)?(?:bilibili\.com/video/[^\s<>\"]+|b23\.tv/[A-Za-z0-9]+)",
    re.IGNORECASE,
)
BILIBILI_SUMMARY_PATTERN = re.compile(
    r"总结|摘要|概括|讲了什么|说了什么|视频内容|看了什么|主要内容|讲啥|啥内容"
)
VIDEO_SUMMARY_PROMPT = (
    "根据下面的视频资料写中文总结。\n"
    "只根据资料写，不要编造资料里没有的情节或数据。\n"
    "先写 1 句总览，再写 3～6 条要点。\n"
    "如果只有简介没有字幕，开头写「按简介整理」。\n"
    "如果有字幕，开头写「按字幕整理」。"
)


def bilibili_url_in(text: str) -> str:
    match = BILIBILI_URL_PATTERN.search(text or "")
    if not match:
        return ""
    return match.group(0).rstrip(")。．，,.;；！!？」'\"")


def is_bilibili_summary_request(text: str) -> bool:
    if not bilibili_url_in(text):
        return False
    return bool(BILIBILI_SUMMARY_PATTERN.search(text_without_urls(text)))


def bilibili_bvid(url: str) -> str:
    match = re.search(r"/video/(BV[0-9A-Za-z]{10})", url or "")
    return match.group(1) if match else ""


def resolve_bilibili_url(url: str) -> str:
    if "b23.tv" not in (url or "").lower():
        return url
    request = urllib.request.Request(url, headers=BILIBILI_HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            return response.geturl() or url
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        log(f"[bilibili resolve failed] {error}")
        return url


def fetch_bilibili_view(bvid: str) -> dict:
    api = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
    request = urllib.request.Request(api, headers=dict(BILIBILI_HEADERS, Cookie=bilibili_cookie()))
    with urllib.request.urlopen(request, timeout=10) as response:
        data = json.loads(response.read().decode("utf-8"))
    if int(data.get("code") or 0) != 0:
        raise ValueError(f"bilibili view code={data.get('code')}")
    view = data.get("data")
    if not isinstance(view, dict):
        raise ValueError("bilibili view empty")
    return view


def fetch_bilibili_subtitles(bvid: str, cid: int) -> str:
    api = f"https://api.bilibili.com/x/player/v2?bvid={bvid}&cid={cid}"
    request = urllib.request.Request(api, headers=dict(BILIBILI_HEADERS, Cookie=bilibili_cookie()))
    with urllib.request.urlopen(request, timeout=10) as response:
        data = json.loads(response.read().decode("utf-8"))
    tracks = (((data.get("data") or {}).get("subtitle") or {}).get("subtitles") or [])
    if not isinstance(tracks, list) or not tracks:
        return ""
    preferred = None
    for track in tracks:
        if not isinstance(track, dict):
            continue
        lang = str(track.get("lan") or "").lower()
        if lang in ("zh-cn", "zh-hans", "ai-zh", "zh"):
            preferred = track
            break
        if preferred is None:
            preferred = track
    if not preferred:
        return ""
    subtitle_url = str(preferred.get("subtitle_url") or "").strip()
    if not subtitle_url:
        return ""
    if subtitle_url.startswith("//"):
        subtitle_url = "https:" + subtitle_url
    request = urllib.request.Request(subtitle_url, headers=BILIBILI_HEADERS)
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    lines = []
    for item in payload.get("body") or []:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        if content:
            lines.append(content)
    return "\n".join(lines)


def bilibili_no_subtitle_text(title: str, owner: str, view: dict, desc: str, link: str) -> str:
    seconds = int(view.get("duration") or 0)
    length = f"，{seconds // 60} 分 {seconds % 60:02d} 秒" if seconds else ""
    reason = ("这个视频没有字幕。" if saved_bilibili_cookie()
              else "B 站 AI 字幕要登录才能读，大豆还没配 B 站登录（朱辉可以在后台填）。")
    shown = desc[:600] if desc else "（简介是空的）"
    return (f"**{title}**（{owner}{length}）\n"
            f"没拿到字幕，没法总结视频内容。{reason}\n"
            f"只看到简介，原文如下：\n{shown}\n\n{link}")


def answer_bilibili_summary(text: str) -> str:
    url = bilibili_url_in(text)
    if not url:
        return "没有看到 B 站链接。"
    resolved = resolve_bilibili_url(url)
    bvid = bilibili_bvid(resolved)
    if not bvid:
        return "这个链接里没有认出 BV 号。请发完整的 bilibili.com/video/BV… 地址。"
    try:
        view = fetch_bilibili_view(bvid)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as error:
        log(f"[bilibili summary] view failed {error}")
        return "B 站视频信息暂时读不到，过一会儿再试。"
    title = str(view.get("title") or "").strip() or bvid
    desc = str(view.get("desc") or "").strip()
    owner = str(((view.get("owner") or {}).get("name") or "")).strip() or "未知 UP"
    pages = view.get("pages") if isinstance(view.get("pages"), list) else []
    cid = 0
    if pages and isinstance(pages[0], dict):
        cid = int(pages[0].get("cid") or 0)
    if not cid:
        cid = int(view.get("cid") or 0)
    subtitle = ""
    if cid:
        try:
            subtitle = fetch_bilibili_subtitles(bvid, cid)
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as error:
            log(f"[bilibili summary] subtitle failed {error}")
            subtitle = ""
    link = f"https://www.bilibili.com/video/{bvid}/"
    source = "字幕" if subtitle else "简介"
    if not subtitle:
        log(f"[bilibili summary] {bvid} no subtitle")
        return bilibili_no_subtitle_text(title, owner, view, desc, link)
    if not LLM_ENABLED:
        body = desc[:1500] if desc else "没有简介。"
        return f"**{title}**（{owner}）\n按{source}整理：\n{body}\n\n{link}"
    material = (
        f"标题：{title}\nUP主：{owner}\n链接：{link}\n简介：\n{desc[:3000] or '（没有）'}\n"
    )
    if subtitle:
        material += f"\n字幕：\n{subtitle[:8000]}\n"
    try:
        body = call_chat(LLM_MODEL, [
            {"role": "system", "content": VIDEO_SUMMARY_PROMPT},
            {"role": "user", "content": material},
        ], LLM_FALLBACK_MODELS)
    except (urllib.error.URLError, TimeoutError, OSError, KeyError, ValueError, RuntimeError) as error:
        log(f"[bilibili summary] llm failed {error}")
        body = desc[:1500] if desc else "模型暂时没总结出来，简介也是空的。"
        return f"**{title}**（{owner}）\n按简介整理：\n{body}\n\n{link}"
    log(f"[bilibili summary] {bvid} source={source}")
    return f"**{title}**（{owner}）\n{body or f'按{source}整理：没有写出要点。'}\n\n{link}"


def bilibili_date(url: str) -> datetime.date | None:
    match = re.search(r"/video/(BV[0-9A-Za-z]{10})", url)
    if not match:
        return None
    bvid = match.group(1)
    if bvid in BILIBILI_DATES:
        return BILIBILI_DATES[bvid]
    api = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
    request = urllib.request.Request(api, headers={"User-Agent": WEB_BROWSER_UA, "Referer": "https://www.bilibili.com/"})
    published = None
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            data = json.loads(response.read().decode("utf-8"))
        pubdate = (data.get("data") or {}).get("pubdate")
        if pubdate:
            published = datetime.datetime.fromtimestamp(int(pubdate), BEIJING_TZ).date()
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        log(f"[bilibili date failed] {bvid} {error}")
    BILIBILI_DATES[bvid] = published
    return published


BILIBILI_COOKIE = {"value": ""}
BILIBILI_HEADERS = {"User-Agent": WEB_BROWSER_UA, "Referer": "https://www.bilibili.com/"}


BILIBILI_COOKIE_PATH = os.path.join(DATA_DIR, "bilibili_cookie.txt")
BILIBILI_COOKIE_MAX = 4000


def saved_bilibili_cookie() -> str:
    try:
        with open(BILIBILI_COOKIE_PATH, encoding="utf-8") as cookie_file:
            return cookie_file.read().strip()
    except OSError:
        return ""


def normalize_bilibili_cookie(raw: str) -> str:
    """接受整段 Cookie 或只贴 SESSDATA 的值。返回空串表示不合法。"""
    value = (raw or "").strip()
    value = re.sub(r"^cookie\s*:\s*", "", value, flags=re.IGNORECASE)
    if not value or len(value) > BILIBILI_COOKIE_MAX or re.search(r"[\r\n\x00-\x1f]", value):
        return ""
    if "=" not in value:
        value = "SESSDATA=" + value
    return value if "SESSDATA=" in value else ""


def bilibili_cookie() -> str:
    login = saved_bilibili_cookie()
    if not BILIBILI_COOKIE["value"]:
        try:
            request = urllib.request.Request("https://api.bilibili.com/x/frontend/finger/spi", headers=BILIBILI_HEADERS)
            with urllib.request.urlopen(request, timeout=8) as response:
                data = json.loads(response.read().decode("utf-8"))
            BILIBILI_COOKIE["value"] = "buvid3=" + str((data.get("data") or {}).get("b_3", ""))
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as error:
            log(f"[bilibili] buvid failed {error}")
    anonymous = BILIBILI_COOKIE["value"]
    if login and "buvid3=" not in login and anonymous:
        return f"{login}; {anonymous}"
    return login or anonymous


def bilibili_login_status() -> dict:
    """只回是否登录和昵称，不回 cookie 本身。"""
    if not saved_bilibili_cookie():
        return {"set": False, "login": False, "uname": "", "message": "没填 B 站登录 cookie，只能看简介，读不到 AI 字幕。"}
    request = urllib.request.Request("https://api.bilibili.com/x/web-interface/nav",
                                     headers=dict(BILIBILI_HEADERS, Cookie=bilibili_cookie()))
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as error:
        return {"set": True, "login": False, "uname": "", "message": f"填了 cookie，但连 B 站失败：{error}"}
    nav = data.get("data") or {}
    if nav.get("isLogin"):
        uname = str(nav.get("uname") or "")
        return {"set": True, "login": True, "uname": uname, "message": f"已登录 B 站：{uname}。能读 AI 字幕。"}
    return {"set": True, "login": False, "uname": "", "message": "填了 cookie，但 B 站说没登录。可能过期了，重新复制一份。"}


def control_bilibili_cookie(raw: str) -> dict:
    if not (raw or "").strip():
        try:
            os.remove(BILIBILI_COOKIE_PATH)
        except OSError:
            pass
        log("[bilibili] cookie cleared")
        return bilibili_login_status()
    value = normalize_bilibili_cookie(raw)
    if not value:
        return {"set": False, "login": False, "uname": "",
                "message": "格式不对。贴浏览器里 bilibili.com 的整段 Cookie，或只贴 SESSDATA 的值。"}
    with open(BILIBILI_COOKIE_PATH, "w", encoding="utf-8", newline="\n") as cookie_file:
        cookie_file.write(value)
    log("[bilibili] cookie saved")
    return bilibili_login_status()


BILIBILI_QR = {"key": "", "at": 0.0}
BILIBILI_QR_TTL = 170
BILIBILI_LOGIN_COOKIES = ("SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5", "sid")


def bilibili_qr_start() -> dict:
    """向 B 站要一个扫码登录二维码，返回 svg data uri。key 只留在内存里。"""
    vendor = os.path.join(ROOT_DIR, "vendor")
    if vendor not in sys.path:
        sys.path.insert(0, vendor)
    import segno
    request = urllib.request.Request("https://passport.bilibili.com/x/passport-login/web/qrcode/generate",
                                     headers=BILIBILI_HEADERS)
    with urllib.request.urlopen(request, timeout=10) as response:
        data = json.loads(response.read().decode("utf-8"))
    info = data.get("data") or {}
    if data.get("code") != 0 or not info.get("url") or not info.get("qrcode_key"):
        return {"ok": False, "message": f"B 站没给二维码：{data.get('message') or data.get('code')}"}
    BILIBILI_QR["key"] = str(info["qrcode_key"])
    BILIBILI_QR["at"] = time.time()
    image = segno.make(str(info["url"]), error="m").svg_data_uri(scale=5, border=2)
    return {"ok": True, "qr": image, "message": "用 B 站 App 扫一扫，然后在手机上点确认。"}


def bilibili_qr_poll() -> dict:
    """state: waiting / scanned / expired / done / error。成功时把登录 cookie 存到本机，不回传 cookie。"""
    key = BILIBILI_QR["key"]
    if not key or time.time() - BILIBILI_QR["at"] > BILIBILI_QR_TTL:
        BILIBILI_QR["key"] = ""
        return {"state": "expired", "message": "二维码过期了，重新点「扫码登录」。"}
    url = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll?" + urllib.parse.urlencode({"qrcode_key": key})
    request = urllib.request.Request(url, headers=BILIBILI_HEADERS)
    with urllib.request.urlopen(request, timeout=10) as response:
        data = json.loads(response.read().decode("utf-8"))
        set_cookies = response.headers.get_all("Set-Cookie") or []
    code = int((data.get("data") or {}).get("code", -1))
    if code == 86101:
        return {"state": "waiting", "message": "等你扫码…"}
    if code == 86090:
        return {"state": "scanned", "message": "已扫码，在手机上点确认。"}
    if code == 86038:
        BILIBILI_QR["key"] = ""
        return {"state": "expired", "message": "二维码过期了，重新点「扫码登录」。"}
    if code != 0:
        return {"state": "error", "message": f"B 站返回 {code}：{(data.get('data') or {}).get('message') or ''}"}
    BILIBILI_QR["key"] = ""
    jar = {}
    for header in set_cookies:
        pair = header.split(";", 1)[0]
        name, _sep, value = pair.partition("=")
        if name.strip() in BILIBILI_LOGIN_COOKIES and value.strip():
            jar[name.strip()] = value.strip()
    if "SESSDATA" not in jar:
        return {"state": "error", "message": "B 站说登录成功，但没拿到 SESSDATA。改用手动粘贴。"}
    view = control_bilibili_cookie("; ".join(f"{name}={value}" for name, value in jar.items()))
    return {"state": "done", "message": view.get("message", ""), "login": view.get("login", False)}


def bilibili_keyword(query: str) -> str:
    keyword = re.sub(r"20\d\d\s*年|\d{1,2}\s*月(份)?|B站|b站|哔哩哔哩|最近|最新|近期|有什么|有哪些|新的", " ", query)
    return " ".join(keyword.split()) or query


def search_bilibili(query: str, recent: bool = False) -> list:
    params = {"search_type": "video", "keyword": bilibili_keyword(query), "order": "click" if recent else "totalrank"}
    if recent:
        now = int(time.time())
        params.update({"pubtime_begin_s": now - 30 * 86400, "pubtime_end_s": now})
    url = "https://api.bilibili.com/x/web-interface/search/type?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers=dict(BILIBILI_HEADERS, Cookie=bilibili_cookie()))
    with urllib.request.urlopen(request, timeout=10) as response:
        data = json.loads(response.read().decode("utf-8"))
    if data.get("code") != 0:
        BILIBILI_COOKIE["value"] = ""
        raise ValueError(f"bilibili code={data.get('code')}")
    results = []
    for entry in (data.get("data") or {}).get("result") or []:
        bvid = entry.get("bvid")
        if not bvid:
            continue
        published = datetime.datetime.fromtimestamp(int(entry.get("pubdate") or 0), BEIJING_TZ).date()
        title = strip_tags(entry.get("title", ""))
        snippet = f"UP主：{entry.get('author', '')}，播放 {entry.get('play', '')}。{strip_tags(entry.get('description', ''))[:120]}"
        results.append({"title": title[:120], "url": f"https://www.bilibili.com/video/{bvid}/",
                        "snippet": snippet, "date": published.isoformat()})
        if len(results) >= 6:
            break
    log(f"[web] engine=bilibili query={params['keyword']} recent={recent} results={len(results)}")
    return results


def gather_bilibili(query: str, seen: set, recent: bool) -> list:
    try:
        found = search_bilibili(query, recent)
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as error:
        log(f"[web bilibili failed] {query} {error}")
        return []
    fresh = [item for item in found if item["url"] not in seen]
    seen.update(item["url"] for item in fresh)
    return fresh


def result_date(item: dict) -> datetime.date | None:
    if is_bilibili(item.get("url", "")):
        published = bilibili_date(item["url"])
        if published:
            return published
    today = datetime.datetime.now(BEIJING_TZ).date()
    head = f"{item.get('snippet', '')[:80]} {item.get('title', '')}"
    relative = re.search(r"(\d{1,2})\s*(分钟|小时|天)前|昨天|前天", head)
    if relative:
        if relative.group(0) == "昨天":
            return today - datetime.timedelta(days=1)
        if relative.group(0) == "前天":
            return today - datetime.timedelta(days=2)
        days = int(relative.group(1)) if relative.group(2) == "天" else 0
        return today - datetime.timedelta(days=days)
    for pattern in RESULT_DATE_PATTERNS:
        match = pattern.search(item.get("snippet", "")[:80])
        if match:
            return make_date(*match.groups())
    for pattern in URL_DATE_PATTERNS:
        match = pattern.search(item.get("url", ""))
        if match:
            return make_date(*match.groups())
    return None


def search_so(query: str, recent: bool = False) -> list:
    params = {"q": query}
    if recent:
        params["adv_t"] = "m"
    url = "https://www.so.com/s?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": WEB_BROWSER_UA, "Accept-Language": "zh-CN,zh;q=0.9"})
    with urllib.request.urlopen(request, timeout=20) as response:
        page = response.read().decode("utf-8", "replace")
    results = []
    for block in re.findall(r'<li class="res-list[^"]*".*?</li>', page, re.S):
        link_match = re.search(r"<h3[^>]*>\s*<a([^>]*)>(.*?)</a>", block, re.S)
        if not link_match:
            continue
        real_match = re.search(r'data-mdurl="([^"]+)"', link_match.group(1))
        href_match = re.search(r'href="([^"]+)"', link_match.group(1))
        link = html.unescape((real_match or href_match).group(1)) if (real_match or href_match) else ""
        title = strip_tags(link_match.group(2))
        snippet_match = re.search(r"data-result-summary[^>]*>(.*?)</span>", block, re.S)
        if not snippet_match:
            snippet_match = re.search(r'<p class="res-desc"[^>]*>(.*?)</p>', block, re.S)
        snippet = strip_tags(snippet_match.group(1)) if snippet_match else ""
        host = urllib.parse.urlparse(link).netloc.lower()
        if not title or not link.startswith("http") or host.endswith(("so.com", "360.com", "360kan.com")):
            continue
        if any(skip in link for skip in WEB_SKIP_HOSTS):
            continue
        results.append({"title": title[:120], "url": link, "snippet": snippet[:300]})
        if len(results) >= 6:
            break
    return results


def search_sogou(query: str, recent: bool = False) -> list:
    del recent
    url = "https://www.sogou.com/web?" + urllib.parse.urlencode({"query": query})
    request = urllib.request.Request(url, headers={"User-Agent": WEB_BROWSER_UA, "Accept-Language": "zh-CN,zh;q=0.9"})
    with urllib.request.urlopen(request, timeout=20) as response:
        page = response.read().decode("utf-8", "replace")
    results = []
    for block in re.findall(r'<div class="vrwrap".*?(?=<div class="vrwrap"|$)', page, re.S):
        title_match = re.search(r"<h3[^>]*>(.*?)</h3>", block, re.S)
        link_match = re.search(r'data-url="([^"]+)"', block)
        if not title_match or not link_match:
            continue
        link = html.unescape(link_match.group(1))
        title = strip_tags(title_match.group(1))
        body = re.sub(r"<script.*?</script>|<style.*?</style>", "", block[title_match.end():], flags=re.S)
        snippet = strip_tags(body)[:300]
        if not title or not link.startswith("http") or any(skip in link for skip in WEB_SKIP_HOSTS):
            continue
        results.append({"title": title[:120], "url": link, "snippet": snippet})
        if len(results) >= 6:
            break
    return results


WEB_CACHE = {}
WEB_CACHE_SECONDS = 1800
web_cache_lock = threading.Lock()


def search_web(query: str, recent: bool = False) -> list:
    now = time.time()
    key = (query, recent)
    with web_cache_lock:
        cached = WEB_CACHE.get(key)
    if cached and now - cached[0] < WEB_CACHE_SECONDS:
        return cached[1]
    results = search_web_uncached(query, recent)
    if results:
        with web_cache_lock:
            WEB_CACHE[key] = (now, results)
    return results


def search_web_uncached(query: str, recent: bool = False) -> list:
    engines = [("so", search_so), ("sogou", search_sogou)]
    # Bing 会把中文词拆成单字，返回小米、字典、节假日这类无关结果
    if not re.search(r"[\u4e00-\u9fff]", query):
        engines.append(("bing", search_bing))
    for name, engine in engines:
        try:
            results = engine(query, recent)
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            log(f"[web {name} failed] {query} {error}")
            continue
        if results:
            log(f"[web] engine={name} query={query} results={len(results)}")
            return results
    return []


RECENT_PATTERN = re.compile(r"最近|最新|近期|这周|本周|这个月|本月|今年|新出|刚出|刚上线|近来")


FRESH_TOPIC_PATTERN = re.compile(r"首曝|实机|预告|定档|发售|上线|公测|开测|新游|新作|新版本|更新|活动|新闻|爆料|发布会|直播")


SGE_GOLD_PAGE = "https://quote.eastmoney.com/globalfuture/AU9999.html"
SGE_GOLD_KLINE = (
    "https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=118.AU9999&klt=101&fqt=1&lmt=1"
    "&end=20500101&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
)


GOLD_ADVICE_PATTERN = re.compile(r"什么时候|何时|时机|买入|卖出|推荐|适合|抄底|加仓|定投|要不要买|能不能买|好不好买")


def is_gold_topic(text: str) -> bool:
    return bool(re.search(r"金价|黄金", text or "")) and "金条" not in (text or "")


def is_gold_advice_question(text: str) -> bool:
    return is_gold_topic(text) and bool(GOLD_ADVICE_PATTERN.search(text or ""))


def is_gold_price_question(text: str) -> bool:
    return is_gold_topic(text) and not is_gold_advice_question(text)


def format_gold_quote(bar: str) -> str:
    """日 K 字段：日期,开,收,高,低,量,额,振幅,涨跌幅,涨跌额,换手。盘中收盘价即最新价。"""
    parts = (bar or "").split(",")
    if len(parts) < 10:
        return ""
    try:
        close = float(parts[2])
        high = float(parts[3])
        low = float(parts[4])
        percent = float(parts[8])
        change = float(parts[9])
    except ValueError:
        return ""
    today = datetime.datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")
    when = "今日" if parts[0] == today else parts[0]
    lines = [f"上海黄金交易所 Au99.99（黄金9999）{when} {close:.2f} 元/克。"]
    lines.append(f"较上一交易日 {change:+.2f} 元/克（{percent:+.2f}%）。")
    lines.append(f"当日最高 {high:.2f}，最低 {low:.2f}。")
    lines.append(f"行情页：{SGE_GOLD_PAGE}")
    lines.append("这是现货金价，不是金条零售价。")
    return "\n".join(lines)


GOLD_CACHE_PATH = os.path.join(DATA_DIR, "gold_cache.json")
GOLD_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
GOLD_CACHE_SECONDS = 20 * 60


def gold_bar_from_payload(payload: dict) -> str:
    data = payload.get("data") if isinstance(payload, dict) else None
    bars = data.get("klines") if isinstance(data, dict) else None
    bar = bars[-1] if isinstance(bars, list) and bars else ""
    if isinstance(bar, str) and format_gold_quote(bar):
        return bar
    return ""


def save_gold_cache(bar: str) -> None:
    moment = datetime.datetime.now(BEIJING_TZ).isoformat(timespec="seconds")
    text = json.dumps({"bar": bar, "fetched_at": moment}, ensure_ascii=False)
    try:
        with open(GOLD_CACHE_PATH, "w", encoding="utf-8") as cache_file:
            cache_file.write(text)
    except OSError as error:
        log(f"[gold] cache {error}")


def load_gold_cache() -> tuple[str, str]:
    try:
        with open(GOLD_CACHE_PATH, encoding="utf-8") as cache_file:
            payload = json.loads(cache_file.read())
    except (OSError, json.JSONDecodeError):
        return "", ""
    bar = str(payload.get("bar") or "")
    fetched = str(payload.get("fetched_at") or "")
    try:
        moment = datetime.datetime.fromisoformat(fetched)
    except ValueError:
        return "", ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=BEIJING_TZ)
    age = (datetime.datetime.now(BEIJING_TZ) - moment).total_seconds()
    if age < 0 or age > GOLD_CACHE_SECONDS or not format_gold_quote(bar):
        return "", ""
    return bar, moment.strftime("%H:%M")


SINA_GOLD_URL = "https://hq.sinajs.cn/list=gds_AU9999"
SINA_GOLD_REFERER = "https://finance.sina.com.cn/"


def sina_to_bar(text: str) -> str:
    """新浪 gds_AU9999：最新, ,买,卖,高,低,时间,昨收,今开,...,日期。"""
    matched = re.search(r'hq_str_gds_AU9999="([^"]*)"', text or "")
    if not matched or not matched.group(1):
        return ""
    parts = matched.group(1).split(",")
    if len(parts) < 13:
        return ""
    try:
        latest = float(parts[0])
        high = float(parts[4])
        low = float(parts[5])
        prev = float(parts[7])
        open_price = float(parts[8])
    except ValueError:
        return ""
    if latest <= 0 or prev <= 0:
        return ""
    change = latest - prev
    percent = change / prev * 100
    return f"{parts[12]},{open_price:.2f},{latest:.2f},{high:.2f},{low:.2f},0,0,0,{percent:.2f},{change:.2f},0"


def parse_gold_body(raw: bytes) -> str:
    if not raw:
        return ""
    text = raw.decode("utf-8", "replace").strip()
    if text.startswith("{"):
        try:
            return gold_bar_from_payload(json.loads(text))
        except json.JSONDecodeError:
            return ""
    return sina_to_bar(raw.decode("gbk", "replace"))


def fetch_gold_bar() -> str:
    last_error = None
    curl = shutil.which("curl") or "curl.exe"
    sources = ((SINA_GOLD_REFERER, SINA_GOLD_URL), (SGE_GOLD_PAGE, SGE_GOLD_KLINE))
    for referer, url in sources:
        try:
            completed = subprocess.run(
                [curl, "-sS", "-L", "--ipv4", "--http1.1", "--max-time", "10", "-A", GOLD_UA, "-e", referer, url],
                capture_output=True,
                timeout=15,
                creationflags=NO_WINDOW,
            )
            bar = parse_gold_body(completed.stdout)
            if bar:
                return bar
            detail = completed.stderr.decode("utf-8", "replace")[:160]
            last_error = RuntimeError(detail or f"curl {completed.returncode}")
        except (OSError, subprocess.TimeoutExpired, ValueError) as error:
            last_error = error
        request = urllib.request.Request(url, headers={"User-Agent": GOLD_UA, "Referer": referer, "Accept": "*/*"})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                bar = parse_gold_body(response.read())
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
            last_error = error
            continue
        if bar:
            return bar
    if last_error:
        log(f"[gold] {last_error}")
    return ""


def answer_gold_spot() -> str:
    bar = fetch_gold_bar()
    fetched_at = ""
    if bar:
        save_gold_cache(bar)
    else:
        bar, fetched_at = load_gold_cache()
    quote = format_gold_quote(bar) if bar else ""
    if quote and fetched_at:
        quote += f"\n取数时间 {fetched_at}。"
    return quote


GOLD_ADVICE_PROMPT = """你是「大豆」。同事在问黄金什么时候买或卖，不是只要一行现价。
下面有一段上海黄金交易所 Au99.99 行情。数字只能照抄，不要换算，不要改成金条零售价，不要编另一个价格。
规则：
- 先回答现在该怎么考虑买，再补充理由。不要只复述行情。
- 不要假装能预测明天涨跌。单日涨跌不能当买卖信号，这一点要写明。
- 讲常见做法：分批、定投、不把短期要用的钱一次买完，而不是等一个精确的最低点。
- 没有确切的「最好的某一天」。
- 不要写项目预算、美术建议，不要推荐金店品牌。
- 材料里的行情页链接可以附上。"""


def answer_gold_advice(text: str) -> str:
    quote = answer_gold_spot() or f"行情页暂时没读到数字。黄金9999在这里：{SGE_GOLD_PAGE}"
    user = f"同事的问题：{text}\n\n【今日行情，数字不可改】\n{quote}"
    try:
        return call_llm(GOLD_ADVICE_PROMPT, user, with_history=False, think=True)
    except (urllib.error.URLError, TimeoutError, OSError, KeyError, ValueError) as error:
        log(f"[gold advice] {error}")
        return quote


def is_price_question(text: str) -> bool:
    return bool(re.search(r"金价|黄金|银价|白银|汇率|油价|股价", text or "") and re.search(r"价|多少|现在|今日|今天|当前", text or ""))


GOLD_BAR_PATTERN = re.compile(r"金条|金店|周大福|老凤祥|六福|金至尊|菜百|周生生|投资金条|零售金")


def keep_spot_quote(item: dict) -> bool:
    """金价不要金条和品牌零售。"""
    blob = f"{item.get('title') or ''} {item.get('snippet') or ''}"
    return not GOLD_BAR_PATTERN.search(blob)


def is_fresh_question(text: str) -> bool:
    if getattr(CURRENT_CHAT, "fresh", False):
        return True
    return bool(RECENT_PATTERN.search(text) or WEB_NEWS_PATTERN.search(text) or FRESH_TOPIC_PATTERN.search(text))


def dated_query(text: str, base: str, recent: bool = False) -> str:
    if not (recent or RECENT_PATTERN.search(text)):
        return ""
    now = datetime.datetime.now(BEIJING_TZ)
    cleaned = RECENT_PATTERN.sub("", base).strip() or base
    return f"{now.year}年{now.month}月 {cleaned}"


def search_bing(query: str) -> list:
    url = "https://www.bing.com/search?" + urllib.parse.urlencode({"q": query, "setlang": "zh-Hans", "count": "10"})
    request = urllib.request.Request(url, headers={"User-Agent": WEB_BROWSER_UA, "Accept-Language": "zh-CN,zh;q=0.9"})
    with urllib.request.urlopen(request, timeout=20) as response:
        page = response.read().decode("utf-8", "replace")
    blocks = re.findall(r'<li class="b_algo".*?</li>', page, re.S)
    results = []
    for block in blocks:
        link_match = re.search(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        snippet_match = re.search(r"<p[^>]*>(.*?)</p>", block, re.S)
        if not link_match:
            continue
        title = strip_tags(link_match.group(2))
        snippet = strip_tags(snippet_match.group(1)) if snippet_match else ""
        link = html.unescape(link_match.group(1))
        if not title or not link.startswith("http"):
            continue
        if any(host in link for host in WEB_SKIP_HOSTS):
            continue
        results.append({"title": title[:120], "url": link, "snippet": snippet[:300]})
        if len(results) >= 5:
            break
    return results


WEB_KEEP_WORDS = ("游戏", "手游", "流水", "畅销", "收入", "下载", "sensor tower", "app store", "grossing", "revenue", "mobile game")


def search_web_relevant(text: str) -> list:
    if re.search(r"付费|畅销|top\s*10|top10|流水|排行", text, re.IGNORECASE):
        charts = fetch_apple_game_charts()
        if charts:
            return charts
    queries = [text[:80]]
    kept = []
    seen = set()
    for query in queries:
        try:
            found = search_web(query)
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            log(f"[web failed] {query} {error}")
            continue
        for item in found:
            blob = (item["title"] + item["snippet"]).lower()
            if not any(word in blob for word in WEB_KEEP_WORDS):
                continue
            if item["url"] in seen:
                continue
            seen.add(item["url"])
            kept.append(item)
            if len(kept) >= 5:
                return kept
    return kept


def format_public_charts(text: str, results: list) -> str:
    parts = []
    for item in results:
        parts.append(f"{item['title']}\n来源：{item['url']}\n{item['snippet']}")
    parts.append("畅销榜按收入排序，接口不提供流水金额。付费榜是买断标价，两张榜不是一回事。")
    parts.append("成功原因只保留榜单上看得到的事实，不把商店简介改写成研究结论，也不编下载量和留存。")
    if "每周" in text or "定时" in text:
        parts.append("每周自动更新还没开，上面是这一次的公开榜。")
    return "\n\n".join(parts)


def answer_web(text: str) -> str:
    if not LLM_ENABLED:
        return "查公开资料需要配置 LLM_API_KEY。"
    now = datetime.datetime.now(BEIJING_TZ)
    queries = [
        f"{now.year}年{now.month}月 中国手游畅销榜 流水",
        f"{now.year}年{now.month}月 付费游戏 排行",
    ]
    seen = set()
    articles = gather_web(queries, seen, 4, recent=True)
    articles = [item for item in articles if not re.search(r"202[0-4]|2[0-4]年", item["title"])]
    videos = gather_bilibili(queries[0], seen, True)[:4]
    charts = []
    for chart in fetch_apple_game_charts():
        if chart["url"] in seen:
            continue
        seen.add(chart["url"])
        charts.append(dict(chart, date=now.date().isoformat()))
    results = videos + articles + charts
    log(f"[web market] videos={len(videos)} articles={len(articles)} charts={len(charts)}")
    if not results:
        return "这次没收集到公开榜单，不编 Top10 和流水。"
    return format_market_reply(text, videos, articles, charts)


def format_market_reply(text: str, videos: list, articles: list, charts: list) -> str:
    lines = [f"这是刚才收集的公开材料（{now_text()}），不是固定稿。"]
    fresh = videos + articles
    if fresh:
        lines.append("近期流水、畅销：")
        for item in fresh[:6]:
            lines.append(f"- {item.get('date', '日期未知')} {item['title']}\n  {item['url']}")
    else:
        lines.append("网页和视频这次没收集到近期流水材料。")
    if charts:
        lines.append("中国区 App Store 刚刚拉到的榜（只有名次和价格，没有流水，也没有成功原因）：")
        for chart in charts:
            lines.append(f"{chart['title']}\n{chart['url']}")
            for line in chart["snippet"].splitlines():
                line = line.split("｜商店简介")[0]
                if line.startswith("说明"):
                    continue
                lines.append(line)
    lines.append("成功原因只能源自上面材料的原文。原文没写原因，这里不补。")
    if "每周" in text or "定时" in text:
        lines.append("每周自动更新还没开，这次只收集这一回。")
    return "\n".join(lines)


WEB_BLOCKED_PREFIX = "联网搜索这会儿被搜索引擎限流了"
REQUERY_PROMPT = """你在帮「大豆」换搜索词。上一轮搜索没拿到能回答问题的结果。
根据同事的问题、已经用过的搜索词和失败原因，给 2 条完全不同角度的新中文搜索词（换同义词、加具体年月/游戏名/会议名/网站名，或换成英文）。
只输出 JSON 数组，例如 ["搜索词1", "搜索词2"]。"""


def gather_web(queries: list, seen: set, limit: int, recent: bool = False, max_age: int | None = None) -> list:
    results = []
    undated = []
    CURRENT_CHAT.web_stale = getattr(CURRENT_CHAT, "web_stale", 0)
    for query in queries:
        try:
            found = search_web(query, recent)
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            log(f"[web failed] {query} {error}")
            continue
        for item in found:
            if item["url"] in seen:
                continue
            seen.add(item["url"])
            published = result_date(item)
            item = dict(item, date=published.isoformat() if published else "日期未知")
            if recent and published is None:
                undated.append(item)
                continue
            if recent and (datetime.datetime.now(BEIJING_TZ).date() - published).days > (max_age if max_age is not None else RECENT_DAYS):
                log(f"[web stale] {published} {item['title'][:40]}")
                CURRENT_CHAT.web_stale += 1
                continue
            results.append(item)
        if len(results) >= limit:
            break
    if max_age is not None:
        return results
    # 找不到发布日期的，只在有日期的结果不够时补位，并在材料里标「日期未知」
    return results + undated[:max(0, 2 - len(results))]


def requery(text: str, used: list, problem: str) -> list:
    user = f"同事的问题：{text}\n已用过的搜索词：{used}\n失败原因：{problem}"
    try:
        content = call_llm(REQUERY_PROMPT, user)
        match = re.search(r"\[.*\]", content, re.DOTALL)
        items = json.loads(match.group(0)) if match else []
    except (urllib.error.URLError, KeyError, ValueError, TimeoutError) as error:
        log(f"[requery failed] {error}")
        return []
    return [str(item).strip() for item in items if str(item).strip() and str(item).strip() not in used][:2]


def summarize_web(text: str, results: list) -> str:
    # B 站链接排前面，同类内容优先给 B 站
    results = sorted(results, key=lambda item: 0 if is_bilibili(item["url"]) else 1)
    materials = "\n\n".join(
        f"【搜索结果{index}】{item['title']}\n发布日期：{item.get('date', '日期未知')}\n{item['url']}\n{item['snippet']}"
        for index, item in enumerate(results[:6], 1)
    )
    prompt = WEB_GENERAL_PROMPT
    history = True
    if is_price_question(text):
        prompt += PRICE_ANSWER_RULES
        history = False
        CURRENT_CHAT.brief = ""
    return call_llm(prompt, f"同事的问题：{text}\n\n{materials}", with_history=history, think=True)


def answer_web_offline(text: str) -> str:
    if is_fresh_question(text):
        return f"{WEB_BLOCKED_PREFIX}，最近的消息查不到，过几分钟再问我。我不拿旧知识冒充最新消息。"
    if re.search(r"分享|演讲|讲座|文章|链接|原文|视频", text):
        note = "（这次没搜到原文和链接。下面只讲做法，不编演讲标题、讲者和年份）\n"
    else:
        note = "（这次没拿到网页。下面按通用经验说，不带链接，也不把没核实的数字说成事实）\n"
    answer = call_llm(WEB_OFFLINE_PROMPT, f"同事的问题：{text}", with_history=True, think=True)
    return note + answer


def answer_web_general(text: str, keywords: list, planned: list | None = None) -> str:
    if is_gold_price_question(text):
        try:
            quote = answer_gold_spot()
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as error:
            log(f"[gold] {error}")
            quote = ""
        if quote:
            return quote
        return f"行情页暂时没读到数字。黄金9999在这里：{SGE_GOLD_PAGE}"
    if not LLM_ENABLED:
        return "查公开资料需要配置 LLM_API_KEY。"
    queries = []
    base = " ".join(keywords[:3]).strip() or text[:60]
    price = is_price_question(text)
    recent = price or is_fresh_question(text)
    if price:
        now = datetime.datetime.now(BEIJING_TZ)
        today = f"{now.year}年{now.month}月{now.day}日 上海黄金交易所 Au99.99 元/克"
        queries.extend([today, "今日 现货黄金 元/克 上海黄金交易所"])
    else:
        for query in list(planned or []) + [dated_query(text, base, recent), base]:
            if query and query not in queries:
                queries.append(query)
    seen = set()
    CURRENT_CHAT.web_stale = 0
    results = []
    video = bool(VIDEO_PATTERN.search(text))
    if video:
        results = gather_bilibili((planned or [base])[0], seen, recent)[:5]
    if len(results) < 3:
        results += gather_web(queries, seen, 6, recent, 1 if price else None)
    if price:
        raw_count = len(results)
        results = [item for item in results if keep_spot_quote(item)]
        if raw_count and not results:
            return "搜到的是金条或金店零售价，不是金价。没查到今日上海黄金交易所的元/克报价。"
    if not results and not video and not price:
        # 网页搜索被限流时退到 B 站视频
        results = gather_bilibili((planned or [base])[0], seen, recent)[:5]
    log(f"[web general] queries={queries} results={len(results)} stale={CURRENT_CHAT.web_stale}")
    if not results and price:
        return "没有今天或昨天的人民币金价数字。更早的报价不拿来当现价。"
    if not results and CURRENT_CHAT.web_stale:
        return (f"搜到的 {CURRENT_CHAT.web_stale} 条都是两个月以前的旧消息，近期没查到相关报道。"
                "换个说法（带上游戏名或平台）我再查。")
    if not results:
        return answer_web_offline(text)
    answer = summarize_web(text, results)
    problem = review_answer(text, answer)
    if not problem:
        return answer
    # 结果不对题：换角度再搜一轮
    new_queries = requery(text, queries, problem)
    log(f"[review] web 回答不合格（{problem}），换搜索词 {new_queries}")
    more = [item for item in gather_web(new_queries, seen, 6, recent, 1 if price else None) if not price or keep_spot_quote(item)]
    if not more:
        return answer
    retry = summarize_web(text, more + results[:2])
    return retry if not review_answer(text, retry) else answer


# ---------- 处理消息 ----------

def is_help_request(text: str) -> bool:
    query = build_query(text)
    return not query or bool(HELP_PATTERN.search(query))


def is_gen_image_request(text: str) -> bool:
    return bool(GEN_IMAGE_PATTERN.search(text))


def answer_capability(text: str, keywords: list) -> str | None:
    if is_gen_image_request(text):
        return NO_GEN_IMAGE_TEXT
    blob = text + " " + " ".join(keywords)
    # 避免「图片」误伤普通提问：仅当在问能力时命中短词
    for word, answer in CAPABILITY_HINTS.items():
        if word in blob:
            return answer
    return None


def handle_without_llm(text: str) -> str:
    query = build_query(text)
    if is_help_request(text):
        return HELP_TEXT
    if is_prefab_particle_request(text):
        return answer_prefab_particles(text)
    weekly_target = weekly_report_target(text)
    if weekly_target:
        return answer_weekly_report(weekly_target)
    if is_review_info_question(text):
        return answer_review_info()
    if is_git_request(text):
        return answer_git(text)
    return answer_docs(text, [query])


DIR_INDEX = []
LAST_QUESTION = {}
LAST_PREFAB_HITS = {}
PREFAB_COUNT_PATTERN = re.compile(r"(有几个|有多少|几个|多少个).{0,12}(预制体|prefab)|预制体.{0,8}(有几个|有多少|几个)", re.IGNORECASE)
PREFAB_FOLLOWUP_PATTERN = re.compile(r"^(哪\d*个|哪几个|哪些|具体是哪些|列出来|都有哪些)[？?。]*$")
PREFAB_NAME_ALIASES = {
    "路飞": ["luffy", "monkeydluffy"],
    "索隆": ["zoro"],
    "娜美": ["nami"],
    "乌索普": ["usopp"],
    "山治": ["sanji"],
    "乔巴": ["chopper"],
    "罗宾": ["robin"],
    "弗兰奇": ["franky"],
    "布鲁克": ["brook"],
    "艾斯": ["ace"],
}
PREFAB_LIST_LIMIT = 40


PATH_TOKEN_SKIP = {"assets", "bundle", "character", "role", "maincity", "prefab", "lod", "lod1", "lod2", "lod3",
                   "client", "model", "common", "cs", "poshadow", "fbx"}
FILE_STAT_PATTERN = re.compile(r"多少面|几面|面数|三角|顶点|几根骨|骨骼数")
STAT_FOLLOWUP_LEFTOVER = re.compile(
    r"^(?:那?这个呢?|呢|也是|同样|一样|再看看|再看下|换这个|这个也|那这个|这个预制体呢|那这个预制体呢)$"
)
GUID_INDEX = {"time": 0.0, "map": {}}


def is_stat_followup(previous: str, question: str) -> bool:
    """上一句在问面数，这句只换了路径，例如「Role_Ace_CS.prefab 这个呢」。"""
    if not previous or not FILE_STAT_PATTERN.search(previous) or FILE_STAT_PATTERN.search(question):
        return False
    if re.search(r"更新|提交|改动|历史|谁|pass|材质|shader", question, re.IGNORECASE):
        return False
    if not extract_git_path_token(question):
        return False
    return bool(STAT_FOLLOWUP_LEFTOVER.match(followup_leftover(question)))


def followup_leftover(question: str) -> str:
    leftover = GIT_PATH_PATTERN.sub("", question)
    leftover = re.sub(r"@_user_\d+|@大豆", "", leftover)
    leftover = re.sub(r"[\s?？。!！,，.的]+", "", leftover)
    return leftover


def prefab_name_tokens(text: str) -> list:
    tokens = []
    for word, aliases in PREFAB_NAME_ALIASES.items():
        if word in text:
            tokens.extend(aliases)
    tokens.extend(match.group(0).lower() for match in re.finditer(r"[A-Za-z][A-Za-z0-9_]{2,}", text)
                  if match.group(0).lower() not in PATH_TOKEN_SKIP)
    seen = set()
    return [token for token in tokens if token not in PATH_TOKEN_SKIP and not (token in seen or seen.add(token))]


def prefab_name_has_token(filename: str, token: str) -> bool:
    """ace 只匹配 Role_Ace，不匹配 Luffy_Face 里的 ace。"""
    stem = filename.lower()
    if stem.endswith(".prefab"):
        stem = stem[:-len(".prefab")]
    parts = [part for part in re.split(r"[^a-z0-9]+", stem) if part]
    return token.lower() in parts


def guid_to_asset(guid: str) -> str:
    now = time.time()
    if not GUID_INDEX["map"] or now - GUID_INDEX["time"] > 600:
        mapping = {}
        assets_root = os.path.join(PROJECT_ROOT, "Assets")
        for dirpath, dirnames, filenames in os.walk(assets_root):
            dirnames[:] = [name for name in dirnames if name not in ("Library", "Temp", "obj") and not name.startswith(".")]
            for name in filenames:
                if not name.endswith(".meta"):
                    continue
                meta_path = os.path.join(dirpath, name)
                try:
                    with open(meta_path, "r", encoding="utf-8", errors="ignore") as meta_file:
                        head = meta_file.read(180)
                except OSError:
                    continue
                match = re.search(r"guid:\s*([0-9a-f]{32})", head)
                if match:
                    mapping[match.group(1)] = meta_path[:-5]
        GUID_INDEX["map"] = mapping
        GUID_INDEX["time"] = now
    return GUID_INDEX["map"].get(guid, "")


def prefab_meshes(prefab_text: str) -> list:
    names = {}
    kind = ""
    block_id = ""
    game_object = ""
    mesh_guid = ""
    found = []

    def flush() -> None:
        nonlocal game_object, mesh_guid
        if kind in ("33", "137") and mesh_guid:
            found.append((names.get(game_object, "未命名"), mesh_guid))
        game_object = ""
        mesh_guid = ""

    for line in prefab_text.splitlines():
        header = re.match(r"--- !u!(\d+) &(\d+)", line)
        if header:
            flush()
            kind, block_id = header.group(1), header.group(2)
            continue
        if kind == "1" and line.strip().startswith("m_Name:"):
            names[block_id] = line.split(":", 1)[1].strip()
        elif kind in ("33", "137") and "m_GameObject:" in line:
            match = re.search(r"fileID:\s*(\d+)", line)
            if match:
                game_object = match.group(1)
        elif kind in ("33", "137") and line.strip().startswith("m_Mesh:"):
            match = re.search(r"guid:\s*([0-9a-f]{32})", line)
            if match:
                mesh_guid = match.group(1)
    flush()
    return found


def mesh_counts(asset_path: str) -> tuple:
    if not asset_path.lower().endswith((".asset", ".prefab")):
        return None
    try:
        with open(asset_path, "r", encoding="utf-8", errors="ignore") as asset_file:
            head = asset_file.read(8000)
    except OSError:
        return None
    if "Mesh:" not in head:
        return None
    indexes = [int(value) for value in re.findall(r"indexCount:\s*(\d+)", head)]
    vertices = [int(value) for value in re.findall(r"vertexCount:\s*(\d+)", head)]
    if not indexes and not vertices:
        return None
    return sum(indexes) // 3, sum(vertices)


def answer_file_stats(text: str) -> str:
    token = extract_git_path_token(text)
    if not token:
        return "要看面数或顶点数，把预制体路径一起发我。"
    git_root = get_git_root()
    relative, _candidates = resolve_git_file(token, git_root)
    if not relative:
        return f"没找到「{token}」。"
    absolute = os.path.join(git_root, *relative.split("/"))
    if not absolute.lower().endswith(".prefab"):
        return f"「{relative}」不是预制体。面数在它引用的 Mesh 上。"
    try:
        with open(absolute, "r", encoding="utf-8", errors="ignore") as prefab_file:
            prefab_text = prefab_file.read()
    except OSError as error:
        return f"读不到这个预制体：{error}"
    meshes = prefab_meshes(prefab_text)
    if not meshes:
        return f"「{relative}」里没有 Mesh，面数不在这个预制体上。"
    lines = [f"「{relative}」引用了 {len(meshes)} 个网格。三角面按 indexCount ÷ 3 算："]
    total_faces = 0
    total_vertices = 0
    missing = []
    for name, guid in meshes:
        asset_path = guid_to_asset(guid)
        counts = mesh_counts(asset_path) if asset_path else None
        if not counts:
            missing.append(name)
            continue
        faces, vertices = counts
        total_faces += faces
        total_vertices += vertices
        asset_name = os.path.basename(asset_path) if asset_path else guid
        lines.append(f"- {name}：三角面 {faces}，顶点 {vertices}（{asset_name}）")
    if total_faces or total_vertices:
        lines.append(f"合计：三角面 {total_faces}，顶点 {total_vertices}。")
    if missing:
        lines.append("这些网格不是可读的 Mesh 文件，面数没算进去：" + "、".join(missing))
    lines.append("预制体里不存面数，数字来自它引用的 Mesh。")
    log(f"[mesh] {relative} faces={total_faces} vertices={total_vertices} missing={len(missing)}")
    return "\n".join(lines)


REFERENCE_IN_PATTERN = re.compile(
    r"被谁引用|被哪些.{0,12}引用|谁引用|引用使用|被引用|"
    r"在哪(里|儿)?引用|哪里引用|引用在哪|"
    r"谁在用|被谁用|谁用了|用在哪|用到哪|被用在|被用了|"
    r"被什么引用|哪些资源引用|哪些地方用|引用关系|引用情况|引用链|谁在引用"
)
REF_GLOB = "*.{prefab,mat,unity,controller,overrideController,playable,anim,spriteatlas,preset}"
REF_CACHE = {}
REF_CACHE_TTL = 600
LAST_REF_HITS = {}
REF_SAMPLE_LIMIT = 20
REF_FULL_LIMIT = 200
REF_EXT_LABEL = {
    ".mat": "材质",
    ".prefab": "预制体",
    ".unity": "场景",
    ".asset": "资产",
    ".controller": "动画控制器",
    ".playable": "Timeline",
    ".spriteatlas": "图集",
    ".anim": "动画",
}


def is_ref_followup(previous: str, question: str) -> bool:
    """上一句在问被谁引用，这句只换了路径。"""
    if not previous or not REFERENCE_IN_PATTERN.search(previous) or REFERENCE_IN_PATTERN.search(question):
        return False
    if FILE_STAT_PATTERN.search(question) or not extract_git_path_token(question):
        return False
    return bool(STAT_FOLLOWUP_LEFTOVER.match(followup_leftover(question)))


def asset_guid_of(absolute: str) -> str:
    try:
        with open(absolute + ".meta", "r", encoding="utf-8", errors="ignore") as meta_file:
            head = meta_file.read(500)
    except OSError:
        return ""
    match = re.search(r"(?m)^guid:\s*([0-9a-fA-F]{32})\s*$", head)
    return match.group(1).lower() if match else ""


def sibling_mesh_guids(absolute: str) -> list:
    """角色预制体引用的是 fbx 旁边拆出来的 Mesh，不是 fbx 自己的 guid。"""
    if not absolute.lower().endswith(".fbx"):
        return []
    folder = os.path.dirname(absolute)
    found = []
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    for name in names:
        if not name.lower().endswith(".asset"):
            continue
        path = os.path.join(folder, name)
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as asset_file:
                head = asset_file.read(240)
        except OSError:
            continue
        if "Mesh:" not in head:
            continue
        guid = asset_guid_of(path)
        if guid:
            found.append(guid)
    return found


def try_ref_lock() -> bool:
    for _ in range(2):
        try:
            fd = os.open(REF_INDEX_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return True
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(REF_INDEX_LOCK) < 600:
                    return False
                os.remove(REF_INDEX_LOCK)
            except OSError:
                return False
    return False


def release_ref_lock() -> None:
    try:
        os.remove(REF_INDEX_LOCK)
    except OSError:
        pass


def build_ref_index() -> int:
    """把材质、预制体、场景里的 guid 收进本地索引。查询不再每次扫全库。"""
    if not try_ref_lock():
        log("[ref-index] skip, another build is running")
        return -1
    try:
        return _build_ref_index()
    finally:
        release_ref_lock()


def _build_ref_index() -> int:
    rg = shutil.which("rg")
    if not rg:
        log("[ref-index] rg not on PATH")
        return 0
    assets_root = os.path.join(PROJECT_ROOT, "Assets")
    tmp_path = REF_INDEX_PATH + ".tmp"
    if os.path.exists(tmp_path):
        os.remove(tmp_path)
    started = time.time()
    connection = sqlite3.connect(tmp_path)
    connection.execute("PRAGMA journal_mode=OFF")
    connection.execute("PRAGMA synchronous=OFF")
    connection.execute("CREATE TABLE refs (guid TEXT NOT NULL, path TEXT NOT NULL, PRIMARY KEY (guid, path))")
    command = [rg, "-o", "--glob", "*.prefab", "--glob", "*.mat", "--glob", "*.unity", "--glob", "*.playable",
               "--glob", "*.controller", "--glob", "*.overrideController", "guid: [0-9a-fA-F]{32}", "."]
    process = subprocess.Popen(
        command, cwd=assets_root, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW,
    )
    batch = []
    seen = set()
    total = 0
    line_pattern = re.compile(r"^(.*):guid:\s*([0-9a-fA-F]{32})\s*$")
    for line in process.stdout:
        match = line_pattern.match(line.strip())
        if not match:
            continue
        raw_path = match.group(1).replace("\\", "/")
        if os.path.isabs(raw_path):
            relative = os.path.relpath(raw_path, PROJECT_ROOT).replace("\\", "/")
        else:
            relative = "Assets/" + raw_path.lstrip("./")
        item = (match.group(2).lower(), relative)
        if item in seen:
            continue
        seen.add(item)
        batch.append(item)
        if len(batch) >= 4000:
            connection.executemany("INSERT OR IGNORE INTO refs (guid, path) VALUES (?, ?)", batch)
            total += len(batch)
            batch.clear()
            seen.clear()
    if batch:
        connection.executemany("INSERT OR IGNORE INTO refs (guid, path) VALUES (?, ?)", batch)
        total += len(batch)
    return_code = process.wait()
    if return_code not in (0, 1):
        connection.close()
        os.remove(tmp_path)
        log(f"[ref-index] rg exit={return_code}")
        return 0
    connection.commit()
    connection.close()
    os.replace(tmp_path, REF_INDEX_PATH)
    log(f"[ref-index] rows={total} seconds={time.time() - started:.0f}")
    return total


def seconds_until_ref_refresh() -> float:
    if not os.path.isfile(REF_INDEX_PATH):
        return 0
    age = time.time() - os.path.getmtime(REF_INDEX_PATH)
    return max(0.0, REF_INDEX_MAX_AGE - age)


def ref_index_loop() -> None:
    while True:
        wait = seconds_until_ref_refresh()
        if wait > 0:
            due = datetime.datetime.now(BEIJING_TZ) + datetime.timedelta(seconds=wait)
            log(f"[ref-index] next {due:%Y-%m-%d %H:%M}")
            time.sleep(wait)
        refresh_ref_index_if_stale()
        if seconds_until_ref_refresh() <= 0:
            time.sleep(30 * 60)


def refresh_ref_index_if_stale() -> None:
    if os.path.isfile(REF_INDEX_PATH) and time.time() - os.path.getmtime(REF_INDEX_PATH) < REF_INDEX_MAX_AGE:
        return
    try:
        build_ref_index()
    except (OSError, subprocess.TimeoutExpired, sqlite3.Error) as error:
        log(f"[ref-index] build failed {error}")


def query_ref_index(guids: list) -> list | None:
    if not os.path.isfile(REF_INDEX_PATH):
        return None
    placeholders = ",".join("?" * len(guids))
    try:
        connection = sqlite3.connect(f"file:{REF_INDEX_PATH}?mode=ro", uri=True)
    except sqlite3.Error as error:
        log(f"[ref-index] open failed {error}")
        return None
    try:
        rows = connection.execute(
            f"SELECT DISTINCT path FROM refs WHERE guid IN ({placeholders})", guids,
        ).fetchall()
    except sqlite3.Error as error:
        log(f"[ref-index] query failed {error}")
        return None
    finally:
        connection.close()
    return sorted({row[0] for row in rows})


def find_guid_users(guids) -> list:
    if isinstance(guids, str):
        guids = [guids]
    guids = sorted({item.lower() for item in guids if item})
    if not guids:
        return []
    indexed = query_ref_index(guids)
    if indexed is not None:
        return indexed
    log("[ref-index] missing, fallback scan")
    key = " ".join(guids)
    cached = REF_CACHE.get(key)
    if cached and time.time() - cached[0] < REF_CACHE_TTL:
        return list(cached[1])
    assets_root = os.path.join(PROJECT_ROOT, "Assets")
    rg = shutil.which("rg")
    if not rg:
        log("[ref] rg not on PATH")
        return None
    command = [rg, "-l", "--glob", REF_GLOB]
    for guid in guids:
        command.extend(["-e", guid])
    command.append(assets_root)
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=150,
            creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        log(f"[ref] rg failed {error}")
        return None
    if result.returncode not in (0, 1):
        log(f"[ref] rg exit={result.returncode} {(result.stderr or '')[:200]}")
        return None
    hits = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if line:
            hits.append(os.path.relpath(line, PROJECT_ROOT).replace("\\", "/"))
    hits = sorted(set(hits))
    REF_CACHE[key] = (time.time(), hits)
    return hits


def reference_bucket(path: str) -> str:
    parts = path.split("/")
    if len(parts) >= 5:
        return "/".join(parts[:5])
    if len(parts) >= 2:
        return "/".join(parts[:-1])
    return path


def format_reference_summary(name: str, guid: str, hits: list, note: str, headline: str = "", users: list | None = None) -> str:
    mats = []
    prefabs = []
    scenes = []
    others = []

    def take(path: str) -> None:
        ext = os.path.splitext(path)[1].lower()
        if ext == ".mat":
            mats.append(path)
        elif ext == ".prefab":
            prefabs.append(path)
        elif ext in (".unity", ".playable"):
            scenes.append(path)
        else:
            others.append(path)

    seen = set()
    for path in list(hits) + list(users or []):
        if path in seen:
            continue
        seen.add(path)
        take(path)
    if not mats and not prefabs and not scenes and not others:
        extra = note if note else ""
        return f"「{name}」（guid {guid}）没有查到引用它的材质或预制体。{extra}"
    lines = [headline or f"「{name}」"]
    if note:
        lines.append(note)

    def add_files(title: str, paths: list) -> None:
        if not paths:
            return
        lines.append(f"{title} {len(paths)} 个：")
        shown = paths[:REF_FULL_LIMIT]
        lines.extend(f"- {path}" for path in shown)
        if len(paths) > len(shown):
            lines.append(f"还有 {len(paths) - len(shown)} 个。回复「列出来」看全部。")

    add_files("材质", mats)
    add_files("预制体", prefabs)
    add_files("场景", scenes)
    add_files("其他", others)
    return "\n".join(lines)


def material_users(mat_paths: list, already: set) -> list | None:
    """贴图的 guid 写在材质上。再查这些材质被哪些预制体和场景使用。"""
    guids = []
    for relative in mat_paths:
        absolute = os.path.join(PROJECT_ROOT, *relative.split("/"))
        guid = asset_guid_of(absolute)
        if guid:
            guids.append(guid)
    if not guids:
        return []
    hits = find_guid_users(guids)
    if hits is None:
        return None
    users = [path for path in hits if path.lower().endswith((".prefab", ".unity", ".playable")) and path not in already]
    return sorted(set(users))


def format_reference_list(cached: dict) -> str:
    hits = cached["hits"]
    users = cached.get("users") or []
    shown = hits[:REF_FULL_LIMIT]
    lines = [f"「{cached['name']}」的 {len(hits)} 个直接引用："]
    lines.extend(f"{index}. {path}" for index, path in enumerate(shown, 1))
    if len(hits) > len(shown):
        lines.append(f"… 还有 {len(hits) - len(shown)} 个。")
    if users:
        lines.append(f"这些材质被 {len(users)} 个预制体或场景使用：")
        lines.extend(f"{index}. {path}" for index, path in enumerate(users[:REF_FULL_LIMIT], 1))
        if len(users) > REF_FULL_LIMIT:
            lines.append(f"… 还有 {len(users) - REF_FULL_LIMIT} 个。")
    return "\n".join(lines)


def answer_references(chat_id: str, text: str) -> str:
    token = extract_git_path_token(text)
    if not token:
        return "要查被谁引用，把资源路径一起发我。"
    git_root = get_git_root()
    relative, _candidates = resolve_git_file(token, git_root)
    if not relative:
        return f"没找到「{token}」。"
    absolute = os.path.join(git_root, *relative.split("/"))
    guid = asset_guid_of(absolute)
    if not guid:
        return f"「{relative}」旁边没有可读的 .meta，查不到 guid。"
    name = os.path.basename(absolute)
    mesh_guids = [item for item in sibling_mesh_guids(absolute) if item != guid]
    lower = absolute.lower()
    if mesh_guids:
        note = "预制体写的是这些 Mesh 的 guid，不是 fbx 自己的 guid。"
    elif lower.endswith((".png", ".tga", ".jpg", ".jpeg", ".psd", ".tif", ".tiff", ".exr")):
        note = ""
    elif lower.endswith(".mat"):
        note = "这里是直接写了这个材质 guid 的资源。"
    elif lower.endswith(".fbx"):
        note = "同目录没有拆出来的 Mesh。这里只算直接写了这个 fbx guid 的资源。"
    else:
        note = ""
    hits = find_guid_users([guid] + mesh_guids)
    if hits is None:
        return "引用扫描超时了，这次没查完。过一会儿再发一次。"
    users = []
    if lower.endswith((".png", ".tga", ".jpg", ".jpeg", ".psd", ".tif", ".tiff", ".exr")):
        mat_paths = [path for path in hits if path.lower().endswith(".mat")]
        users = material_users(mat_paths, set(hits))
        if users is None:
            return "引用扫描超时了，这次没查完。过一会儿再发一次。"
        if not users and mat_paths:
            note = "这些材质没有再被预制体或场景引用。"
    headline = ""
    if mesh_guids:
        headline = f"「{name}」旁边 {len(mesh_guids)} 个 Mesh 被 {len(hits)} 个资源直接引用。"
    LAST_REF_HITS[chat_id] = {"name": name, "guid": guid, "hits": hits, "users": users}
    log(f"[ref] {relative} guid={guid} meshes={len(mesh_guids)} hits={len(hits)} users={len(users)}")
    return format_reference_summary(name, guid, hits, note, headline, users)


CUSTOM_DATA_PATTERN = re.compile(r"自定义数据|custom\s*data|对应哪个选项|对应哪一项|哪个数字对应", re.IGNORECASE)
CUSTOM_CHANNEL_LINE = re.compile(
    r"//\s*x:(\S+)\s+y:(\S+)\s+z:(\S+)\s+w:(\S+)\s*\n\s*(?:half4|float4)\s+(custom[12])\b",
    re.IGNORECASE,
)


def is_custom_data_followup(previous: str, question: str) -> bool:
    """上一句在问粒子自定义数据，这句只补了 Shader 名。"""
    if not previous or not CUSTOM_DATA_PATTERN.search(previous) or CUSTOM_DATA_PATTERN.search(question):
        return False
    return bool(re.search(r"[A-Za-z][A-Za-z0-9_]{4,}", question))


def shader_name_token(text: str) -> str:
    names = re.findall(r"[A-Za-z][A-Za-z0-9_]{4,}", text or "")
    skip = {"custom", "shader", "particle", "manual"}
    names = [name for name in names if name.lower() not in skip]
    return max(names, key=len) if names else ""


def shader_sources(token: str) -> list:
    root = os.path.join(PROJECT_ROOT, "Assets", "Bundle", "Shader")
    if not token or not os.path.isdir(root):
        return []
    needle = token.lower()
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in ("Library", "Temp")]
        for name in filenames:
            if needle in name.lower() and name.lower().endswith((".shader", ".hlsl", ".cginc")):
                found.append(os.path.join(dirpath, name))
    found.sort(key=lambda path: (0 if path.lower().endswith(".shader") else 1, path.lower()))
    return found[:8]


def read_shader_text(path: str) -> str:
    chunks = []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as shader_file:
            text = shader_file.read()
    except OSError:
        return ""
    chunks.append(text)
    folder = os.path.dirname(path)
    for include in re.findall(r'#include\s+"([^"]+)"', text):
        if include.startswith("Packages/"):
            continue
        include_path = os.path.normpath(os.path.join(folder, include))
        if not os.path.isfile(include_path):
            continue
        try:
            with open(include_path, "r", encoding="utf-8", errors="ignore") as include_file:
                chunks.append(include_file.read())
        except OSError:
            continue
    return "\n".join(chunks)


def custom_channel_label(text: str, name: str, channel: str, raw: str) -> str:
    known = {
        "dissolve": "溶解",
        "distort": "扭曲强度",
        "edgewidth": "边缘宽度",
        "edgebrightness": "边缘亮度",
    }
    if raw.lower() not in ("?", "？"):
        return f"{raw}（{known.get(raw.lower(), raw)}）" if raw.lower() in known else raw
    if name == "custom1" and channel in ("x", "y") and "custom1.xy" in text and "_MainTex_ST" in text:
        side = "U" if channel == "x" else "V"
        return f"主贴图偏移 {side}。注释写的是问号，代码加在 _MainTex_ST.zw 上"
    if name == "custom2" and channel in ("x", "y") and "custom2.xy - 1" in text:
        return "主贴图缩放。注释写的是问号，代码先减 1"
    return "注释写的是问号"


def answer_shader_custom_data(text: str) -> str:
    token = shader_name_token(text)
    if not token:
        return "把 Shader 名一起发我，例如 UVEffect_Manual_UIClip。我按文件里的 Custom1、Custom2 对。"
    files = shader_sources(token)
    if not files:
        return f"Assets/Bundle/Shader 里没有名字带 {token} 的 Shader。"
    body = read_shader_text(files[0])
    channels = {}
    for match in CUSTOM_CHANNEL_LINE.finditer(body):
        channels[match.group(5).lower()] = {
            "x": match.group(1), "y": match.group(2), "z": match.group(3), "w": match.group(4),
        }
    if "custom1" not in channels and "custom2" not in channels:
        return f"「{os.path.basename(files[0])}」里没有 Custom1、Custom2 的通道注释。"
    lines = [f"「{os.path.basename(files[0])}」的粒子自定义数据："]
    if "_ShaderMode" in body and "v.custom1" in body:
        lines.append("Shader模式打开时不读粒子自定义数据，用材质上的流速、溶解、扭曲和边缘宽度。关掉才用下面的通道。")
    order = (("custom1", "Custom1"), ("custom2", "Custom2"))
    for key, title in order:
        if key not in channels:
            continue
        lines.append(f"{title}：")
        for channel in ("x", "y", "z", "w"):
            lines.append(f"- {title}.{channel}：{custom_channel_label(body, key, channel, channels[key][channel])}")
    if "v.custom2.w + 1" in body:
        lines.append("Custom2.w 进顶点后会先加 1，再拿去乘边缘颜色。")
    log(f"[shader-custom] token={token} file={os.path.basename(files[0])}")
    return "\n".join(lines)


def find_prefabs(tokens: list) -> list:
    if not tokens:
        return []
    hits = []
    assets_root = os.path.join(PROJECT_ROOT, "Assets")
    for dirpath, dirnames, filenames in os.walk(assets_root):
        dirnames[:] = [name for name in dirnames if name not in DIR_INDEX_SKIP and not name.startswith(".")]
        for name in filenames:
            if not name.lower().endswith(".prefab"):
                continue
            if any(prefab_name_has_token(name, token) for token in tokens):
                hits.append(os.path.relpath(os.path.join(dirpath, name), PROJECT_ROOT).replace("\\", "/"))
    hits.sort()
    return hits


def directory_tree(paths: list) -> dict:
    root = {"files": 0, "children": {}, "path": ""}
    for path in paths:
        node = root
        parts = path.split("/")
        for index, part in enumerate(parts[:-1], 1):
            node = node["children"].setdefault(part, {"files": 0, "children": {}, "path": "/".join(parts[:index])})
        node["files"] += 1
    return root


def inclusive_count(node: dict) -> int:
    return node["files"] + sum(inclusive_count(child) for child in node["children"].values())


def collapsed_node(node: dict) -> dict:
    # 中间只有一个子目录、自己没有文件时，合并成一条路径，避免父子被拆开
    while node["files"] == 0 and len(node["children"]) == 1:
        node = next(iter(node["children"].values()))
    return node


def directory_summary(paths: list, roots: int = 6, children: int = 5) -> list:
    """父目录数字包含全部下级。子目录缩进列出，是拆开看，不重复计入总数。"""

    def append_children(lines: list, node: dict, indent: str, depth: int) -> None:
        kids = sorted(node["children"].values(), key=lambda item: (-inclusive_count(item), item["path"]))
        if not kids:
            return
        if node["files"]:
            lines.append(f"{indent}- 本目录：{node['files']}")
        shown = kids[:children]
        for index, child in enumerate(shown):
            child = collapsed_node(child)
            total = inclusive_count(child)
            prefix = node["path"] + "/"
            name = child["path"][len(prefix):] if child["path"].startswith(prefix) else child["path"].split("/")[-1]
            lines.append(f"{indent}- {name}：{total}")
            # 只把最大的那一支再拆开，能看到子目录属于谁，又不会铺太长
            if depth == 0 and index == 0 and child["children"] and child["files"]:
                append_children(lines, child, indent + "  ", depth + 1)
        rest = kids[children:]
        if rest:
            rest_total = sum(inclusive_count(child) for child in rest)
            lines.append(f"{indent}- … 还有 {len(rest)} 个子目录，共 {rest_total} 个")

    top = collapsed_node(directory_tree(paths))
    ranked = sorted((collapsed_node(child) for child in top["children"].values()),
                    key=lambda node: (-inclusive_count(node), node["path"]))
    lines = ["按目录（数字包含它下面的全部子目录；缩进的是拆开看，不要再加进总数）："]
    for node in ranked[:roots]:
        lines.append(f"- {node['path']}：{inclusive_count(node)}")
        append_children(lines, node, "  ", 0)
    hidden = ranked[roots:]
    if hidden:
        hidden_total = sum(inclusive_count(node) for node in hidden)
        lines.append(f"- … 还有 {len(hidden)} 个目录，共 {hidden_total} 个")
    return lines


def format_prefab_hits(tokens: list, hits: list) -> str:
    label = "、".join(tokens)
    if not hits:
        return f"工程里没有文件名带 {label} 这一段的 prefab。"
    role_hits = [path for path in hits if "/Character/Role/" in path or "/Character/MainCity/" in path]
    lines = []
    if role_hits:
        lines.append(f"角色/主城预制体 {len(role_hits)} 个：")
        lines.extend(f"{index}. {path}" for index, path in enumerate(role_hits, 1))
        lines.append("")
    lines.append(f"全工程文件名里有 {label} 这一段的 prefab 共 {len(hits)} 个（含特效、UI、过场）。")
    if len(hits) == len(role_hits):
        return "\n".join(lines)
    lines.extend(directory_summary(hits))
    return "\n".join(lines)


ASSET_TYPES = (
    ("shadergraph", "Shader Graph", (".shadergraph",)),
    ("shader", "Shader", (".shader",)),
    ("hlsl", "HLSL", (".hlsl", ".cginc")),
    ("材质", "材质", (".mat",)),
    ("material", "材质", (".mat",)),
    ("贴图", "贴图", (".png", ".tga", ".psd", ".jpg", ".jpeg", ".exr", ".tif")),
    ("纹理", "贴图", (".png", ".tga", ".psd", ".jpg", ".jpeg", ".exr", ".tif")),
    ("texture", "贴图", (".png", ".tga", ".psd", ".jpg", ".jpeg", ".exr", ".tif")),
    ("模型", "模型", (".fbx", ".obj")),
    ("fbx", "模型", (".fbx",)),
    ("动画控制器", "动画控制器", (".controller",)),
    ("controller", "动画控制器", (".controller",)),
    ("动画", "动画片段", (".anim",)),
    ("anim", "动画片段", (".anim",)),
    ("场景", "场景", (".unity",)),
    ("scene", "场景", (".unity",)),
    ("timeline", "Timeline", (".playable",)),
    ("playable", "Timeline", (".playable",)),
    ("脚本", "C# 脚本", (".cs",)),
    ("代码", "C# 脚本", (".cs",)),
)
ASSET_COUNT_PATTERN = re.compile(r"有几个|有多少|多少个|几个|数量|统计|总共|一共", re.IGNORECASE)
ASSET_LIST_LIMIT = 60
RELATED_EXTENSIONS = {
    ".shader": ((".shadergraph", "Shader Graph"), (".shadervariants", "Shader 变体")),
}
LAST_ASSET_HITS = {}
EXTENSION_INDEX = {"time": 0.0, "files": {}}
extension_index_lock = threading.Lock()


def asset_type_for(text: str) -> tuple | None:
    lower = text.lower()
    if "预制体" in text or "prefab" in lower:
        return None
    for word, label, extensions in ASSET_TYPES:
        if word in lower:
            return label, extensions
    return None


def files_by_extension(root_name: str) -> dict:
    """按扩展名索引 Assets / Packages 下的文件，10 分钟内复用。"""
    with extension_index_lock:
        cached = EXTENSION_INDEX["files"].get(root_name)
        if cached is not None and time.time() - EXTENSION_INDEX["time"] < 600:
            return cached
    index = {}
    root = os.path.join(PROJECT_ROOT, root_name)
    for dirpath, dirnames, filenames in os.walk(root):
        # 统计要覆盖第三方插件目录，只跳过生成目录
        dirnames[:] = [name for name in dirnames if name not in ("Library", "Temp", "obj") and not name.startswith(".")]
        for name in filenames:
            extension = os.path.splitext(name)[1].lower()
            if extension and extension != ".meta":
                index.setdefault(extension, []).append(
                    os.path.relpath(os.path.join(dirpath, name), PROJECT_ROOT).replace("\\", "/"))
    with extension_index_lock:
        if time.time() - EXTENSION_INDEX["time"] >= 600:
            EXTENSION_INDEX["files"] = {}
            EXTENSION_INDEX["time"] = time.time()
        EXTENSION_INDEX["files"][root_name] = index
    return index


def answer_asset_count(text: str, chat_id: str) -> str:
    found = asset_type_for(text)
    if not found:
        return ""
    label, extensions = found
    assets_index = files_by_extension("Assets")
    hits = sorted(path for extension in extensions for path in assets_index.get(extension, []))
    packages_index = files_by_extension("Packages")
    package_count = sum(len(packages_index.get(extension, [])) for extension in extensions)
    LAST_ASSET_HITS[chat_id] = {"label": label, "hits": hits}
    LAST_PREFAB_HITS.pop(chat_id, None)
    log(f"[asset-count] {label} assets={len(hits)} packages={package_count}")
    lines = [f"Assets 下{label}共 {len(hits)} 个（{'、'.join(extensions)}）。"]
    if package_count:
        lines[0] += f"另外 Packages（引擎和插件包）里还有 {package_count} 个，没算进去。"
    related = []
    for extension in extensions:
        for other, other_label in RELATED_EXTENSIONS.get(extension, ()):
            count = len(assets_index.get(other, []))
            if count and other not in extensions:
                related.append(f"{other_label} {count} 个")
    if related:
        lines.append("相近但没算进上面的：" + "、".join(related) + "。")
    lines.extend(directory_summary(hits))
    lines.append("要看清单回我「列出来」。")
    return "\n".join(lines)


def format_asset_list(cached: dict) -> str:
    hits = cached["hits"]
    if not hits:
        return f"Assets 下没有{cached['label']}。"
    groups = {}
    for path in hits:
        groups.setdefault(path.rsplit("/", 1)[0], []).append(path)
    ranked = sorted(groups.items(), key=lambda item: (-len(item[1]), item[0]))
    lines = [f"{cached['label']}共 {len(hits)} 个，按目录从多到少列" +
             (f"，先列前 {ASSET_LIST_LIMIT} 个：" if len(hits) > ASSET_LIST_LIMIT else "：")]
    shown = 0
    for folder, paths in ranked:
        if shown >= ASSET_LIST_LIMIT:
            break
        lines.append(f"{folder}（{len(paths)}）")
        for path in sorted(paths):
            if shown >= ASSET_LIST_LIMIT:
                break
            shown += 1
            lines.append(f"{shown}. {path}")
    if len(hits) > shown:
        lines.append(f"… 还有 {len(hits) - shown} 个。要看某个目录，告诉我目录名。")
    return "\n".join(lines)


CHARACTER_PREFAB_ROOT = "Assets/Bundle/Character"
PREFAB_CATEGORIES = (
    ("主城", "MainCity", "主城角色"),
    ("精英", "Elite", "精英怪"),
    ("boss", "Boss", "Boss"),
    ("怪", "Monster", "怪物"),
    ("npc", "Npc", "NPC"),
    ("场景", "Scene", "场景角色"),
    ("角色", "Role", "战斗角色"),
    ("人物", "Role", "战斗角色"),
)


def count_category_prefabs(folder: str) -> dict:
    root = os.path.join(PROJECT_ROOT, *CHARACTER_PREFAB_ROOT.split("/"), folder)
    counts = {}
    for dirpath, _, filenames in os.walk(root):
        total = sum(1 for name in filenames if name.lower().endswith(".prefab"))
        if not total:
            continue
        relative = os.path.relpath(dirpath, root).replace("\\", "/")
        owner = relative.split("/", 1)[0] if relative != "." else "(根目录)"
        counts[owner] = counts.get(owner, 0) + total
    return counts


def answer_prefab_category(text: str) -> str:
    lower = text.lower()
    matched = [(folder, label) for word, folder, label in PREFAB_CATEGORIES if word in lower]
    if not matched:
        overview = []
        for _, folder, label in PREFAB_CATEGORIES[:7]:
            counts = count_category_prefabs(folder)
            overview.append(f"- {label}（{folder}）：{sum(counts.values())} 个")
        return ("要统计哪类？角色目录下各类预制体：\n" + "\n".join(overview) +
                "\n\n也可以带名字问，例如：项目里有几个路飞预制体。")
    folder, label = matched[0]
    counts = count_category_prefabs(folder)
    total = sum(counts.values())
    if not total:
        return f"{CHARACTER_PREFAB_ROOT}/{folder} 下没有 prefab。"
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    shown = ranked[:30]
    rest = ranked[30:]
    owners = "、".join(f"{name.replace('Role_', '').replace('MC_', '')} {count}" for name, count in shown)
    if rest:
        owners += f"，还有 {len(rest)} 个文件夹共 {sum(count for _, count in rest)} 个"
    lines = [f"{label}预制体共 {total} 个，分属 {len(counts)} 个文件夹（{CHARACTER_PREFAB_ROOT}/{folder}）。",
             f"按文件夹：{owners}"]
    if folder == "Role":
        city = count_category_prefabs("MainCity")
        lines.append(f"另外主城角色（MainCity）{sum(city.values())} 个，没算进上面。特效、UI 里的同名 prefab 也没算。")
    log(f"[prefab-category] folder={folder} total={total}")
    return "\n".join(lines)


def answer_prefab_count(text: str) -> str:
    tokens = prefab_name_tokens(text)
    if not tokens:
        return answer_prefab_category(text)
    hits = find_prefabs(tokens)
    log(f"[prefab-count] tokens={tokens} hits={len(hits)}")
    return format_prefab_hits(tokens, hits)


ANSWER_DOUBT_PATTERN = re.compile(r"哪里不对|回答对吗|对不对|这回答|到底.*不对")
SHADER_MEMORY_PATH = os.path.join(DATA_DIR, "last_shader.json")
SHADER_MEMORY_WINDOW = 30 * 60


def remember_shader_token(chat_id: str, text: str) -> None:
    token = shader_name_token(text)
    if not chat_id or not token or not shader_sources(token):
        return
    data = {}
    if os.path.isfile(SHADER_MEMORY_PATH):
        try:
            with open(SHADER_MEMORY_PATH, "r", encoding="utf-8") as memory_file:
                data = json.load(memory_file)
        except (OSError, json.JSONDecodeError):
            data = {}
    if not isinstance(data, dict):
        data = {}
    data[chat_id] = {"token": token, "time": time.time()}
    try:
        with open(SHADER_MEMORY_PATH, "w", encoding="utf-8", newline="\n") as memory_file:
            json.dump(data, memory_file, ensure_ascii=False)
    except OSError:
        return


def remembered_shader_token(chat_id: str) -> str:
    if not chat_id or not os.path.isfile(SHADER_MEMORY_PATH):
        return ""
    try:
        with open(SHADER_MEMORY_PATH, "r", encoding="utf-8") as memory_file:
            data = json.load(memory_file)
    except (OSError, json.JSONDecodeError):
        return ""
    item = data.get(chat_id) if isinstance(data, dict) else None
    if not isinstance(item, dict):
        return ""
    if time.time() - float(item.get("time") or 0) > SHADER_MEMORY_WINDOW:
        return ""
    token = str(item.get("token") or "")
    return token if token and shader_sources(token) else ""


def shader_custom_from_context(chat_id: str) -> str:
    """上一问如果是某个带 Custom 通道的 Shader，按文件重答，不再编用途。"""
    chunks = [
        LAST_QUESTION.get(chat_id, ""),
        (LAST_TURN.get(chat_id) or {}).get("question", ""),
        (LAST_TURN.get(chat_id) or {}).get("preview", ""),
    ]
    with history_lock:
        for item in CHAT_HISTORY.get(chat_id, [])[-8:]:
            chunks.append(item.get("content", ""))
    names = []
    for chunk in chunks:
        for name in re.findall(r"[A-Za-z][A-Za-z0-9_]{4,}", chunk or ""):
            if name.lower() in ("custom", "shader", "particle", "manual") or name in names:
                continue
            names.append(name)
    remembered = remembered_shader_token(chat_id)
    if remembered and remembered not in names:
        names.append(remembered)
    names.sort(key=len, reverse=True)
    for name in names[:6]:
        if not shader_sources(name):
            continue
        answer = answer_shader_custom_data(name)
        if "Custom1" in answer:
            return answer
    return ""


def answer_correction(chat_id: str, raw_question: str, parent_text: str) -> str:
    previous = LAST_QUESTION.get(chat_id, "")
    fact = CORRECTION_PATTERN.sub("", raw_question).strip(" ，,。:：")
    if not fact:
        looked = shader_custom_from_context(chat_id)
        if looked:
            return looked
        return "收到。正确的是什么？直接告诉我，我记下来。"
    save_note(previous or parent_text or "（未记录原问题）", fact)
    log(f"[note] q={previous!r} fact={fact!r}")
    return f"记住了，以后按这个答：{fact}"


AGENTS_PATH = os.path.join(DATA_DIR, "agents.json")
_jobs = {}
_jobs_lock = threading.Lock()
_edit_locks = {}
LOCAL_JOB_WAIT = 75
LOCAL_AGENT_HINT = (
    "你电脑上的本地程序没有回应。请在你自己的电脑上运行：\n"
    "python local_agent.py --root 你的工程目录\n"
    "需要先安装 lark-cli，并用你的飞书账号登录。程序文件向朱辉要。"
)


def edit_lock_for(open_id: str) -> threading.Lock:
    with _jobs_lock:
        lock = _edit_locks.get(open_id)
        if lock is None:
            lock = threading.Lock()
            _edit_locks[open_id] = lock
        return lock


def remember_agent(open_id: str, root: str) -> bool:
    if not open_id:
        return False
    try:
        with open(AGENTS_PATH, encoding="utf-8") as file:
            saved = json.load(file)
    except (OSError, ValueError):
        saved = {}
    previous = ""
    kept = []
    for item in saved.get("items") or []:
        if item.get("open_id") == open_id:
            previous = str(item.get("root") or "")
            continue
        kept.append(item)
    kept.append({
        "open_id": open_id,
        "root": root,
        "seen": datetime.datetime.now(BEIJING_TZ).isoformat(timespec="seconds"),
    })
    temporary = AGENTS_PATH + ".tmp"
    with open(temporary, "w", encoding="utf-8", newline="\n") as file:
        json.dump({"items": kept[-30:]}, file, ensure_ascii=False, indent=2)
        file.write("\n")
    os.replace(temporary, AGENTS_PATH)
    return previous != root


def begin_job(job_id: str, open_id: str) -> None:
    event = threading.Event()
    with _jobs_lock:
        _jobs[job_id] = {"event": event, "open_id": open_id, "payload": None}


def finish_job(job_id: str, open_id: str, payload: dict) -> bool:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job or job["open_id"] != open_id:
            return False
        job["payload"] = payload
        job["event"].set()
        return True


def wait_job(job_id: str, timeout: int) -> dict | None:
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        return None
    job["event"].wait(timeout)
    with _jobs_lock:
        found = _jobs.pop(job_id, None)
    if not found:
        return None
    return found.get("payload")


def download_file_message(message_id: str, file_key: str) -> str:
    os.makedirs(IMG_DIR, exist_ok=True)
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", file_key)[:100] or "file"
    out_rel = f"{IMG_DIR_REL}/{safe}"
    run_cli(["im", "+messages-resources-download", "--as", "bot", "--message-id", message_id,
             "--file-key", file_key, "--type", "file", "--output", out_rel])
    matches = sorted(Path(IMG_DIR).glob(safe + "*"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not matches:
        raise RuntimeError("下载后找不到文件")
    return str(matches[0])


def send_user_file(open_id: str, name: str, content: str) -> None:
    folder = os.path.join(DATA_DIR, "_agent_out")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, name), "w", encoding="utf-8", newline="\n") as file:
        file.write(content)
    rel = os.path.join("_agent_out", name).replace("\\", "/")
    run_cli(["im", "+messages-send", "--as", "bot", "--user-id", open_id, "--file", rel,
             "--idempotency-key", f"dadou-{name}"])


def read_agent_payload(message: dict) -> str:
    text = extract_text(message)
    if text.startswith("DADOU/1") or text.startswith("DADOU_AGENT"):
        return text
    if message.get("message_type") != "file":
        return ""
    data = parse_content(message)
    file_key = str(data.get("file_key") or "")
    message_id = str(message.get("message_id") or "")
    if not file_key or not message_id:
        return ""
    try:
        path = download_file_message(message_id, file_key)
        body = Path(path).read_text(encoding="utf-8")
    except (OSError, RuntimeError, subprocess.TimeoutExpired, UnicodeError) as error:
        log(f"[local-agent] download failed {error}")
        return ""
    if body.startswith("DADOU/1") or body.startswith("DADOU_AGENT"):
        return body
    return ""


def accept_agent_payload(text: str, open_id: str) -> str:
    fields, body = local_edit.decode_message(text)
    if not fields:
        return ""
    root = fields.get("root") or ""
    fresh = remember_agent(open_id, root) if root else False
    op = fields.get("op") or ""
    if op in ("file", "wrote", "error"):
        matched = finish_job(fields.get("id") or "", open_id, {
            "op": op, "ok": fields.get("ok") or "", "path": fields.get("path") or "", "body": body, "root": root,
        })
        log(f"[local-agent] {op} id={fields.get('id') or '-'} matched={matched}")
        return ""
    if op == "hello" and fresh:
        return f"本地程序已连上。之后 @我说改你本地的哪个文件。当前目录：{root or '未填目录'}"
    return ""


def rewrite_local_text(path: str, request: str, original: str) -> str:
    system = (
        "你在改同事自己电脑上的一个文本文件。只输出修改后的完整文件。"
        "第一行必须是 <<<FILE>>>，最后一行必须是 <<<END>>>。不要解释，不要用代码围栏。"
        "只改对方要求的部分。如果不用改，原样放进标记里。"
    )
    user = f"路径：{path}\n要求：{request}\n原文件：\n{original}"
    raw = call_chat(LLM_MODEL, [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ], LLM_FALLBACK_MODELS)
    return local_edit.extract_rewritten(raw)


def answer_local_edit(text: str, sender: str) -> str:
    if PREVIEWING:
        path = local_edit.extract_local_edit_path(text)
        return f"（试跑）这句会改提问人自己电脑上的文件：{path or '还没给路径'}。不会改这台机器的工程。"
    if not sender:
        return "没认出是谁发的，改不了你电脑上的文件。"
    path = local_edit.extract_local_edit_path(text)
    if not path:
        return "要改你电脑上的哪个文件？发相对你工程目录的路径，例如 Assets/Scripts/Foo.cs。"
    if not LLM_ENABLED:
        return "改写文件需要语言模型，这边还没配。"
    with edit_lock_for(sender):
        job_id = os.urandom(6).hex()
        begin_job(job_id, sender)
        try:
            send_user_file(sender, f"dadou-read-{job_id}.txt", local_edit.encode_message(
                {"op": "read", "id": job_id, "path": path}, text))
        except (RuntimeError, subprocess.TimeoutExpired, OSError) as error:
            wait_job(job_id, 0)
            log(f"[local-edit] send read failed {error}")
            return "没把读取任务发到你的飞书，文件没动。"
        loaded = wait_job(job_id, LOCAL_JOB_WAIT)
        if not loaded:
            return LOCAL_AGENT_HINT
        if loaded.get("op") != "file" or loaded.get("ok") != "1":
            reason = (loaded.get("body") or "本地程序没读到文件").strip()
            return f"没改成。{reason}"
        original = loaded.get("body") or ""
        try:
            rewritten = rewrite_local_text(path, text, original)
        except (urllib.error.URLError, TimeoutError, OSError, RuntimeError, KeyError, ValueError) as error:
            log(f"[local-edit] rewrite failed {error}")
            return "改写失败，你电脑上的文件没动。"
        if not rewritten:
            return "没得到完整文件，你电脑上的文件没动。"
        if rewritten == original:
            return f"看过你电脑上的 `{path}`，按这句话不用改。"
        job_id = os.urandom(6).hex()
        begin_job(job_id, sender)
        try:
            send_user_file(sender, f"dadou-write-{job_id}.txt", local_edit.encode_message(
                {"op": "write", "id": job_id, "path": path}, rewritten))
        except (RuntimeError, subprocess.TimeoutExpired, OSError) as error:
            wait_job(job_id, 0)
            log(f"[local-edit] send write failed {error}")
            return "改好了但没发到你的电脑，文件没动。"
        saved = wait_job(job_id, LOCAL_JOB_WAIT)
        if not saved:
            return "读取成功，但写回去时本地程序没有回应，不确定文件有没有改成。"
        if saved.get("op") != "wrote" or saved.get("ok") != "1":
            reason = (saved.get("body") or "写入失败").strip()
            return f"没写进你的电脑。{reason}"
        count = local_edit.change_size(original, rewritten)
        return f"已写到你电脑上的 `{path}`，大约 {count} 行有变化。"


REMINDERS_PATH = os.path.join(DATA_DIR, "reminders.json")
_reminder_lock = threading.Lock()
_REMIND_CANCEL = re.compile(r"取消提醒|别提醒|不用提醒")
_REMIND_URGENT = re.compile(r"加急\s*提醒|提醒\s*加急|应用内加急")
_REMIND_CLOCK = re.compile(
    r"(明早|明晚|今晚|早上|上午|中午|下午|晚上|凌晨)?\s*(\d{1,2})\s*[:：点时]\s*(半|\d{1,2})?"
)


def parse_clock(text: str):
    match = _REMIND_CLOCK.search(text or "")
    if not match:
        return None
    period = match.group(1) or ""
    hour = int(match.group(2))
    minute_text = match.group(3) or ""
    minute = 30 if minute_text == "半" else (int(minute_text) if minute_text else 0)
    if period in ("下午", "晚上", "今晚", "明晚") and hour < 12:
        hour += 12
    if period == "中午" and hour < 11:
        hour += 12
    if period == "凌晨" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    return hour, minute, period


def plan_reminder_when(text: str, now: datetime.datetime):
    parsed = parse_clock(text)
    if not parsed:
        return None, False
    hour, minute, period = parsed
    when = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    tomorrow = "明天" in (text or "") or period in ("明早", "明晚")
    if tomorrow:
        return when + datetime.timedelta(days=1), False
    if when <= now:
        return when + datetime.timedelta(days=1), True
    return when, False


def reminder_is_urgent(text: str) -> bool:
    return _REMIND_URGENT.search(text or "") is not None


def is_reminder_request(text: str) -> bool:
    if "提醒" not in (text or ""):
        return False
    if _REMIND_CANCEL.search(text):
        return True
    when, _rolled = plan_reminder_when(text, datetime.datetime.now(BEIJING_TZ))
    return when is not None


def remind_task(text: str, names: list) -> str:
    task = text or ""
    for name in names:
        if name:
            task = task.replace(name, "")
    task = re.sub(r"(今天|明天|今晚|明早|明晚|早上|上午|中午|下午|晚上|凌晨)", "", task)
    task = re.sub(r"\d{1,2}\s*[:：点时]\s*(半|\d{1,2}\s*分?)?", "", task)
    task = re.sub(r"加急\s*提醒(一下|我)?|提醒\s*加急|应用内加急", "", task)
    task = re.sub(r"提醒(一下|我)?", "", task)
    task = re.sub(r"[\s，,。！!？?、]+", "", task)
    return task[:80]


def mention_open_id(mention: dict) -> str:
    identity = mention.get("id")
    if isinstance(identity, dict):
        return str(identity.get("open_id") or "")
    if isinstance(identity, str) and identity.startswith("ou_"):
        return identity
    return str(mention.get("open_id") or "")


def reminder_targets(message: dict) -> list:
    found = []
    seen = set()
    for mention in message.get("mentions") or []:
        name = str(mention.get("name") or "")
        open_id = mention_open_id(mention)
        if not name or is_bot_mention(name, open_id):
            continue
        if not re.fullmatch(r"ou_[A-Za-z0-9]+", open_id) or open_id in seen:
            continue
        seen.add(open_id)
        found.append({"open_id": open_id, "name": name.replace("<", "").replace(">", "")[:40]})
    return found


def read_reminders() -> list:
    try:
        with open(REMINDERS_PATH, encoding="utf-8") as file:
            saved = json.load(file)
    except (OSError, ValueError):
        return []
    items = saved.get("items") if isinstance(saved, dict) else []
    return [item for item in items or [] if isinstance(item, dict)]


def write_reminders(items: list) -> None:
    temporary = REMINDERS_PATH + ".tmp"
    with open(temporary, "w", encoding="utf-8", newline="\n") as file:
        json.dump({"items": items[-50:]}, file, ensure_ascii=False, indent=2)
        file.write("\n")
    os.replace(temporary, REMINDERS_PATH)


def cancel_chat_reminders(chat_id: str) -> str:
    with _reminder_lock:
        items = read_reminders()
        kept = [item for item in items if item.get("chat_id") != chat_id]
        removed = len(items) - len(kept)
        if removed:
            write_reminders(kept)
    if not removed:
        return "这个会话没有待提醒的。"
    return f"已取消 {removed} 条还没响的提醒。"


def delete_reminder(item_id: str) -> bool:
    with _reminder_lock:
        items = read_reminders()
        kept = [item for item in items if item.get("id") != item_id]
        if len(kept) == len(items):
            return False
        write_reminders(kept)
        return True


def urgent_app(message_id: str, open_ids: list) -> bool:
    if not re.fullmatch(r"om_[A-Za-z0-9]+", message_id or ""):
        return False
    ids = []
    seen = set()
    for open_id in open_ids:
        if re.fullmatch(r"ou_[A-Za-z0-9]+", open_id or "") and open_id not in seen:
            seen.add(open_id)
            ids.append(open_id)
    if not ids:
        return False
    try:
        run_cli([
            "api", "POST", f"/open-apis/im/v1/messages/{message_id}/urgent_app",
            "--as", "bot",
            "--params", json.dumps({"user_id_type": "open_id"}),
            "--data", json.dumps({"user_id_list": ids[:10]}),
        ])
    except (RuntimeError, subprocess.TimeoutExpired) as error:
        log(f"[remind] urgent failed {error}")
        return False
    log(f"[remind] urgent {message_id}")
    return True


def send_reminder(item: dict) -> bool:
    chat_id = str(item.get("chat_id") or "")
    if not chat_id:
        return False
    parts = []
    open_ids = []
    for target in item.get("targets") or []:
        open_id = str(target.get("open_id") or "")
        name = str(target.get("name") or "").replace("<", "").replace(">", "")
        if re.fullmatch(r"ou_[A-Za-z0-9]+", open_id):
            parts.append(f'<at user_id="{open_id}">{name}</at>')
            open_ids.append(open_id)
    text = ((" ".join(parts) + " ") if parts else "") + str(item.get("task") or "到点了")
    try:
        stdout = run_cli(["im", "+messages-send", "--as", "bot", "--chat-id", chat_id, "--text", text,
                          "--idempotency-key", f"remind-{item.get('id')}"])
    except (RuntimeError, subprocess.TimeoutExpired) as error:
        log(f"[remind] send failed {error}")
        return False
    log(f"[remind] sent id={item.get('id')}")
    if item.get("urgent") != "app":
        return True
    if urgent_app(sent_message_id(stdout, ""), open_ids):
        return True
    note = "提醒已经发了，但应用内加急没成功。请在飞书开放平台给大豆开通「发送应用内加急」。"
    try:
        run_cli(["im", "+messages-send", "--as", "bot", "--chat-id", chat_id, "--text", note,
                 "--idempotency-key", f"remind-fail-{item.get('id')}"])
    except (RuntimeError, subprocess.TimeoutExpired) as error:
        log(f"[remind] urgent notice failed {error}")
    return True


def answer_reminder(text: str, message: dict, chat_id: str, sender: str, group: bool) -> str:
    if _REMIND_CANCEL.search(text or ""):
        if PREVIEWING:
            return "（试跑）会取消这个会话里还没响的提醒。"
        return cancel_chat_reminders(chat_id)
    when, rolled = plan_reminder_when(text, datetime.datetime.now(BEIJING_TZ))
    if when is None:
        return "说个钟点，例如：18点提醒 @胡诚 更新艾尼路蒙皮。"
    targets = reminder_targets(message)
    if not targets and re.fullmatch(r"ou_[A-Za-z0-9]+", sender or ""):
        targets = [{"open_id": sender, "name": ""}]
    task = remind_task(text, [item.get("name") or "" for item in targets]) or "到点了"
    names = "、".join(item.get("name") or "你" for item in targets) or "这个会话"
    where = "这个群" if group else "私聊"
    lead = "今天这个点已过，改到" if rolled else ""
    urgent = reminder_is_urgent(text) and bool(targets)
    buzz = ""
    if reminder_is_urgent(text):
        buzz = "到点会应用内加急。" if urgent else "没拿到对方身份，到点只能发文字，加不了急。"
    reply_text = f"已记下。{lead}{when:%Y-%m-%d %H:%M} 在{where}提醒{names}：{task}。只提醒一次。{buzz}"
    if PREVIEWING:
        return "（试跑）" + reply_text
    item = {
        "id": os.urandom(6).hex(),
        "chat_id": chat_id,
        "when": when.isoformat(timespec="seconds"),
        "task": task,
        "targets": targets,
        "urgent": "app" if urgent else "",
        "created": datetime.datetime.now(BEIJING_TZ).isoformat(timespec="seconds"),
    }
    with _reminder_lock:
        items = read_reminders()
        items.append(item)
        write_reminders(items)
    log(f"[remind] set {item['when']} {task}")
    return reply_text


def reminder_loop() -> None:
    while True:
        now = datetime.datetime.now(BEIJING_TZ)
        with _reminder_lock:
            pending = read_reminders()
        for item in pending:
            when = parse_schedule_time(item.get("when"))
            if when is None or when > now:
                continue
            if send_reminder(item):
                delete_reminder(str(item.get("id") or ""))
        time.sleep(20)


# ---------- 监修文档监听 / CICD 打包通知（移植自 FeishuKonwledgeBot，见 bot/doc_watch.py、bot/cicd.py） ----------

DOC_WATCHER = None
CICD_NOTIFIER = None


def feishu_api(method: str, path: str, params: dict = None, data=None) -> dict:
    """以机器人身份调飞书接口，统一成 {"code": 0, "data": {...}}；失败时 code 是飞书错误码（拿不到时 -1），不抛异常。"""
    args = LARK_CLI + ["api", method, path, "--as", "bot"]
    if params:
        args += ["--params", json.dumps(params, ensure_ascii=False)]
    if data is not None:
        args += ["--data", "-"]
    try:
        result = subprocess.run(args, input=json.dumps(data, ensure_ascii=False) if data is not None else None,
                                capture_output=True, text=True, encoding="utf-8", errors="replace",
                                timeout=CLI_TIMEOUT, creationflags=NO_WINDOW)
        # 成功的 JSON 在 stdout；接口报错（ok: false + 错误码）lark-cli 写在 stderr
        output = result.stdout.strip() or result.stderr.strip()
        payload = json.loads(output[output.find("{"):] if "{" in output else output)
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        log(f"[feishu-api] {method} {path} failed {type(error).__name__}: {str(error)[:200]}")
        return {"code": -1}
    if payload.get("ok", payload.get("code") == 0):
        return {"code": 0, "data": payload.get("data") or {}}
    error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    code = error.get("code") or payload.get("code") or -1
    return {"code": int(code) if str(code).lstrip("-").isdigit() else -1, "msg": error.get("message") or payload.get("msg") or ""}


def send_dm_markdown(open_id: str, markdown: str) -> int:
    """私聊一条 post（md 段落），返回飞书错误码。"""
    text = redact(re.sub(r"\n{3,}", "\n\n", markdown).strip())
    content = json.dumps({"zh_cn": {"content": [[{"tag": "md", "text": text}]]}}, ensure_ascii=False)
    body = feishu_api("POST", "/open-apis/im/v1/messages", {"receive_id_type": "open_id"},
                      {"receive_id": open_id, "msg_type": "post", "content": content})
    return body["code"]


def send_chat_card(chat_id: str, card: dict) -> int:
    body = feishu_api("POST", "/open-apis/im/v1/messages", {"receive_id_type": "chat_id"},
                      {"receive_id": chat_id, "msg_type": "interactive", "content": card_content(card)})
    return body["code"]


def reply_at_all(message_id: str, text: str) -> int:
    content = json.dumps({"text": f'<at user_id="all"></at> {text}'}, ensure_ascii=False)
    return feishu_api("POST", f"/open-apis/im/v1/messages/{message_id}/reply", None,
                      {"msg_type": "text", "content": content})["code"]


def background_ask(messages: list, timeout: float = 0) -> str:
    """后台任务用的 AI：Codex（可限时）优先，失败且配了智谱时退回智谱。不带聊天历史和调教。"""
    if USE_CODEX:
        name = codex_cli.label()
        try:
            answer, usage = codex_cli.ask(messages, timeout=timeout)
            model_stats.note(name, True, prompt_tokens=int(usage.get("input_tokens") or 0),
                             completion_tokens=int(usage.get("output_tokens") or 0))
            return answer
        except (codex_cli.CodexError, codex_cli.CodexTimeout, OSError) as error:
            model_stats.note(name, False, redact(f"{type(error).__name__}: {error}"))
            if not LLM_API_KEY:
                raise
            log(f"[codex] 后台任务退回智谱：{str(error)[:120]}")
    if not LLM_API_KEY:
        raise ValueError("没有可用的 AI（Codex 没装，也没配 LLM_API_KEY）")
    return call_chat(LLM_MODEL, messages, LLM_FALLBACK_MODELS)


def cicd_project_context(query: str) -> str:
    hits = search_code_ondemand(build_code_keywords(query, []))
    return "\n\n".join(f"文件 {path}\n{snippet}" for path, snippet in hits)


def name_by_open_id(open_id: str) -> str:
    for name, ids in bot_known_people().items():
        if open_id in ids:
            return name
    return ""


def read_name_map(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as file:
            value = json.load(file)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def env_flag(name: str, default: bool) -> bool:
    value = get_env(name).strip().lower()
    if not value:
        return default
    return value not in ("0", "false", "no", "off")


def env_int(name: str, default: int, low: int, high: int) -> int:
    try:
        return min(high, max(low, int(get_env(name) or default)))
    except ValueError:
        return default


def setup_doc_watch():
    """data/doc_watch/config.json 存在且 enabled 时建监听器；ask 用 Codex / 智谱。"""
    global DOC_WATCHER
    try:
        config, mapping = doc_watch.load_settings()
    except (OSError, ValueError) as error:
        log(f"[文档监测] 读取 data/doc_watch 配置失败，不启动：{error}")
        return None
    if not config:
        return None
    DOC_WATCHER = doc_watch.DocWatcher(feishu_api, send_dm_markdown, background_ask if LLM_ENABLED else None,
                                       mapping, config, log=log, report=log)
    return DOC_WATCHER


def setup_cicd():
    """CICD 打包通知 + 分析接口 + Zeus 提醒。CICD_ANALYZE_ENABLED=false 时全关。"""
    global CICD_NOTIFIER
    if not env_flag("CICD_ANALYZE_ENABLED", True):
        return
    also_at = [x for x in re.split(r"[,，、\s]+", get_env("CICD_FAILURE_ALSO_AT")) if x]
    name_sources = [
        lambda name: read_name_map(os.path.join(doc_watch.DATA_ROOT, "bindings.json")).get(name) or "",
        lambda name: next((oid for oid, v in read_name_map(cicd.OPENIDS_PATH).items()
                           if isinstance(v, dict) and v.get("name") == name), ""),
    ]
    chat_name = get_env("CICD_NOTIFY_CHAT_NAME") or "打包机器人"
    CICD_NOTIFIER = cicd.CicdNotifier(feishu_api, send_chat_card, chat_name=chat_name,
                                      chat_id=get_env("CICD_NOTIFY_CHAT_ID"), failure_also_at=also_at,
                                      name_sources=name_sources, name_of=name_by_open_id, log=log, report=log)
    timeout = env_int("CICD_AI_TIMEOUT_SECONDS", 105, 10, 115)
    cicd.start_analysis_server(
        get_env("CICD_ANALYZE_HOST") or "127.0.0.1", env_int("CICD_ANALYZE_PORT", 3210, 1, 65535),
        get_env("CICD_ANALYZE_TOKEN"), lambda messages: background_ask(messages, timeout), cicd_project_context,
        notifier=CICD_NOTIFIER, reply_budget=env_int("CICD_REPLY_BUDGET_SECONDS", 100, 20, 115), log=log, report=log)
    if env_flag("ZEUS_WATCH_ENABLED", True):
        zeus = cicd.ZeusWatcher(
            feishu_api, reply_at_all, CICD_NOTIFIER.resolve_chat,
            sender_id=get_env("ZEUS_WATCH_SENDER") or "cli_c08abc1da138d00f",
            title=get_env("ZEUS_WATCH_TITLE") or "Zeus热更系统提醒",
            text=get_env("ZEUS_WATCH_TEXT") or "请留意上面的 Zeus 热更系统提醒，及时登录 Zeus 热更后台发布正式版本。",
            interval=env_int("ZEUS_WATCH_SECONDS", 30, 10, 3600), log=log, report=log)
        threading.Thread(target=zeus.loop, name="zeus-watch", daemon=True).start()
        log(f"[Zeus 提醒] 已开启：每 {zeus.interval} 秒检查打包群，看到「{zeus.title}」就回复 @所有人。")


def doc_watch_item(message: dict) -> dict:
    """私聊给文档监测的消息：@ 换成「@姓名」（/bind @某人 要用），带上被 @ 的人。"""
    data = parse_content(message)
    text = str(data.get("text", "")) if message.get("message_type") == "text" else post_plain_text(data)
    mentions = []
    for mention in message.get("mentions") or []:
        key = str(mention.get("key") or "")
        name = str(mention.get("name") or "")
        ident = mention.get("id") or {}
        open_id = ident.get("open_id") if isinstance(ident, dict) else str(ident or "")
        if key:
            text = text.replace(key, f"@{name}")
        if open_id and not is_bot_mention(name, ident):
            mentions.append({"name": name, "openId": open_id})
    return {"text": text.strip(), "userId": message.get("_sender_open_id", ""), "mentions": mentions}


def answer_doc_changes(question: str, context: str) -> str:
    """「监修文档刚改了什么」：用文档监测记下的最近变更回答。不归这里管时返回空。"""
    if not DOC_WATCHER or not doc_watch.asks_about_changes(question, context, [d["name"] for d in DOC_WATCHER.docs]):
        return ""
    changes = DOC_WATCHER.recent_changes()
    if not changes:
        return "最近 7 天监修文档没有检测到变更。"
    if not LLM_ENABLED:
        return changes[:3000]
    system = ("你是「大豆」，航海王项目组的助手。下面是机器人监测到的监修文档最近变更记录（新的在前），只按这些记录回答同事的问题，"
              "说清是哪篇文档、哪个标题下改了什么，附上文档链接；记录里没有的不要编。日文先翻成中文。"
              "用适合飞书的简短 Markdown，不要表格。\n\n【监修文档最近变更】\n" + changes)
    try:
        return call_llm(system, question)
    except Exception as error:
        log(f"[文档监测] 回答变更问题失败：{error}")
        return changes[:3000]


def run_doc_watch_cli(argv: list) -> None:
    """python -m bot --doc-watch [--dry-run] [--force] [--digest]：立即检查一次 / 预览日报，不连飞书长连接。"""
    if not setup_doc_watch():
        sys.exit("没有启用文档监测：先放好 data/doc_watch/config.json（enabled: true）和 mapping.json")
    dry_run = "--dry-run" in argv
    if "--digest" in argv:
        print(DOC_WATCHER.digest(dry_run=dry_run))
    else:
        print(DOC_WATCHER.check(dry_run=dry_run, force="--force" in argv))


def handle(message_id: str, message: dict, local_index: list) -> None:
    try:
        chat_id = message.get("chat_id", "")
        CURRENT_CHAT.id = chat_id
        CURRENT_CHAT.kind = "单聊" if message.get("chat_type") in ("p2p", "private") else "群聊"
        CURRENT_CHAT.brief = ""
        CURRENT_CHAT.queries = []
        CURRENT_CHAT.fresh = False
        raw_question = extract_text(message)
        CURRENT_CHAT.question = raw_question
        if message.get("chat_type") in ("p2p", "private"):
            payload = read_agent_payload(message)
            if payload:
                note = accept_agent_payload(payload, message.get("_sender_open_id", ""))
                if note:
                    reply(message_id, note, route="local-agent")
                return
        sender_id = message.get("_sender_open_id", "")
        if CICD_NOTIFIER:
            note = CICD_NOTIFIER.handle_command(raw_question, message.get("chat_type") or "", chat_id, sender_id)
            if note:
                reply(message_id, note, route="cicd")
                return
        if DOC_WATCHER and message.get("chat_type") in ("p2p", "private") and sender_id:
            try:
                note = DOC_WATCHER.handle_direct_message(doc_watch_item(message))
            except Exception as error:
                log(f"[文档监测] 私聊指令出错：{error}")
                note = None
            if note:
                reply(message_id, note, route="doc-watch")
                return
        if RECALL_PATTERN.search(raw_question):
            target_id = parent_message_id(message) or LAST_BOT_MESSAGE.get(chat_id, "")
            if target_id == message_id:
                target_id = LAST_BOT_MESSAGE.get(chat_id, "")
            if not target_id:
                reply(message_id, "没有可撤回的上一条。请回复我那条消息，再说「撤回」。", route="recall")
                return
            if not recall_message(target_id):
                reply(message_id, "撤回失败。只能撤回我自己发的、且还在时限内的消息。", route="recall")
                return
            mark_miss(chat_id, "撤回", target_id)
            append_question(raw_question, "recall", "已撤回")
            if LAST_BOT_MESSAGE.get(chat_id) == target_id:
                LAST_BOT_MESSAGE.pop(chat_id, None)
            return
        add_reaction(message_id, pick_reaction(raw_question))
        if message_id and raw_question:
            SENT_TEXT[message_id] = raw_question[:HISTORY_CHARS]
        remember_turn("user", raw_question)
        text = extract_text(message)
        image_keys = extract_image_keys(message)
        parent_text, parent_keys, parent_id = load_reply_context(message)
        image_owner = message_id
        if not image_keys and parent_keys:
            image_keys = parent_keys
            image_owner = parent_id
        if parent_text:
            text = f"{text}\n（回复的原消息：{parent_text}）" if text else parent_text
            replace_last_user(text)
        log(f"[handle] type={message.get('message_type')} text={text!r} images={len(image_keys)} parent={parent_id}")
        if answer_sticker(message_id, extract_text(message), message.get("_sender_open_id", ""), parent_keys, parent_id):
            return
        if image_keys:
            if not LLM_ENABLED:
                reply(message_id, "识图需要配置 LLM_API_KEY。当前只能处理文字搜文档。", route="image")
                return
            reply(message_id, handle_image(image_owner, text, image_keys, local_index), markdown=True, route="image")
            return
        raw_question = extract_text(message)
        CURRENT_CHAT.question = raw_question
        policy = doc_policy_answer(raw_question)
        if policy:
            reply(message_id, policy, route="policy")
            return
        doc_changes = answer_doc_changes(raw_question, f"{raw_question}\n{parent_text}")
        if doc_changes:
            reply(message_id, doc_changes, markdown=True, route="doc-watch")
            return
        if REFERENTIAL_PATTERN.search(raw_question) and not image_keys:
            reply(message_id, "我没拿到被回复的那张图，所以没法判断是谁。请把图片直接发给我，并 @大豆。", route="image")
            return
        recent = time.time() - float((LAST_TURN.get(chat_id) or {}).get("time") or 0) < VAGUE_CONTEXT_SEC
        vague_ask = missing_target_ask(raw_question, bool(parent_text) or recent)
        if vague_ask:
            reply(message_id, vague_ask, route="clarify")
            return
        time_answer = answer_time(raw_question)
        if time_answer:
            reply(message_id, time_answer, route="time")
            return
        if is_token_status_question(raw_question):
            reply(message_id, answer_token_status(raw_question), markdown=True, route="token")
            return
        people_answer = answer_people(raw_question, chat_id)
        if people_answer:
            reply(message_id, people_answer, route="people")
            return
        remember_shader_token(chat_id, raw_question)
        if CORRECTION_PATTERN.search(raw_question):
            mark_miss(chat_id, "不对")
            reply(message_id, answer_correction(chat_id, raw_question, parent_text), route="correction")
            return
        if ANSWER_DOUBT_PATTERN.search(raw_question) and not parent_text:
            looked = shader_custom_from_context(chat_id)
            if looked:
                reply(message_id, looked, markdown=True, route="shader-custom")
                return
        weekly_target = weekly_report_target(raw_question)
        if weekly_target:
            reply(message_id, answer_weekly_report(weekly_target), markdown=True, route="weekly-report")
            return
        if is_review_info_question(raw_question):
            reply(message_id, answer_review_info(), markdown=True, route="review-info")
            return
        refused = cannot_do_answer(raw_question)
        if refused:
            reply(message_id, refused, route="refuse")
            return
        if is_reminder_request(raw_question):
            reply(message_id, answer_reminder(
                raw_question, message, chat_id, message.get("_sender_open_id", ""),
                message.get("chat_type") not in ("p2p", "private")), route="remind")
            return
        if local_edit.is_local_edit_request(raw_question):
            reply(message_id, answer_local_edit(raw_question, message.get("_sender_open_id", "")), route="local-edit")
            return
        if is_pull_schedule_request(raw_question):
            reply(message_id, answer_pull_schedule(raw_question, message.get("_sender_open_id", ""), chat_id),
                  markdown=True, route="pull-schedule")
            return
        if is_checkout_request(raw_question):
            sender = message.get("_sender_open_id", "")
            reply(message_id, answer_checkout(raw_question, sender), markdown=True, route="checkout")
            return
        if is_pull_request(raw_question):
            sender = message.get("_sender_open_id", "")
            result = answer_pull(sender)
            in_group = message.get("chat_type") not in ("p2p", "private")
            if sender in PULL_OWNER_OPEN_IDS and in_group:
                send_owner_pull(f"拉新：\n{result}")
                reply(message_id, "拉新结果已私聊朱辉。", route="pull")
            else:
                reply(message_id, result, markdown=True, route="pull")
            return
        if PROJECT_ADDRESS_PATTERN.search(raw_question) and not extract_git_path_token(raw_question):
            reply(message_id, answer_project_address(), route="address")
            return
        if is_which_branch_question(raw_question):
            reply(message_id, answer_which_branch(chat_id), route="branch")
            return
        previous = LAST_QUESTION.get(chat_id, "")
        if PREFAB_FOLLOWUP_PATTERN.search(raw_question):
            asset_cached = LAST_ASSET_HITS.get(chat_id)
            if asset_cached:
                reply(message_id, format_asset_list(asset_cached), markdown=True, route="asset-list")
                return
            cached = LAST_PREFAB_HITS.get(chat_id)
            if cached:
                reply(message_id, format_prefab_hits(cached["tokens"], cached["hits"]), markdown=True, route="prefab-list")
                return
            if previous and PREFAB_COUNT_PATTERN.search(previous) and prefab_name_tokens(previous):
                tokens = prefab_name_tokens(previous)
                hits = find_prefabs(tokens)
                LAST_PREFAB_HITS[chat_id] = {"tokens": tokens, "hits": hits}
                reply(message_id, format_prefab_hits(tokens, hits), markdown=True, route="prefab-list")
                return
            ref_cached = LAST_REF_HITS.get(chat_id)
            if ref_cached:
                reply(message_id, format_reference_list(ref_cached), markdown=True, route="reference-list")
                return
        if is_stat_followup(previous, raw_question):
            reply(message_id, answer_file_stats(raw_question), markdown=True, route="mesh")
            return
        if is_ref_followup(previous, raw_question):
            reply(message_id, answer_references(chat_id, raw_question), markdown=True, route="reference")
            return
        if is_custom_data_followup(previous, raw_question):
            reply(message_id, answer_shader_custom_data(raw_question), markdown=True, route="shader-custom")
            return
        if is_reask((LAST_TURN.get(chat_id) or {}).get("question", ""), raw_question):
            mark_miss(chat_id, "又问了一遍")
        LAST_QUESTION[chat_id] = raw_question
        save_chat_state()
        LAST_ASSET_HITS.pop(chat_id, None)
        LAST_PREFAB_HITS.pop(chat_id, None)
        LAST_REF_HITS.pop(chat_id, None)
        if FILE_STAT_PATTERN.search(raw_question):
            reply(message_id, answer_file_stats(raw_question), markdown=True, route="mesh")
            return
        if (ASSET_COUNT_PATTERN.search(raw_question) and asset_type_for(raw_question)
                and not re.search(r"pass|变体|keyword|关键字|属性|参数|顶点|面数|骨骼", raw_question, re.IGNORECASE)):
            reply(message_id, answer_asset_count(raw_question, chat_id), markdown=True, route="asset-count")
            return
        if PREFAB_COUNT_PATTERN.search(raw_question):
            LAST_ASSET_HITS.pop(chat_id, None)
            tokens = prefab_name_tokens(raw_question)
            if not tokens:
                reply(message_id, answer_prefab_category(raw_question), markdown=True, route="prefab-count")
                return
            hits = find_prefabs(tokens)
            LAST_PREFAB_HITS[chat_id] = {"tokens": tokens, "hits": hits}
            reply(message_id, format_prefab_hits(tokens, hits), markdown=True, route="prefab-count")
            return
        if REFERENCE_IN_PATTERN.search(raw_question) and extract_git_path_token(raw_question):
            reply(message_id, answer_references(chat_id, raw_question), markdown=True, route="reference")
            return
        if CUSTOM_DATA_PATTERN.search(raw_question):
            reply(message_id, answer_shader_custom_data(raw_question), markdown=True, route="shader-custom")
            return
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,}", raw_question.strip()):
            looked = answer_shader_custom_data(raw_question)
            if "Custom1" in looked:
                reply(message_id, looked, markdown=True, route="shader-custom")
                return
        if PATH_QUESTION_PATTERN.search(raw_question) and not extract_git_path_token(raw_question) and LLM_ENABLED:
            reply(message_id, answer_path(raw_question, DIR_INDEX), markdown=True, route="path")
            return
        if not LLM_ENABLED:
            reply(message_id, handle_without_llm(text), route="docs")
            return
        if is_help_request(text):
            reply(message_id, HELP_TEXT, route="help")
            return
        # 生图/画图：明确拒绝，禁止误判成搜文档或让模型胡说能生图
        if is_gen_image_request(text):
            reply(message_id, NO_GEN_IMAGE_TEXT, route="refuse-image")
            return
        # prefab 粒子系统统计（只读 YAML）
        if is_prefab_particle_request(text):
            reply(message_id, answer_prefab_particles(text), markdown=True, route="particle")
            return
        # 有 .prefab + 粒子相关词但路径解析失败时，仍尽量走粒子查询提示
        if PREFAB_PARTICLE_PATTERN.search(text) and ".prefab" in text.lower():
            reply(message_id, answer_prefab_particles(text), markdown=True, route="particle")
            return
        # B 站链接 + 总结：先于 Git，避免链接参数被当成提交号
        if is_bilibili_summary_request(text):
            reply(message_id, answer_bilibili_summary(text), markdown=True, route="bilibili")
            return
        # Git 只读查询：不依赖 LLM，优先于能力说明/意图分类
        if is_git_request(text):
            reply(message_id, answer_git(text), markdown=True, route="git")
            return
        if is_gold_advice_question(text):
            reply(message_id, answer_gold_advice(text), markdown=True, route="gold-advice")
            return
        if is_gold_price_question(text):
            quote = answer_gold_spot()
            if not quote:
                quote = f"行情页暂时没读到数字。黄金9999在这里：{SGE_GOLD_PAGE}"
            reply(message_id, quote, markdown=True, route="gold")
            return
        if is_web_question(text):
            reply(message_id, answer_web(text), markdown=True, route="web")
            return
        result = plan(text)
        log(f"[plan] intent={result['intent']} keywords={result['keywords']}")
        # 模型仍把「生成UI」判成 docs 时纠正
        if result["intent"] == "docs" and is_gen_image_request(text):
            reply(message_id, NO_GEN_IMAGE_TEXT, route="refuse-image")
            return
        capability = answer_capability(text, result.get("keywords") or [])
        if capability:
            reply(message_id, capability, route="capability")
            return
        if result["intent"] in ("docs", "web") and is_web_question(text):
            reply(message_id, answer_web(text), markdown=True, route="web")
            return
        if result["intent"] in ("docs", "question", "chat") and (WEB_TOPIC_PATTERN.search(text) or WEB_NEWS_PATTERN.search(text)):
            result["intent"] = "web"
        if result["intent"] == "help":
            reply(message_id, HELP_TEXT, route="help")
        elif result["intent"] == "web":
            add_reaction(message_id, REACTION_WORKING)
            planned = result.get("queries") or getattr(CURRENT_CHAT, "queries", [])
            reply(message_id, answer_web_general(text, result["keywords"], planned), markdown=True, route="web")
        elif result["intent"] == "docs":
            if doc_policy_answer(" ".join(result["keywords"])) and SECRET_DOC_PATTERN.search(text):
                reply(message_id, doc_policy_answer(text) or DOC_POLICY_TEXT, route="policy")
                return
            reply(message_id, answer_docs(raw_question, result["keywords"]), route="docs")
        elif result["intent"] == "git" and extract_git_path_token(text) and re.search(r"引用|谁用|用在哪", text) and not is_git_request(text):
            reply(message_id, answer_references(chat_id, raw_question), markdown=True, route="reference")
        elif result["intent"] == "git":
            reply(message_id, answer_git(text), markdown=True, route="git")
        elif result["intent"] == "chat":
            reply(message_id, answer_chat(text), markdown=True, route="chat")
        else:
            reply(message_id, answer_question(text, result["keywords"], local_index), markdown=True, route="llm")
    except Exception as error:
        log(f"[handle failed] {type(error).__name__}: {error}")
        reply(message_id, "处理出错了，稍后再试，或者联系机器人管理员。", route="error")


def preview_question(text: str) -> dict:
    """后台试跑：走真实规则，不发飞书、不写笔记和失败记录。"""
    global PREVIEWING, PERSIST_HISTORY, RECORD_MISSES, RECORD_QUESTIONS
    box = {"route": "", "answer": ""}
    saved_reply = reply
    saved_reaction = add_reaction
    saved_preview = PREVIEWING
    saved_persist = PERSIST_HISTORY
    saved_miss = RECORD_MISSES
    saved_questions = RECORD_QUESTIONS

    def fake_reply(message_id: str, body: str, markdown: bool = False, route: str = "") -> None:
        del message_id, markdown
        if route in FABRICATION_GUARDED_ROUTES:
            body = guard_fabrication(body, text)
        box["route"] = route
        box["answer"] = body

    def fake_reaction(*args, **kwargs) -> None:
        del args, kwargs

    PREVIEWING = True
    PERSIST_HISTORY = False
    RECORD_MISSES = False
    RECORD_QUESTIONS = False
    globals()["reply"] = fake_reply
    globals()["add_reaction"] = fake_reaction
    message = {
        "message_id": "om_console_preview",
        "chat_id": "oc_console_preview",
        "chat_type": "p2p",
        "message_type": "text",
        "content": json.dumps({"text": text}, ensure_ascii=False),
        "mentions": [],
    }
    try:
        handle("om_console_preview", message, [])
    finally:
        globals()["reply"] = saved_reply
        globals()["add_reaction"] = saved_reaction
        PREVIEWING = saved_preview
        PERSIST_HISTORY = saved_persist
        RECORD_MISSES = saved_miss
        RECORD_QUESTIONS = saved_questions
    return box


def main() -> None:
    migrate_legacy_data(log)
    # lark-cli 下载图片的 --output 只接受相对当前目录的路径
    os.chdir(ROOT_DIR)
    if not LARK_CLI:
        sys.exit("找不到 lark-cli，请先安装：npm i -g @larksuite/cli")
    if "--doc-watch" in sys.argv:
        run_doc_watch_cli(sys.argv)
        return
    if not os.path.isdir(PROJECT_ROOT):
        log(f"[start] 工程目录不存在：{PROJECT_ROOT}，设置 FEISHU_BOT_PROJECT_ROOT 指向 Client 目录")
    log(f"[start] project={PROJECT_ROOT}")
    load_bot_identity()
    load_chat_state()
    log(f"[start] history_chats={len(CHAT_HISTORY)} sent={len(SENT_TEXT)}")
    threading.Thread(target=ref_index_loop, name="ref-index", daemon=True).start()
    threading.Thread(target=coach_worker, name="coach", daemon=True).start()
    threading.Thread(target=pull_schedule_loop, name="pull-schedule", daemon=True).start()
    threading.Thread(target=watch_loop, name="watch", daemon=True).start()
    threading.Thread(target=bomb_loop, name="bomb", daemon=True).start()
    threading.Thread(target=review_loop, name="review", daemon=True).start()
    threading.Thread(target=reminder_loop, name="reminder", daemon=True).start()
    if setup_doc_watch():
        threading.Thread(target=DOC_WATCHER.loop, name="doc-watch", daemon=True).start()
        minutes = " / ".join(f":{int(m):02d}" for m in DOC_WATCHER.config.get("checkMinutes") or [0, 30])
        log(f"[文档监测] 已启动：监测 {'、'.join(d['name'] for d in DOC_WATCHER.docs)}，每小时 {minutes} 检查。")
    setup_cicd()
    local_index = load_local_index()
    DIR_INDEX.extend(load_dir_index())
    log(f"[start] dir_index={len(DIR_INDEX)} notes={len(load_notes())}")
    log(f"[start] llm={'on' if LLM_ENABLED else 'off'} model={answer_model() if LLM_ENABLED else '-'} "
        f"vision={(answer_model() if USE_CODEX else LLM_VISION_MODEL) if LLM_ENABLED else '-'} local_files={len(local_index)}")
    publish_model_config()
    args = LARK_CLI + ["event", "+subscribe", "--as", "bot", "--event-types", EVENT_TYPE]
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                               errors="replace", creationflags=NO_WINDOW)
    executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
    seen_ids = []
    try:
        for line in process.stdout:
            line = line.strip()
            if not line:
                continue
            if not line.startswith("{"):
                log(f"[subscribe] {line[:300]}")
                continue
            try:
                event = json.loads(line)
            except ValueError:
                log(f"[skip] 无法解析：{line[:200]}")
                continue
            if event.get("ok") is False:
                log(f"[subscribe error] {json.dumps(event, ensure_ascii=False)[:500]}")
                continue
            message = extract_message(event)
            message_id = message.get("message_id", "")
            if not message_id or message_id in seen_ids:
                continue
            seen_ids.append(message_id)
            if len(seen_ids) > MAX_SEEN:
                seen_ids.pop(0)

            if not should_reply(message, event):
                log(f"[skip] no @大豆 message_id={message_id}")
                continue
            text = extract_text(message)
            image_keys = extract_image_keys(message)
            log(f"[recv] chat={message.get('chat_id')} chat_type={message.get('chat_type')} "
                f"msg_type={message.get('message_type')} text={text!r} images={image_keys} "
                f"mentions={len(message.get('mentions') or [])}")
            message["_sender_open_id"] = sender_open_id(event)
            log(f"[sender] {message['_sender_open_id'] or '-'}")
            executor.submit(handle, message_id, message, local_index)
    except KeyboardInterrupt:
        pass
    finally:
        process.terminate()
        process.wait()
        executor.shutdown(wait=False)


if __name__ == "__main__":
    main()
