"""热心市民：遇到问题时描述一下，先从常见问题库里找，再让 AI 分析该找谁；也能按部门翻通讯录自己挑。

- GET  /help/people              所有能找的人（登录过的同事 + 手动加的），带「能帮什么」
- PUT  /help/people/me           写自己的「能帮什么」，成为热心市民
- PUT  /help/people/{id}         管理员改别人的；POST /help/people 管理员手动加人；DELETE 删资料
- GET  /help/questions?q=        常见问题库；带 q 时按相似度排序，只返回对得上的
- POST /help/questions           加一条（所有人）；PUT/DELETE 作者或管理员
- POST /help/analyze             AI 分析：问题属于什么、建议找哪个部门的谁、自己先试什么
"""
import json
import re
import threading
import time
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

import wiki_kb
from backend import help_store, llm
from backend.deps import admin_user, current_user, same_origin

router = APIRouter(prefix="/help", tags=["热心市民"])

RATE_LIMIT = 10  # 每人每 10 分钟最多让 AI 分析几次
RATE_WINDOW = 600
MAX_PEOPLE_IN_PROMPT = 300
SKILLS_MAX = 500
_lock = threading.Lock()
_recent: Dict[str, List[float]] = {}


class Person(BaseModel):
    id: str
    name: str
    department: str = ""
    avatar: str = ""
    open_id: str = ""
    skills: str = ""
    tags: List[str] = []
    helper: bool = False
    updated_at: str = ""
    manual: bool = False


class ProfileBody(BaseModel):
    skills: str = Field("", max_length=SKILLS_MAX, description="能帮大家解决什么问题")
    tags: List[str] = Field([], max_length=12, description="擅长的关键词，如 Spine、打包、SVN")


class ManualPerson(ProfileBody):
    name: str = Field(min_length=1, max_length=20)
    department: str = Field("", max_length=60)


class Question(BaseModel):
    id: int
    title: str
    keywords: List[str] = []
    answer: str = ""
    department: str = ""
    helpers: List[str] = []
    author: str = ""
    author_id: str = ""
    created_at: str = ""
    updated_at: str = ""
    hits: int = 0
    score: Optional[float] = None
    matched: List[str] = []


class QuestionBody(BaseModel):
    title: str = Field(min_length=2, max_length=100, description="问题，一句话")
    keywords: List[str] = Field([], max_length=12, description="出现这些词就算对上")
    answer: str = Field("", max_length=2000, description="自己可以先试试什么、怎么解决")
    department: str = Field("", max_length=60, description="一般归哪个部门管")
    helpers: List[str] = Field([], max_length=8, description="该找谁（people 里的 id）")


class AnalyzeBody(BaseModel):
    problem: str = Field(min_length=2, max_length=2000, description="遇到了什么问题")


class Pick(BaseModel):
    person: Person
    reason: str = ""


class Analysis(BaseModel):
    model: str = ""
    summary: str = ""
    department: str = ""
    tips: str = ""
    picks: List[Pick] = []
    questions: List[Question] = []


def is_admin(user: dict) -> bool:
    return user.get("role") == "admin"


def known_ids(ids: List[str]) -> List[str]:
    valid = {p["id"] for p in help_store.people()}
    return [i for i in dict.fromkeys(ids) if i in valid]


# ---------- 通讯录 ----------

@router.get("/people", operation_id="list_help_people", summary="能找的人，热心市民在前，按部门排")
def list_people(user: dict = Depends(current_user)) -> dict:
    everyone = help_store.people()
    departments = sorted({p["department"] or help_store.NO_DEPARTMENT for p in everyone})
    return {"people": everyone, "departments": departments, "me": user.get("open_id") or "",
            "admin": is_admin(user)}


@router.put("/people/me", response_model=Person, operation_id="set_my_help_profile",
            summary="写自己能帮什么（skills 和 tags 都清空就不再是热心市民）", dependencies=[Depends(same_origin)])
def set_my_profile(body: ProfileBody, user: dict = Depends(current_user)) -> Person:
    key = str(user.get("open_id") or "")
    help_store.set_profile(key, body.skills, body.tags, by=user.get("name") or "")
    return Person(**next(p for p in help_store.people() if p["id"] == key))


@router.post("/people", response_model=Person, status_code=201, operation_id="add_help_person",
             summary="管理员手动加一个还没登录过的人", dependencies=[Depends(same_origin)])
def add_person(body: ManualPerson, user: dict = Depends(admin_user)) -> Person:
    key = help_store.set_profile("", body.skills, body.tags, body.name, body.department, by=user.get("name") or "")
    return Person(**next(p for p in help_store.people() if p["id"] == key))


