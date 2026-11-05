"""AI 聊天：网页直接和大豆配好的大模型对话。对话存在服务器上，每个人只能看到、续聊、删除自己的。

POST /api/v1/chat/messages 返回 text/event-stream，每行一个 `data: {...}`：
  {"conversation": {"id", "title"}}  这次问题落在哪个对话（新对话会在这里拿到 id）
  {"model": "codex gpt-6.1-sol"}     开始回答，用的是哪个模型（Codex 答完才一次性给出整段 delta）
  {"delta": "一段文字"}              回答内容，按顺序拼起来
  {"done": true}                     结束
  {"error": "原因"}                  出错（已经显示的内容保留）
用户中途停止时，已经收到的部分也会存下来。
"""
import json
import re
import threading
import time
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from common import wiki_kb
from backend import chat_store, llm
from backend.deps import current_user, same_origin

router = APIRouter(prefix="/chat", tags=["AI 聊天"])

MAX_MESSAGES = 60        # 一次最多看这个对话最近多少条消息
RECENT_TURNS = 3         # 最近几轮（一问一答）原样带上
RECENT_CHARS = 12000     # 最近几轮的总字数上限
RECENT_ANSWER_CHARS = 3000  # 最近几轮里单条回答太长时保留开头和结尾
OLDER_CHARS = 4000       # 更早的对话压缩后的总字数上限，从新往旧装，装不下的最早几轮丢掉
OLDER_QUESTION_CHARS = 300  # 压缩时问题留得多一些（问题更能说明在聊什么）
OLDER_ANSWER_CHARS = 160
CODE_BLOCK = re.compile(r"```.*?(```|$)", re.S)
RATE_LIMIT = 30  # 每人每 10 分钟最多问几次，防止把免费额度刷光
RATE_WINDOW = 600
SYSTEM = ("你是「大豆」，游戏项目组内网里的 AI 助手，对话的是组里的同事（美术、程序、策划、QA）。"
          "能查项目的 Unity 工程代码和 Git 提交记录、改动，也能查团队飞书知识库，问到时先查再答。"
          "用中文回答，先给结论再展开，能用列表和代码块就用 Markdown。不知道的直接说不知道，不要编。")

_lock = threading.Lock()
_recent: Dict[str, List[float]] = {}


class SendBody(BaseModel):
    content: str = Field(min_length=1, max_length=8000, description="要问的话")
    conversation_id: Optional[str] = Field(None, description="接着哪个对话问；不填就开新对话")


class RenameBody(BaseModel):
    title: str = Field(min_length=1, max_length=100)


def owner_of(user: dict) -> str:
    owner = str(user.get("open_id") or "")
    if not owner:
        raise HTTPException(status_code=401, detail={"message": "请先登录。"})
    return owner


def not_found() -> HTTPException:
    return HTTPException(status_code=404, detail={"message": "没有这个对话。"})


def allow(key: str) -> bool:
    now = time.time()
    with _lock:
        stamps = [stamp for stamp in _recent.get(key, []) if stamp > now - RATE_WINDOW]
        if len(stamps) >= RATE_LIMIT:
            _recent[key] = stamps
            return False
        _recent[key] = stamps + [now]
        return True


def clip_middle(text: str, limit: int) -> str:
    """太长时保留开头和结尾（结论常在两头）。"""
    if len(text) <= limit:
        return text
    head = limit * 2 // 3
    return f"{text[:head]}\n…（中间省略 {len(text) - limit} 字）…\n{text[-(limit - head):]}"


