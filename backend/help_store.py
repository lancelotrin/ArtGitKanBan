"""热心市民的数据：help.json 里存「能帮什么」的自我介绍和常见问题库。

- helpers：{key: {...}}。登录过的人 key 就是 open_id，名字和部门每次从 users.json 现取；
  管理员也能手动加还没登录过的人，key 是 m<数字>，名字和部门存在这里。
- questions：常见问题，每条写清问题、关键词、先自己试试什么、该找谁（helper key 列表）。
"""
import datetime
import json
import os
import re
import threading
from typing import List, Optional

from backend.deps import ROOT_DIR, auth

HELP_PATH = os.environ.get("HELP_PATH") or os.path.join(ROOT_DIR, "help.json")
BEIJING_TZ = datetime.timezone(datetime.timedelta(hours=8))
NO_DEPARTMENT = "未填部门"
_lock = threading.Lock()


def now_text() -> str:
    return datetime.datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M")


def load() -> dict:
    try:
        with open(HELP_PATH, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    if not isinstance(data.get("helpers"), dict):
        data["helpers"] = {}
    if not isinstance(data.get("questions"), list):
        data["questions"] = []
    data.setdefault("next_id", 1)
    return data


def save(data: dict) -> None:
    temporary = HELP_PATH + ".tmp"
    with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    os.replace(temporary, HELP_PATH)


# ---------- 通讯录：登录过的人 + 手动加的人 ----------

def people(data: Optional[dict] = None) -> List[dict]:
    """所有能找的人：登录过的同事都在，写过「能帮什么」的是热心市民。"""
    data = data or load()
    helpers = data["helpers"]
    result = []
    for user in auth.load_users().values():
        open_id = str(user.get("open_id") or "")
        if not open_id:
            continue
        profile = helpers.get(open_id) or {}
        result.append(person(open_id, user.get("name") or open_id, user.get("department") or "",
                             user.get("avatar") or "", open_id, profile))
    for key, profile in helpers.items():
        if key.startswith("m") and profile.get("name"):
            result.append(person(key, profile["name"], profile.get("department") or "", "", "", profile))
    result.sort(key=lambda p: (not p["helper"], p["department"] or "~", p["name"]))
    return result


def person(key: str, name: str, department: str, avatar: str, open_id: str, profile: dict) -> dict:
    skills = str(profile.get("skills") or "")
    return {"id": key, "name": str(name), "department": str(profile.get("department") or department or ""),
            "avatar": avatar, "open_id": open_id, "skills": skills, "tags": list(profile.get("tags") or []),
            "helper": bool(skills or profile.get("tags")), "updated_at": str(profile.get("updated_at") or ""),
            "manual": key.startswith("m")}


def set_profile(key: str, skills: str, tags: List[str], name: str = "", department: str = "", by: str = "") -> str:
    """写自己的（或管理员替别人写）「能帮什么」。key 为空表示新加一个手动的人，返回 key。"""
    with _lock:
        data = load()
        if not key:
            key = f"m{data['next_id']}"
            data["next_id"] += 1
        profile = data["helpers"].get(key) or {}
        profile.update(skills=skills.strip(), tags=clean_tags(tags), updated_at=now_text(), by=by)
        if key.startswith("m"):
            profile.update(name=name.strip(), department=department.strip())
        data["helpers"][key] = profile
        save(data)
    return key


def remove_profile(key: str) -> bool:
    with _lock:
        data = load()
        if key not in data["helpers"]:
            return False
        del data["helpers"][key]
        for question in data["questions"]:
            question["helpers"] = [k for k in question.get("helpers", []) if k != key]
        save(data)
    return True


def clean_tags(tags: List[str]) -> List[str]:
    seen = []
    for tag in tags:
        tag = str(tag).strip()[:20]
        if tag and tag not in seen:
            seen.append(tag)
    return seen[:12]


# ---------- 常见问题库 ----------

def list_questions() -> List[dict]:
    return load()["questions"]


def find_question(question_id: int) -> Optional[dict]:
    return next((q for q in list_questions() if q.get("id") == question_id), None)


def save_question(question_id: int, fields: dict, author: str = "", author_id: str = "") -> dict:
    with _lock:
        data = load()
        if question_id:
            question = next((q for q in data["questions"] if q.get("id") == question_id), None)
            if question is None:
                raise KeyError(question_id)
            question.update(fields, updated_at=now_text())
        else:
            question = dict(fields, id=data["next_id"], author=author, author_id=author_id,
                            created_at=now_text(), updated_at=now_text(), hits=0)
            data["next_id"] += 1
            data["questions"].append(question)
        question["keywords"] = clean_tags(question.get("keywords") or [])
        save(data)
    return dict(question)


def delete_question(question_id: int) -> bool:
    with _lock:
        data = load()
        before = len(data["questions"])
        data["questions"] = [q for q in data["questions"] if q.get("id") != question_id]
        if len(data["questions"]) == before:
            return False
        save(data)
    return True


def note_hit(question_id: int) -> None:
    """有人点了「这条有用」：用来给问题库排序。"""
    with _lock:
        data = load()
        for question in data["questions"]:
            if question.get("id") == question_id:
                question["hits"] = int(question.get("hits") or 0) + 1
                save(data)
                return


WORDS = re.compile(r"[a-z0-9_.+#-]+|[一-鿿]+", re.I)


def grams(text: str) -> set:
    """英文按词，中文按相邻两个字，用来粗略比相似度。"""
    result = set()
    for word in WORDS.findall(text.lower()):
        if word[0] < "一":
            result.add(word)
        elif len(word) == 1:
            result.add(word)
        else:
            result.update(word[i:i + 2] for i in range(len(word) - 1))
    return result


def search(text: str, limit: int = 5) -> List[dict]:
    """按关键词命中（权重大）和问题描述的字面相似度给问题库打分，返回分数最高的几条（带 score）。"""
    text = text.strip()
    if not text:
        return []
    lowered = text.lower()
    mine = grams(text)
    scored = []
    for question in list_questions():
        keywords = [k for k in question.get("keywords") or [] if k.lower() in lowered]
        theirs = grams(" ".join([question.get("title") or "", " ".join(question.get("keywords") or [])]))
        overlap = len(mine & theirs) / max(1, min(len(mine), len(theirs))) if theirs else 0
        score = len(keywords) * 3 + overlap * 4 + min(int(question.get("hits") or 0), 20) * 0.02
        if keywords or overlap >= 0.25:
            scored.append((score, dict(question, score=round(score, 2), matched=keywords)))
    scored.sort(key=lambda item: -item[0])
    return [item[1] for item in scored[:limit]]