@router.put("/people/{key}", response_model=Person, operation_id="set_help_profile",
            summary="管理员改别人的资料", dependencies=[Depends(same_origin)])
def set_profile(key: str, body: ManualPerson, user: dict = Depends(admin_user)) -> Person:
    if key not in {p["id"] for p in help_store.people()}:
        raise HTTPException(status_code=404, detail={"message": "没有这个人。"})
    help_store.set_profile(key, body.skills, body.tags, body.name, body.department, by=user.get("name") or "")
    return Person(**next(p for p in help_store.people() if p["id"] == key))


@router.delete("/people/{key}", operation_id="remove_help_profile",
               summary="删掉热心市民资料（自己的，或管理员删任何人的）", dependencies=[Depends(same_origin)])
def remove_profile(key: str, user: dict = Depends(current_user)) -> dict:
    if key != user.get("open_id") and not is_admin(user):
        raise HTTPException(status_code=403, detail={"message": "只能删自己的资料。"})
    if not help_store.remove_profile(key):
        raise HTTPException(status_code=404, detail={"message": "没有这份资料。"})
    return {"message": "已删除。"}


# ---------- 常见问题库 ----------

def editable(question_id: int, user: dict) -> dict:
    question = help_store.find_question(question_id)
    if not question:
        raise HTTPException(status_code=404, detail={"message": "这条问题不在了，刷新看看。"})
    if question.get("author_id") != user.get("open_id") and not is_admin(user):
        raise HTTPException(status_code=403, detail={"message": "只有写这条的人和管理员能改。"})
    return question


def question_fields(body: QuestionBody) -> dict:
    fields = body.model_dump()
    fields["helpers"] = known_ids(body.helpers)
    fields["title"] = body.title.strip()
    return fields


@router.get("/questions", operation_id="list_help_questions", summary="常见问题库；带 q 就按相似度找")
def list_questions(q: str = "", _user: dict = Depends(current_user)) -> dict:
    if q.strip():
        found = help_store.search(q, limit=8)
    else:
        found = sorted(help_store.list_questions(), key=lambda x: (-int(x.get("hits") or 0), -int(x["id"])))
    return {"questions": [Question(**item) for item in found]}


@router.post("/questions", response_model=Question, status_code=201, operation_id="add_help_question",
             summary="往问题库里加一条", dependencies=[Depends(same_origin)])
def add_question(body: QuestionBody, user: dict = Depends(current_user)) -> Question:
    return Question(**help_store.save_question(0, question_fields(body), user.get("name") or "",
                                               user.get("open_id") or ""))


@router.put("/questions/{question_id}", response_model=Question, operation_id="edit_help_question",
            summary="改问题库里的一条（作者或管理员）", dependencies=[Depends(same_origin)])
def edit_question(question_id: int, body: QuestionBody, user: dict = Depends(current_user)) -> Question:
    editable(question_id, user)
    return Question(**help_store.save_question(question_id, question_fields(body)))


@router.delete("/questions/{question_id}", operation_id="delete_help_question",
               summary="删问题库里的一条（作者或管理员）", dependencies=[Depends(same_origin)])
def delete_question(question_id: int, user: dict = Depends(current_user)) -> dict:
    editable(question_id, user)
    help_store.delete_question(question_id)
    return {"message": "已删除。"}


@router.post("/questions/{question_id}/hit", operation_id="help_question_useful",
             summary="这条有用（给问题库排序）", dependencies=[Depends(same_origin)])
def hit_question(question_id: int, _user: dict = Depends(current_user)) -> dict:
    if not help_store.find_question(question_id):
        raise HTTPException(status_code=404, detail={"message": "这条问题不在了。"})
    help_store.note_hit(question_id)
    return {"message": "收到，谢谢反馈。"}


# ---------- AI 分析 ----------

SYSTEM = ("你是「大豆」，游戏项目组内网里的求助分诊助手。同事遇到问题时描述给你，你判断这是什么问题、"
          "该找哪个部门的哪位同事，以及在找人之前自己可以先试什么。只能从给出的通讯录里挑人，"
          "优先挑写了「能帮什么」且对得上的热心市民；问题库里有对得上的条目时，优先用条目里写的负责人。"
          "没有合适的人就不挑，只给部门建议。不要编造通讯录里没有的人。\n"
          "只输出一个 JSON 对象，不要 Markdown，不要多余文字，格式：\n"
          '{"summary": "一两句话说这是什么问题", "department": "建议找的部门（可以为空）", '
          '"people": [{"id": "通讯录里的 id", "reason": "为什么找 TA，一句话"}], '
          '"tips": "找人之前自己可以先试的步骤，可以分行，没有就留空"}\n'
          "people 最多 3 个，按推荐程度排序。")