def squeeze(text: str, limit: int) -> str:
    """压成一行：代码块换成 [代码略]，空白合并，超长截断。"""
    text = CODE_BLOCK.sub(" [代码略] ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[:limit] + "…"


def split_turns(messages: List[dict]) -> List[List[dict]]:
    """按一问一答分轮；连续几条问题（中间回答出错没存）算同一轮。"""
    turns: List[List[dict]] = []
    for message in messages:
        if message["role"] == "user" and (not turns or turns[-1][-1]["role"] == "assistant"):
            turns.append([])
        if turns:  # 开头是助手的话（没有对应问题）丢掉
            turns[-1].append(message)
    return turns


def compress_older(turns: List[List[dict]]) -> str:
    """更早的对话压成「问 / 答」摘要行，从新往旧装进 OLDER_CHARS，返回时按时间顺序。"""
    lines, used = [], 0
    for turn in reversed(turns):
        parts = []
        for message in turn:
            if message["role"] == "user":
                parts.append("问：" + squeeze(message["content"], OLDER_QUESTION_CHARS))
            else:
                parts.append("答：" + squeeze(message["content"], OLDER_ANSWER_CHARS))
        line = "- " + " ／ ".join(parts)
        if used + len(line) > OLDER_CHARS:
            break
        lines.append(line)
        used += len(line)
    dropped = len(turns) - len(lines)
    lines.reverse()
    if dropped:
        lines.insert(0, f"（更早的 {dropped} 轮已省略）")
    return "\n".join(lines)


def asker_text(user: Optional[dict]) -> str:
    """提问的人是谁、在哪个部门：让回答贴合对方的工作（美术讲操作、程序讲代码）。"""
    if not user or not user.get("name"):
        return ""
    department = str(user.get("department") or "").strip()
    who = f"{user['name']}（{department}）" if department else str(user["name"])
    hint = "回答时按对方部门的工作习惯和关心的点来讲，用对方熟悉的说法。" if department else ""
    return f"\n\n正在提问的同事：{who}。{hint}"


def context(messages: List[dict], user: Optional[dict] = None) -> List[dict]:
    """喂给模型的上下文，按权重分三层：
    1. 当前问题：原样放最后，最重要；
    2. 最近 RECENT_TURNS 轮：原样带上（超长的回答保留头尾），总字数超了就少带几轮；
    3. 更早的对话：压缩成摘要行放进系统提示，注明仅供参考。
    出错的回答不算。"""
    from bot import echo_bot
    usable = [{"role": m["role"], "content": m["content"]} for m in messages
              if m["status"] != "error" and m["content"].strip()][-MAX_MESSAGES:]
    turns = split_turns(usable)
    current = turns.pop() if turns else []
    recent, used = [], sum(len(m["content"]) for m in current)
    while turns and len(recent) < RECENT_TURNS:
        turn = [dict(m, content=clip_middle(m["content"], RECENT_ANSWER_CHARS)) if m["role"] == "assistant" else m
                for m in turns[-1]]
        size = sum(len(m["content"]) for m in turn)
        if recent and used + size > RECENT_CHARS:
            break
        turns.pop()
        recent.insert(0, turn)
        used += size
    kept = [m for turn in recent for m in turn] + current
    system = [{"role": "system", "content": f"现在是 {echo_bot.now_text()}。\n\n{SYSTEM}{asker_text(user)}"}]
    older = compress_older(turns)
    if older:
        system.append({"role": "system", "content": "这个对话更早的内容（已压缩，只供了解来龙去脉；"
                                                    "和最近的对话、当前问题冲突时以后者为准）：\n" + older})
    wiki = wiki_context(kept)
    if wiki:
        system.append({"role": "system", "content": f"团队飞书知识库中与问题相关的文档片段：\n{wiki}"})
    return system + kept


def wiki_context(messages: List[dict]) -> str:
    """用最后一个问题检索本地知识库；很短的追问（「那 iOS 呢？」）连上一个问题一起搜。"""
    questions = [m["content"] for m in messages if m["role"] == "user"]
    if not questions:
        return ""
    query = questions[-1]
    if len(query) < 12 and len(questions) > 1:
        query = questions[-2] + "\n" + query
    try:
        return wiki_kb.search(query)["context"]
    except Exception as error:  # 检索坏了也照常回答，只是少了知识库资料
        print(f"[wiki] search failed {type(error).__name__}: {error}", flush=True)
        return ""


def event(payload: dict) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


@router.get("/info", operation_id="chat_info", summary="聊天用的模型，以及有没有配好")
def info(_user: dict = Depends(current_user)) -> dict:
    config = llm.settings()
    return {"enabled": llm.enabled(config), "model": config["model"]}


@router.get("/conversations", operation_id="list_my_conversations", summary="我的对话列表（新的在前）")
def list_conversations(user: dict = Depends(current_user)) -> dict:
    return {"conversations": chat_store.list_conversations(owner_of(user))}


@router.get("/conversations/{conversation_id}", operation_id="get_my_conversation", summary="我的某个对话的全部消息")
def get_conversation(conversation_id: str, user: dict = Depends(current_user)) -> dict:
    conversation = chat_store.get_conversation(owner_of(user), conversation_id)
    if not conversation:
        raise not_found()
    return conversation


@router.patch("/conversations/{conversation_id}", operation_id="rename_my_conversation", summary="改对话标题",
              dependencies=[Depends(same_origin)])
def rename_conversation(conversation_id: str, body: RenameBody, user: dict = Depends(current_user)) -> dict:
    if not chat_store.rename_conversation(owner_of(user), conversation_id, body.title):
        raise not_found()
    return {"message": "已改名。"}


@router.delete("/conversations/{conversation_id}", operation_id="delete_my_conversation", summary="删除对话",
               dependencies=[Depends(same_origin)])
def delete_conversation(conversation_id: str, user: dict = Depends(current_user)) -> dict:
    if not chat_store.delete_conversation(owner_of(user), conversation_id):
        raise not_found()
    return {"message": "已删除。"}


@router.post("/messages", operation_id="send_chat_message", dependencies=[Depends(same_origin)],
             summary="问一句话，流式返回回答（text/event-stream）；问题和回答都存进我的对话")
def send_message(body: SendBody, user: dict = Depends(current_user)) -> StreamingResponse:
    owner = owner_of(user)
    if body.conversation_id and not chat_store.get_conversation(owner, body.conversation_id):
        raise not_found()
    if not llm.enabled(llm.settings()):
        raise HTTPException(status_code=503, detail={"message": "没有可用的模型（Codex 没找到，也没有配 LLM_API_KEY），找管理员配一下。"})
    if not allow(owner):
        raise HTTPException(status_code=429, detail={"message": "问得太频繁了，过几分钟再来。"})
    conversation_id = body.conversation_id or chat_store.create_conversation(owner, body.content)
    chat_store.add_message(owner, conversation_id, "user", body.content)
    conversation = chat_store.get_conversation(owner, conversation_id)
    messages = context(conversation["messages"], user)

    async def stream():
        # 用异步生成器：用户断开时 Starlette 会在 await 处抛 CancelledError，finally 一定会跑到。
        # （同步生成器在断开后不会被关闭，中途停止的内容就存不下来。）
        parts, model, status, error_text = [], "", "stopped", ""
        chunks = iter(llm.stream_chat(messages))
        try:
            yield event({"conversation": {"id": conversation_id, "title": conversation["title"]}})
            while True:
                item = await run_in_threadpool(next, chunks, None)  # 读模型是阻塞的，放到线程里
                if item is None:
                    break
                if "model" in item:
                    model = item["model"]
                else:
                    parts.append(item["delta"])
                yield event(item)
            status = "ok"
            yield event({"done": True})
        except llm.LLMError as error:
            status, error_text = "error", "模型没答上来：" + str(error.args[0])[:200]
            yield event({"error": error_text})
        finally:
            # 正常结束、出错、用户中途停止都会走到这里；有内容就存，出错时把原因存成一条失败的回答
            text = "".join(parts)
            if text or status == "error":
                chat_store.add_message(owner, conversation_id, "assistant", text or error_text, model,
                                       "error" if status == "error" and not text else status)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