def allow(key: str) -> bool:
    now = time.time()
    with _lock:
        stamps = [s for s in _recent.get(key, []) if s > now - RATE_WINDOW]
        if len(stamps) >= RATE_LIMIT:
            _recent[key] = stamps
            return False
        _recent[key] = stamps + [now]
        return True


def directory_text(everyone: List[dict]) -> str:
    rows = []
    for p in everyone[:MAX_PEOPLE_IN_PROMPT]:
        line = f"- id={p['id']} | {p['name']} | {p['department'] or help_store.NO_DEPARTMENT}"
        if p["helper"]:
            line += f" | 热心市民，能帮：{p['skills'][:200]}"
            if p["tags"]:
                line += f"（{'、'.join(p['tags'])}）"
        rows.append(line)
    return "\n".join(rows) or "（通讯录是空的）"


def questions_text(questions: List[dict], names: Dict[str, str]) -> str:
    rows = []
    for q in questions:
        who = "、".join(f"{names.get(k, k)}(id={k})" for k in q.get("helpers") or [])
        rows.append(f"- 问题：{q['title']}；归属部门：{q.get('department') or '未写'}；该找：{who or '未写'}；"
                    f"处理办法：{(q.get('answer') or '')[:300]}")
    return "\n".join(rows)


def wiki_text(problem: str) -> str:
    try:
        return wiki_kb.search(problem)["context"]
    except Exception as error:  # 知识库坏了照样分析，只是少些资料
        print(f"[help] wiki search failed {type(error).__name__}: {error}", flush=True)
        return ""


def messages_for(problem: str, user: dict, everyone: List[dict], questions: List[dict]) -> List[dict]:
    names = {p["id"]: p["name"] for p in everyone}
    asker = user.get("name") or ""
    if user.get("department"):
        asker += f"（{user['department']}）"
    system = [{"role": "system", "content": SYSTEM},
              {"role": "system", "content": "通讯录：\n" + directory_text(everyone)}]
    if questions:
        system.append({"role": "system", "content": "常见问题库里可能对得上的条目：\n" + questions_text(questions, names)})
    wiki = wiki_text(problem)
    if wiki:
        system.append({"role": "system", "content": "团队飞书知识库里相关的片段（给 tips 参考）：\n" + wiki})
    return system + [{"role": "user", "content": f"求助的人：{asker or '未知'}\n遇到的问题：{problem}"}]


JSON_OBJECT = re.compile(r"\{.*\}", re.S)


def parse_answer(text: str) -> dict:
    """模型偶尔包一层 ```json 或带几句废话：取第一个 { 到最后一个 } 解析；解析不了就整段当 summary。"""
    match = JSON_OBJECT.search(text or "")
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, dict):
                return data
        except ValueError:
            pass
    return {"summary": (text or "").strip()[:1000]}


def collect(messages: List[dict]) -> tuple:
    model, parts = "", []
    for item in llm.stream_chat(messages):
        if "model" in item:
            model = item["model"]
        else:
            parts.append(item["delta"])
    return model, "".join(parts)


@router.post("/analyze", response_model=Analysis, operation_id="analyze_help_request",
             summary="AI 分析问题该找谁（会先查问题库，一起交给模型）", dependencies=[Depends(same_origin)])
async def analyze(body: AnalyzeBody, user: dict = Depends(current_user)) -> Analysis:
    if not llm.enabled(llm.settings()):
        raise HTTPException(status_code=503, detail={"message": "没有可用的模型，先从问题库和通讯录里找人吧。"})
    if not allow(str(user.get("open_id") or "")):
        raise HTTPException(status_code=429, detail={"message": "分析得太频繁了，过几分钟再来。"})
    everyone = [p for p in help_store.people() if p["id"] != user.get("open_id")]  # 不推荐求助的人自己
    questions = help_store.search(body.problem, limit=5)
    messages = messages_for(body.problem, user, everyone, questions)
    try:
        model, text = await run_in_threadpool(collect, messages)
    except llm.LLMError as error:
        raise HTTPException(status_code=502, detail={"message": "AI 没答上来：" + str(error.args[0])[:200]})
    data = parse_answer(text)
    by_id = {p["id"]: p for p in everyone}
    picks, seen = [], set()
    for item in data.get("people") or []:
        key = str((item or {}).get("id") or "") if isinstance(item, dict) else ""
        if key in by_id and key not in seen:
            seen.add(key)
            picks.append(Pick(person=Person(**by_id[key]), reason=str(item.get("reason") or "")[:300]))
    return Analysis(model=model, summary=str(data.get("summary") or "")[:1000],
                    department=str(data.get("department") or "")[:60], tips=str(data.get("tips") or "")[:2000],
                    picks=picks[:3], questions=[Question(**q) for q in questions])
