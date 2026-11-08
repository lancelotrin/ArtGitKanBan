"""监修文档监听（移植自 FeishuKonwledgeBot/src/doc-watch）。

定时比对知识库里的 docx 文档（按 block_id 精确到段落的新增 / 修改 / 删除），按归因规则找到负责人，私聊通知。
- 归因优先级：人工确认（learned）> 跨系统主题（topics）> 模型判断 > 关键词与角色规则；功能对照表（features）额外加负责策划。
- 只有全部发送成功才推进基线；失败的下一轮只重发给失败的人，连续 MAX_ATTEMPTS 轮失败后放弃并推进。
- 归不上的变更私聊问维护者（clarify.asker），回复数字记住该标题的归属并补发；冲突时转给决策人（arbiter）。
- 每天 digest.time 把当天发出的通知汇总给 digest.to。

配置：data/doc_watch/config.json（文档列表、管理员、维护者、日报）+ data/doc_watch/mapping.json（归因规则）。
运行数据也在 data/doc_watch/，格式与原 Node 版相同，可直接拷贝过来续用。

飞书接口和 AI 由调用方注入：
  request(method, path, params, data) -> {"code": 0, "data": {...}}
  send(open_id, markdown) -> 飞书错误码（0 = 成功）
  ask(messages) -> str，失败抛异常
"""
import datetime
import hashlib
import json
import os
import re
import threading
import time

from common.paths import DATA_DIR

DATA_ROOT = os.path.join(DATA_DIR, "doc_watch")
CONFIG_PATH = os.path.join(DATA_ROOT, "config.json")
MAPPING_PATH = os.path.join(DATA_ROOT, "mapping.json")

CANCEL = re.compile(r"^(取消推送|退订|别发了|不要发了|不想收|停止推送|关闭推送|取消)[。！!\s]*$")
RESUME = re.compile(r"^(恢复推送|继续推送|重新订阅|订阅)[。！!\s]*$")
MAX_ATTEMPTS = 6
KIND_MARK = {"added": "＋", "changed": "～", "removed": "－"}
KINDS = ("added", "changed", "removed")
DEFAULT_WIKI_BASE = "https://rcnc3bm8lc5c.feishu.cn/wiki"
CHANGE_WORDS = re.compile(r"改了|改动|修改|更新|变更|变化|最新|新增|刚刚|最近|删了|动了")


def today(now: datetime.datetime = None) -> str:
    return (now or datetime.datetime.now()).strftime("%Y-%m-%d")


def stamp(now: datetime.datetime = None) -> str:
    return (now or datetime.datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


def count_changes(changes: dict) -> int:
    return sum(len(changes[kind]) for kind in KINDS)


def asks_about_changes(text: str, context: str, doc_names=()) -> bool:
    """问的是最近改了什么，且和监修文档有关（问题或被回复的消息里提到监修 / 文档名）。"""
    if not CHANGE_WORDS.search(text or ""):
        return False
    context = context or ""
    return "监修" in context or any(name and name in context for name in doc_names)


def parse_doc_source(entry) -> dict:
    value = {"url": entry} if isinstance(entry, str) else dict(entry or {})
    token = value.get("wikiToken") or value.get("wiki_token")
    if not token:
        match = re.search(r"/wiki/([A-Za-z0-9]+)", value.get("url") or "")
        token = match.group(1) if match else ""
    if not token:
        raise ValueError(f"doc_watch 的 docs 里有无法识别的文档：{json.dumps(entry, ensure_ascii=False)}")
    return {"name": value.get("name") or token, "token": token}


# ---------------------------------------------------------------- blocks：快照与段落级 diff

HEADING_LEVEL = {3: 1, 4: 2, 5: 3, 6: 4, 7: 5, 8: 6, 9: 7, 10: 8, 11: 9}
TEXT_KEYS = ("text", "heading1", "heading2", "heading3", "heading4", "heading5", "heading6", "heading7",
             "heading8", "heading9", "bullet", "ordered", "code", "quote", "todo", "callout")


def elements_text(elements) -> str:
    parts = []
    for el in elements or []:
        if not isinstance(el, dict):
            continue
        if isinstance(el.get("text_run"), dict) and el["text_run"].get("content") is not None:
            parts.append(str(el["text_run"]["content"]))
        elif isinstance(el.get("mention_doc"), dict) and el["mention_doc"].get("title") is not None:
            parts.append(str(el["mention_doc"]["title"]))
        elif isinstance(el.get("equation"), dict) and el["equation"].get("content") is not None:
            parts.append(str(el["equation"]["content"]))
        elif el.get("mention_user"):
            parts.append(f"@{(el['mention_user'] or {}).get('name') or ''}")
    return "".join(parts)


def flatten_blocks(blocks: list, document_id: str) -> list:
    """把 block 树按文档顺序摊平：[{id, type, level, text}]。"""
    index = {b.get("block_id"): b for b in blocks}
    order = []
    stack = [document_id]
    while stack:
        block = index.get(stack.pop())
        if not block:
            continue
        kind = block.get("block_type") or 0
        key = next((k for k in TEXT_KEYS if isinstance(block.get(k), dict)), None)
        text = elements_text(block[key].get("elements")) if key else ""
        if text.strip() or kind in HEADING_LEVEL:
            order.append({"id": block["block_id"], "type": kind, "level": HEADING_LEVEL.get(kind, 0), "text": text})
        stack.extend(reversed(block.get("children") or []))
    return order


def block_sections(blocks: list) -> dict:
    """每个 block 所在的标题路径（「2026-10-20 / 议题一」）；标题自己的路径不含自己。"""
    result = {}
    stack = []
    for block in blocks:
        if block.get("level"):
            stack = [h for h in stack if h["level"] < block["level"]]
            result[block["id"]] = " / ".join(h["text"] for h in stack)
            stack.append({"level": block["level"], "text": block["text"].strip()})
        else:
            result[block["id"]] = " / ".join(h["text"] for h in stack)
    return result


def diff_docs(old, new) -> dict:
    if not old:
        return {"firstRun": True, "added": [], "changed": [], "removed": []}
    before = {b["id"]: b for b in old["blocks"]}
    after = {b["id"] for b in new["blocks"]}
    new_sections = block_sections(new["blocks"])
    old_sections = block_sections(old["blocks"])
    added, changed = [], []
    for block in new["blocks"]:
        prev = before.get(block["id"])
        if not prev:
            if block["text"].strip():
                added.append({**block, "section": new_sections.get(block["id"], "")})
        elif prev["text"] != block["text"]:
            changed.append({**block, "section": new_sections.get(block["id"], ""), "oldText": prev["text"]})
    removed = [{**b, "section": old_sections.get(b["id"], "")} for b in old["blocks"]
               if b["id"] not in after and b["text"].strip()]
    return {"firstRun": False, "added": added, "changed": changed, "removed": removed}


# ---------------------------------------------------------------- summary：纯规则的通知正文

NOISE = re.compile(r"^[\w\-. ()一-鿿]*\.(png|jpe?g|gif|bmp|webp|mp4|mov|avi|xlsx?|docx?|pptx?|pdf|txt|zip|rar|7z)$",
                   re.IGNORECASE)
MAX_PER_SECTION = 4
MAX_SECTIONS = 12
MAX_LINE = 60


def is_noise(text: str) -> bool:
    t = text.strip()
    return not t or len(t) <= 1 or bool(NOISE.match(t))


def shorten(text: str, limit: int = MAX_LINE) -> str:
    t = re.sub(r"\s+", " ", text.strip())
    return t if len(t) <= limit else t[:limit - 1] + "…"


def headline(changes: dict) -> str:
    parts = []
    for kind, label in (("added", "新增"), ("changed", "修改"), ("removed", "删除")):
        if changes[kind]:
            parts.append(f"{label} {len(changes[kind])}")
    return "，".join(parts) or "无变更"


def summarize(changes: dict, condensed: dict = None) -> str:
    """按标题分组、去重、折叠长列表。condensed（{标题: [要点]}）有的话替换该节的原文段落。"""
    condensed = condensed or {}
    groups = {}
    noise = 0
    for kind in KINDS:
        mark = KIND_MARK[kind]
        for item in changes[kind]:
            if item.get("level"):
                continue  # 标题只当分节名
            if is_noise(item["text"]):
                noise += 1
                continue
            key = item.get("section") or "（无标题）"
            line = f"{mark} {shorten(item['text'])}"
            group = groups.setdefault(key, [])
            if line not in group:
                group.append(line)
    lines = []
    keys = list(groups)
    for n, section in enumerate(keys):
        if n == MAX_SECTIONS:
            lines.append(f"…另有 {sum(len(groups[k]) for k in keys[n:])} 处变更")
        if n >= MAX_SECTIONS:
            break
        items = groups[section]
        lines.append(f"▸ {section[-40:] if len(section) > 40 else section}")
        if condensed.get(section):
            marks = "".join(dict.fromkeys(line[0] for line in items))
            points = condensed[section] if isinstance(condensed[section], list) else [condensed[section]]
            lines.extend(f"   {marks} {point}" for point in points)
            continue
        lines.extend(f"   {line}" for line in items[:MAX_PER_SECTION])
        if len(items) > MAX_PER_SECTION:
            lines.append(f"   …本节共 {len(items)} 处")
    if noise:
        lines.append(f"（另有 {noise} 个附件/图片变更未列出）")
    return "\n".join(lines)


# ---------------------------------------------------------------- attribution：归给谁

DIGIT_ORDER = ("battle", "level", "system", "num", "text", "audio", "art", "interact")
KANA = re.compile(r"[぀-ヿ]")


def route_options(mapping: dict) -> list:
    """问维护者时的编号选项；沿用原机器人的编号（7 美术，8 交互）。"""
    routes = mapping.get("routes") or []
    ordered = [r for key in DIGIT_ORDER for r in routes if r.get("key") == key]
    ordered += [r for r in routes if r.get("key") not in DIGIT_ORDER]
    return [{"digit": str(n + 1), "key": r["key"], "name": r.get("name") or r["key"]} for n, r in enumerate(ordered)]


def keyword_routes(item: dict, mapping: dict) -> list:
    whole = f"{item.get('section') or ''} {item['text']}"
    haystack = whole.lower()
    hits = [r["key"] for r in mapping.get("routes") or []
            if not any(str(ex).lower() in haystack for ex in r.get("exclude") or [])
            and any(str(kw).lower() in haystack for kw in r.get("keywords") or [])]
    # 角色规则只在讲角色本身（动作/技能/模型…）时生效
    rules = mapping.get("character_rules") or {}
    if any(w and w in whole for w in rules.get("content_words") or []):
        if any(n and n in whole for n in rules.get("monsters") or []):
            hits = [k for k in hits if k != "battle"] + ["level"]
        elif any(n and n in whole for n in rules.get("playable") or []):
            hits = [k for k in hits if k != "level"] + ["battle"]
    return list(dict.fromkeys(hits))


def feature_matches(item: dict, mapping: dict) -> list:
    whole = f"{item.get('section') or ''} {item['text']}"
    return [f for f in mapping.get("features") or []
            if f.get("owner") and any(k and k in whole for k in f.get("keywords") or [])
            and not any(k and k in whole for k in f.get("exclude") or [])]


def module_attribute(item: dict, mapping: dict, learned: dict) -> dict:
    section = item.get("section") or ""
    if section and learned.get(section):
        return {"keys": learned[section], "people": [], "via": "人工确认"}
    whole = f"{section} {item['text']}"
    for topic in mapping.get("topics") or []:
        if any(k and k in whole for k in topic.get("keywords") or []):
            name = topic.get("name") or (topic.get("keywords") or [""])[0]
            return {"keys": topic.get("routes") or [], "people": topic.get("owners") or [], "via": f"跨系统主题「{name}」"}
    if item.get("modelRoutes") is not None:
        return {"keys": item["modelRoutes"], "people": [], "via": "模型判断"}
    return {"keys": keyword_routes(item, mapping), "people": [], "via": "关键词"}


def attribute(item: dict, mapping: dict, learned: dict = None) -> dict:
    """返回 {keys: 模块, people: 额外的人（主题 / 功能策划）, via: 哪一层定的}。
    没命中模块但命中功能时，归到功能所属模块。"""
    base = module_attribute(item, mapping, learned or {})
    hits = feature_matches(item, mapping)
    if not hits:
        return base
    keys = base["keys"] or list(dict.fromkeys(f["route"] for f in hits if f.get("route")))
    people = list(dict.fromkeys(base["people"] + [f["owner"] for f in hits]))
    via = f"{base['via'] if base['keys'] else '功能对照表'}；功能「{'、'.join(f.get('name') or '' for f in hits)}」"
    return {"keys": keys, "people": people, "via": via}


def owners_for(keys: list, mapping: dict) -> list:
    names = []
    for key in keys:
        route = next((r for r in mapping.get("routes") or [] if r.get("key") == key), None)
        if route:
            names.append(route.get("owner") or key)
            names.extend(route.get("cc") or [])
    return names


def parse_json_array(text: str) -> list:
    """模型有时把答案重复一遍：只取第一个完整的 JSON 数组。"""
    start = text.find("[")
    if start < 0:
        raise ValueError("模型没给 JSON 数组")
    depth, in_string, i = 0, False, start
    while i < len(text):
        ch = text[i]
        if in_string:
            if ch == "\\":
                i += 1
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
        i += 1
    raise ValueError("模型给的 JSON 数组不完整")


def annotate_with_model(changes: dict, mapping: dict, ask, max_items: int = 40) -> int:
    """让模型给每条变更标模块；模型不可用时抛异常，调用方降级到关键词。"""
    items = (changes["added"] + changes["changed"] + changes["removed"])[:max_items]
    if not items:
        return 0
    routes = "\n".join(f"- {r['key']}（{r.get('name')}）：{'、'.join((r.get('keywords') or [])[:12])}"
                       for r in mapping.get("routes") or [])
    rules = mapping.get("character_rules") or {}
    confirmed = "\n".join(f"- {x}" for x in mapping.get("_已确认规则") or [])
    system = "\n\n".join(x for x in (
        "你是游戏项目的文档变更分诊员。根据变更所在的标题路径和正文，判断应同步给哪些模块负责人。",
        f"可用模块及关键词：\n{routes}",
        f"怪物/敌人（其动作、外观一律归 level）：{'、'.join(rules['monsters'])}" if rules.get("monsters") else "",
        f"玩家可控角色（其技能、动作归 battle）：{'、'.join(rules['playable'])}" if rules.get("playable") else "",
        f"已确认的分工规则（必须遵守）：\n{confirmed}" if confirmed else "",
        "一条可以命中多个模块；美术交付物额外加 art 并保留所属功能模块；实在判断不了的给空数组。",
        '只输出 JSON 数组，不要解释，格式：[{"i":0,"modules":["battle","art"]}]',
    ) if x)
    payload = [{"i": i, "section": (item.get("section") or "")[-120:], "text": item["text"][:160]}
               for i, item in enumerate(items)]
    rows = parse_json_array(ask([{"role": "system", "content": system},
                                 {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]))
    valid = {r["key"] for r in mapping.get("routes") or []}
    marked = 0
    for row in rows:
        try:
            item = items[int(row.get("i"))]
        except (TypeError, ValueError, IndexError, AttributeError):
            continue
        if not isinstance(row.get("modules"), list):
            continue
        item["modelRoutes"] = [m for m in row["modules"] if m in valid]
        marked += 1
    return marked


def condense_with_model(changes: dict, ask, max_sections: int = 20, max_items: int = 8, max_line: int = 40,
                        max_lines: int = 4) -> dict:
    """把每个标题下的变更精炼成几行中文要点（日文先翻译）。返回 {标题: [要点]}；失败时调用方用原文段落。"""
    sections = {}
    for kind, mark in (("added", "新增"), ("changed", "修改"), ("removed", "删除")):
        for item in changes[kind]:
            if item.get("level") or not item["text"].strip():
                continue
            group = sections.setdefault(item.get("section") or "（无标题）", [])
            if len(group) < max_items:
                group.append(f"{mark}：{item['text'][:300]}")
    keys = list(sections)[:max_sections]
    if not keys:
        return {}
    payload = [{"i": i, "section": section[-80:], "changes": sections[section]} for i, section in enumerate(keys)]
    system = (f"你是游戏项目的通知助理。下面每一组是同一标题下的文档变更。把每组精炼成简体中文要点：能一行说清就只写一行；"
              f"一行写不下时分成多行，每行一个要点，最多 {max_lines} 行。每行不超过 {max_line} 字，说清楚改了什么、需要谁做什么或确认什么；"
              "不要把一句话硬拆开。日文先翻译成中文再精炼；忽略图片文件名等附件；数字、ID、角色名、技能名（如 Skill01_EX）保持原样。"
              '只输出 JSON 数组，不要解释，格式：[{"i":0,"lines":["要点1","要点2"]}]')
    rows = parse_json_array(ask([{"role": "system", "content": system},
                                 {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]))
    result = {}
    for row in rows:
        try:
            section = keys[int(row.get("i"))]
        except (TypeError, ValueError, IndexError, AttributeError):
            continue
        raw = row.get("lines") if isinstance(row.get("lines"), list) else [row.get("line")]
        lines = [re.sub(r"\s+", " ", x.strip()) for x in raw if isinstance(x, str) and x.strip()][:max_lines]
        if lines and not any(KANA.search(x) for x in lines):
            result[section] = lines
    return result


def summarize_with_model(title: str, changes: dict, ask) -> str:
    def clip(items):
        return [{"section": it.get("section") or "", "text": it["text"][:120]} for it in items[:12]]

    data = {"doc": title, "added": clip(changes["added"]), "changed": clip(changes["changed"]),
            "removed": clip(changes["removed"])}
    answer = ask([
        {"role": "system", "content": "你是游戏项目的通知助理。用一句话概括下面这组文档变更（40 字以内，中文，不要客套话），"
                                      "点明是哪个功能/角色/交付物发生了什么类型的变动。只输出这句话本身。"},
        {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
    ])
    return answer.strip().split("\n")[0]


# ---------------------------------------------------------------- 看板：一批改动涉及的人和功能

def batch_id(row: dict) -> str:
    return f"{row.get('token')}@{row.get('time')}"


def mapping_people(mapping: dict) -> dict:
    """名单里的人 → 职责说明（模块负责人、抄送、功能策划）。"""
    people = {}
    for name, info in (mapping.get("owners") or {}).items():
        people[name] = (info or {}).get("role") or ""
    for route in mapping.get("routes") or []:
        label = route.get("name") or route.get("key")
        if route.get("owner"):
            people[route["owner"]] = people.get(route["owner"]) or f"{label}负责人"
        for name in route.get("cc") or []:
            people.setdefault(name, f"{label}抄送")
    for feature in mapping.get("features") or []:
        if feature.get("owner"):
            people.setdefault(feature["owner"], "功能策划")
    return people


def batch_sections(row: dict) -> list:
    """一批改动按标题分组：[{section, items: [{kind, text}]}]，保持文档里的顺序。"""
    groups = {}
    for item in row.get("items") or []:
        groups.setdefault(item.get("section") or "", []).append({"kind": item.get("kind"), "text": item.get("text") or ""})
    return [{"section": section, "items": items} for section, items in groups.items()]


def analyze_batch(row: dict, mapping: dict, ask) -> dict:
    """让 AI 看一批改动：每个标题下改了什么、涉及哪些功能、该谁跟进、为什么。名字和功能只认名单里有的。"""
    sections = batch_sections(row)[:30]
    if not sections:
        return {"summary": "", "sections": [], "people": []}
    people = mapping_people(mapping)
    features = mapping.get("features") or []
    routes = "\n".join(f"- {r.get('name') or r['key']}：负责人 {r.get('owner') or '无'}"
                       f"{'，抄送 ' + '、'.join(r['cc']) if r.get('cc') else ''}；关键词 {'、'.join((r.get('keywords') or [])[:10])}"
                       for r in mapping.get("routes") or [])
    feature_text = "\n".join(f"- {f.get('name')}（策划 {f.get('owner')}）：{'、'.join((f.get('keywords') or [])[:6])}"
                             for f in features if f.get("owner"))
    rules = "\n".join(f"- {x}" for x in mapping.get("_已确认规则") or [])
    system = "\n\n".join(x for x in (
        "你是游戏项目（航海王手游）的策划助理，帮策划看懂版权方监修文档的改动。",
        f"模块和负责人：\n{routes}",
        f"功能对照表（功能名 / 负责策划 / 关键词）：\n{feature_text}" if feature_text else "",
        f"已确认的分工规则：\n{rules}" if rules else "",
        "对每一组改动：用中文写 1~3 条要点（日文先翻成中文，每条不超过 50 字，说清改了什么、要谁做什么或确认什么）；"
        "列出涉及的功能（只能用功能对照表里的功能名，没有就给空数组）；列出需要跟进的人（只能用上面出现过的姓名），"
        "每人一句理由（不超过 30 字）。最后给整批一句话概括（40 字以内）和整批需要跟进的人（去重，带理由）。",
        "只输出 JSON，不要解释，格式："
        '{"summary":"…","sections":[{"i":0,"points":["…"],"features":["…"],"people":[{"name":"…","reason":"…"}]}],'
        '"people":[{"name":"…","reason":"…"}]}',
    ) if x)
    payload = {"doc": row.get("doc"), "groups": [
        {"i": i, "section": s["section"][-100:] or "（无标题）",
         "changes": [f"{KIND_LABEL.get(it['kind'], '')}：{it['text'][:200]}" for it in s["items"][:10]]}
        for i, s in enumerate(sections)]}
    answer = ask([{"role": "system", "content": system}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}])
    start = answer.find("{")
    data = json.loads(answer[start:answer.rfind("}") + 1]) if start >= 0 else {}
    feature_names = {f.get("name") for f in features}

    def clean_people(items) -> list:
        result, seen = [], set()
        for p in items if isinstance(items, list) else []:
            name = str((p or {}).get("name") or "").strip() if isinstance(p, dict) else str(p).strip()
            if name in people and name not in seen:
                seen.add(name)
                result.append({"name": name, "reason": str((p or {}).get("reason") or "")[:60] if isinstance(p, dict) else "",
                               "role": people[name]})
        return result

    out_sections = []
    for s in data.get("sections") or []:
        try:
            index = int(s.get("i"))
            section = sections[index]["section"]
        except (TypeError, ValueError, IndexError, AttributeError):
            continue
        points = [str(x).strip()[:80] for x in s.get("points") or [] if str(x).strip()][:3]
        out_sections.append({"section": section, "points": points,
                             "features": [f for f in dict.fromkeys(s.get("features") or []) if f in feature_names],
                             "people": clean_people(s.get("people"))})
    all_people = clean_people(data.get("people"))
    named = {p["name"] for p in all_people}
    for s in out_sections:  # 分组里提到、整批漏了的人补上
        for p in s["people"]:
            if p["name"] not in named:
                named.add(p["name"])
                all_people.append(p)
    return {"summary": str(data.get("summary") or "").strip()[:80], "sections": out_sections, "people": all_people}


KIND_LABEL = {"added": "新增", "changed": "修改", "removed": "删除"}


# ---------------------------------------------------------------- 监听器

def load_settings() -> tuple:
    """(config, mapping)；没配置或 enabled=false 时 config 为 None。"""
    try:
        with open(CONFIG_PATH, encoding="utf-8") as file:
            config = json.load(file)
    except (OSError, ValueError):
        return None, None
    if not config.get("enabled", True):
        return None, None
    path = config.get("mapping") or os.path.basename(MAPPING_PATH)
    if not os.path.isabs(path):
        path = os.path.join(os.path.dirname(CONFIG_PATH), path)
    with open(path, encoding="utf-8") as file:
        mapping = json.load(file)
    return config, mapping


class DocWatcher:
    def __init__(self, request, send, ask, mapping: dict, config: dict, root: str = DATA_ROOT, log=print, report=print):
        self._raw_request = request
        self._send = send
        self.ask = ask
        self.mapping = mapping
        self.config = config
        self.root = root
        self.log = log
        self.report = report
        self.docs = [parse_doc_source(entry) for entry in config.get("docs") or []]
        self.wiki_base = (config.get("wikiBase") or DEFAULT_WIKI_BASE).rstrip("/")
        self.stale_sec = float(config.get("staleMinutes", 10)) * 60
        clarify = config.get("clarify") or {}
        self.asker = clarify.get("asker") or ""
        self.admins = config.get("admins") or ([self.asker] if self.asker else [])
        self.clarify_enabled = clarify.get("enabled", True)
        self.options = route_options(mapping)
        self._check_lock = threading.Lock()
        self._file_lock = threading.RLock()

    # ------------------------------------------------ 文件
    def _path(self, name: str) -> str:
        return os.path.join(self.root, name)

    def load(self, name: str, fallback):
        with self._file_lock:
            try:
                with open(self._path(name), encoding="utf-8") as file:
                    return json.load(file)
            except (OSError, ValueError):
                return fallback

    def save(self, name: str, value) -> None:
        path = self._path(name)
        with self._file_lock:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path + ".tmp", "w", encoding="utf-8") as file:
                json.dump(value, file, ensure_ascii=False, indent=2)
            os.replace(path + ".tmp", path)

    def append(self, name: str, row: dict) -> None:
        with self._file_lock:
            os.makedirs(self.root, exist_ok=True)
            with open(self._path(name), "a", encoding="utf-8") as file:
                file.write(json.dumps(row, ensure_ascii=False) + "\n")

    def read_rows(self, name: str) -> list:
        rows = []
        try:
            with open(self._path(name), encoding="utf-8") as file:
                for line in file:
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        continue
        except OSError:
            pass
        return rows

    # ------------------------------------------------ 飞书
    def request(self, method: str, path: str, params: dict = None, data: dict = None) -> dict:
        for attempt in range(5):
            body = self._raw_request(method, path, params, data)
            if body.get("code") not in (99991400, 429) or attempt >= 4:
                return body
            time.sleep(2 ** attempt)  # 飞书限流
        return body

    @staticmethod
    def ok(body: dict, what: str) -> dict:
        if body.get("code") != 0:
            raise RuntimeError(f"{what}失败（飞书错误码 {body.get('code', '未知')}）")
        return body.get("data") or {}

    # ------------------------------------------------ 人
    def people(self) -> dict:
        """mapping.owners 里的 open_id 可能是别的应用的；/bind 记下的本应用 open_id 优先。"""
        bindings = self.load("bindings.json", {})
        result = {}
        for name, info in (self.mapping.get("owners") or {}).items():
            info = info or {}
            result[name] = {"role": info.get("role") or "",
                            "openId": bindings.get(name) or info.get("openId") or info.get("open_id") or ""}
        for name, open_id in bindings.items():
            result.setdefault(name, {"role": "", "openId": open_id})
        return result

    def name_of(self, open_id: str) -> str:
        return next((name for name, p in self.people().items() if p["openId"] == open_id), "")

    def open_id_of(self, name: str) -> str:
        return (self.people().get(name) or {}).get("openId") or ""

    def send_to(self, name: str, markdown: str) -> str:
        person = self.people().get(name)
        if not person or not person["openId"]:
            return f"SKIP：{name} 没有 open_id（请让对方私聊机器人发送 /bind {name}）"
        code = self._send(person["openId"], markdown)
        if code == 0:
            return "OK"
        # 别的应用的 open_id 重试也没用，和没 open_id 一样是配置问题：报出来，但不拦基线
        if code == 99992361:
            return f"SKIP：{name} 的 open_id 属于其他应用（请让对方私聊机器人发送 /bind {name}）"
        hint = "（对方不在应用可用范围内）" if code == 230013 else ""
        return f"ERR {code}{hint}"

    def known_names(self) -> set:
        m = self.mapping
        names = set(m.get("owners") or {})
        for route in m.get("routes") or []:
            names.add(route.get("owner"))
            names.update(route.get("cc") or [])
        names.update(m.get("leads") or [])
        names.update([m.get("arbiter"), self.asker])
        names.update((self.config.get("digest") or {}).get("to") or [])
        names.update(self.admins)
        return {n for n in names if n}

    # ------------------------------------------------ 拉文档
    def fetch_doc(self, source: dict, node: dict) -> dict:
        blocks = []
        page_token = ""
        while True:
            params = {"page_size": 500, "document_revision_id": -1}
            if page_token:
                params["page_token"] = page_token
            data = self.ok(self.request("GET", f"/open-apis/docx/v1/documents/{node['obj_token']}/blocks", params),
                           "读取文档块")
            blocks.extend(data.get("items") or [])
            page_token = data.get("page_token") if data.get("has_more") else ""
            if not page_token:
                break
        return {"token": source["token"], "title": node.get("title") or source["name"],
                "editTime": str(node.get("obj_edit_time") or ""), "fetchedAt": stamp(),
                "blocks": flatten_blocks(blocks, node["obj_token"])}

    @staticmethod
    def snapshot_name(token: str) -> str:
        return os.path.join("snapshots", f"{token}.json")

    # ------------------------------------------------ 分发
    def message_for(self, doc: dict, name: str, role: str, subset: dict, extra: dict) -> str:
        condensed = extra.get("condensed") or {}
        refined = any(condensed.get(item.get("section") or "（无标题）") for kind in KINDS for item in subset[kind])
        body_lines = []
        for line in summarize(subset, condensed).split("\n"):
            if line.startswith("▸"):
                body_lines.append(f"**{line}**")
            elif line.startswith("   "):
                body_lines.append(f"- {line.strip()}")
            else:
                body_lines.append(line)
        lines = [
            f"**📌 {doc['title']}**",
            f"{f'{name}（{role}）' if role else name} · {extra.get('subtitle') or '你负责的部分有更新'}（{headline(subset)}）",
            "⚠️ 智能归类暂时不可用，本条按关键词规则分发，可能有偏差；如需纠正直接回复。" if extra.get("degraded") else "",
            extra.get("sentence") or "",
            "",
            "\n".join(body_lines),
            "（以上由 AI 精炼，日文已译为中文，原文见文档）" if refined else "",
            "",
            f"🔗 {self.wiki_base}/{doc['token']}",
        ]
        kept = [line for n, line in enumerate(lines) if line or (n > 0 and lines[n - 1])]
        return "\n".join(kept)

    def dispatch(self, doc: dict, changes: dict, dry_run: bool, degraded: bool, sentence: str, condensed: dict,
                 delivered: set) -> tuple:
        learned = self.load("learned.json", {})
        directory = self.people()
        unsubscribed = self.load("unsubscribed.json", {})
        buckets = {}
        unmatched = []
        for kind in KINDS:
            for item in changes[kind]:
                found = attribute(item, self.mapping, learned)
                names = list(dict.fromkeys(owners_for(found["keys"], self.mapping) + found["people"]))
                if not names:
                    unmatched.append(item)
                    if self.clarify_enabled:
                        continue
                    names = list(self.mapping.get("leads") or [])
                for name in names:
                    bucket = buckets.setdefault(name, {"added": [], "changed": [], "removed": [], "lead": not found["keys"]})
                    bucket[kind].append(item)
        results = {}
        for name, subset in buckets.items():
            if name in delivered:
                results[name] = "SKIP：这批变更已发过"
                continue
            if unsubscribed.get(name):
                results[name] = "SKIP：已取消推送"
                continue
            markdown = self.message_for(doc, name, (directory.get(name) or {}).get("role"), subset, {
                "degraded": degraded, "sentence": sentence, "condensed": condensed,
                "subtitle": "未匹配到具体模块，已同步组长" if subset["lead"] else ""})
            if dry_run:
                results[name] = f"DRY-RUN\n{markdown}"
                continue
            results[name] = self.send_to(name, markdown)
            if results[name] == "OK":
                self.append("notify-log.jsonl", {"time": stamp(), "doc": doc["title"], "token": doc["token"],
                                                 "owner": name, "count": count_changes(subset)})
        return results, unmatched

    # ------------------------------------------------ 问维护者 / 升级
    def ask_unmatched(self, doc: dict, unmatched: list) -> int:
        if not self.clarify_enabled or not self.asker or not unmatched:
            return 0
        pending = self.load("pending.json", {})
        asked = 0
        choices = "\n".join(f"{o['digit']} {o['name']}" for o in self.options)
        for item in unmatched:
            section = item.get("section") or ""
            if section in pending or asked >= 3:
                continue
            text = (f"❓ **有条变更我看不出该归谁，帮我确认一下**\n\n📄 {doc['title']}\n📎 {section or '（无标题）'}\n"
                    f"内容：{item['text'][:120]}\n\n回复数字（可多个，如「1,7」）：\n{choices}\n\n回复后会记住这条标题的归属，下次不再问。")
            if self.send_to(self.asker, text) != "OK":
                continue
            pending[section] = {"docTitle": doc["title"], "text": item["text"][:300],
                                "link": f"{self.wiki_base}/{doc['token']}", "askedAt": stamp()}
            asked += 1
        self.save("pending.json", pending)
        return asked

    def escalate(self, reason: str, detail: str) -> bool:
        arbiter = self.mapping.get("arbiter")
        if not arbiter:
            return False
        sent = self.send_to(arbiter, f"⚖️ **需要你定一下（{reason}）**\n\n{detail}\n\n—— 回我一句怎么处理就行，我按你的决定改规则。")
        self.append("escalations.jsonl", {"time": stamp(), "reason": reason, "detail": detail, "sent": sent})
        return sent == "OK"

    def label(self, keys: list) -> str:
        return "、".join(o["name"] for o in self.options if o["key"] in keys)

    def answer_clarify(self, text: str, sender: str):
        pending = self.load("pending.json", {})
        if not pending:
            return None
        if re.search(r"[^\d\s,，、]", text):
            return None  # 不是数字回复
        keys = list(dict.fromkeys(o["key"] for d in re.findall(r"\d+", text) for o in self.options if o["digit"] == d))
        if not keys:
            return None
        section = max(pending, key=lambda s: pending[s].get("askedAt") or "")
        learned = self.load("learned.json", {})
        if learned.get(section) and sorted(learned[section]) != sorted(keys):
            self.escalate("同一标题被要求归到不同模块",
                          f"标题：{section}\n原来归「{self.label(learned[section])}」，{sender or '有人'}要求改成「{self.label(keys)}」。")
            return (f"⚠️ 这条和之前的记录不一致：\n原来：「{section}」→ {self.label(learned[section])}\n"
                    f"这次：「{section}」→ {self.label(keys)}\n\n已转给{self.mapping.get('arbiter') or '决策人'}确认，在此之前仍按原规则分发。")
        learned[section] = keys
        self.save("learned.json", learned)
        info = pending.pop(section)
        self.save("pending.json", pending)
        forwarded = []
        for name in dict.fromkeys(owners_for(keys, self.mapping)):
            body = (f"**📌 {info['docTitle']}**\n▸ {section or '（无标题）'}\n{info['text'][:200]}\n\n"
                    f"（这条之前无法自动归类，已按 {self.label(keys)} 补发）\n🔗 {info['link']}")
            if self.send_to(name, body) == "OK":
                forwarded.append(name)
        extra = f"\n已补发给：{'、'.join(forwarded)}" if forwarded else ""
        return f"✅ 记住了：「{section or '（无标题）'}」→ {self.label(keys)}\n以后这个标题下的变更会自动归到这些模块。{extra}"

    def bind_mentioned(self, item: dict, text: str, known: set) -> str:
        """管理员批量绑定：「/bind @殷琦 @万汩」或「/bind 宋子由 @宋自由」（名单里的姓名在前，人在后）。"""
        sender = self.name_of(item["userId"])
        if sender not in self.admins:
            return f"只有管理员（{'、'.join(self.admins) or '未配置'}）可以替别人绑定。管理员需先私聊机器人发送 /bind 自己的姓名。"
        bindings = self.load("bindings.json", {})
        done, failed = [], []
        tokens = text[len("/bind"):].split()
        for n, token in enumerate(tokens):
            if not token.startswith("@"):
                continue
            person = next((m for m in item["mentions"] if f"@{m['name']}" == token), None)
            if not person:
                failed.append(f"{token}（没识别到这个 @）")
                continue
            alias = tokens[n - 1] if n > 0 and not tokens[n - 1].startswith("@") else ""
            # 显示名常带装饰（「阿飞（苏敬袁）」）：退回到显示名里包含的最长名单姓名
            contained = sorted((k for k in known if k in person["name"]), key=len, reverse=True)
            name = alias or (person["name"] if person["name"] in known else (contained[0] if contained else person["name"]))
            if name not in known:
                failed.append(f"{name}（不在人员名单里，可写成「名单里的姓名 @此人」）")
                continue
            bindings[name] = person["openId"]
            done.append(f"{name} → {person['name']}" if alias and alias != person["name"] else name)
        if done:
            self.save("bindings.json", bindings)
        missing = [name for name in sorted(known) if not bindings.get(name)]
        return "\n".join(x for x in (
            f"✅ 已绑定 {len(done)} 人：{'、'.join(done)}" if done else "没有绑定任何人。",
            f"⚠️ 未绑定：{'；'.join(failed)}" if failed else "",
            f"还没绑定：{'、'.join(missing)}" if missing else "名单里的人已全部绑定。",
        ) if x)

    def alert_degraded(self, reason: str, dry_run: bool) -> None:
        health = self.load("health.json", {})
        if dry_run or health.get("lastNotice") == today() or not self.asker:
            return
        text = (f"⚠️ **文档通知机器人告警**\n\n智能归类不可用：{reason}\n\n"
                "已自动切换到关键词规则分发——通知照常发，但归类可能不准。恢复后会自动切回。")
        if self.send_to(self.asker, text) == "OK":
            self.save("health.json", {"lastNotice": today(), "reason": reason, "at": stamp()})

    # ------------------------------------------------ 一轮检查
    def check(self, dry_run: bool = False, force: bool = False) -> str:
        """同一时间只跑一轮；正在跑时直接返回。"""
        if not self._check_lock.acquire(blocking=False):
            return "上一轮还没跑完，这次跳过。"
        try:
            return self._check(dry_run, force)
        finally:
            self._check_lock.release()

    def _check(self, dry_run: bool, force: bool) -> str:
        summary = []
        use_model = self.ask is not None and self.config.get("useModel", True) is not False
        for source in self.docs:
            try:
                node = self.ok(self.request("GET", "/open-apis/wiki/v2/spaces/get_node", {"token": source["token"]}),
                               "读取文档节点")["node"]
                edited_ago = time.time() - float(node.get("obj_edit_time") or 0)
                old = self.load(self.snapshot_name(source["token"]), None)
                if old and old.get("editTime") == str(node.get("obj_edit_time")):
                    summary.append(f"{node.get('title')}：无变更")
                    continue
                # 刚编辑过的先不推，对方可能还在写
                if not force and old and edited_ago < self.stale_sec:
                    summary.append(f"{node.get('title')}：{round(edited_ago / 60)} 分钟前刚编辑过，下轮再看")
                    continue
                doc = self.fetch_doc(source, node)
                changes = diff_docs(old, doc)
                if changes["firstRun"]:
                    if not dry_run:
                        self.save(self.snapshot_name(source["token"]), doc)
                    summary.append(f"{doc['title']}：首次运行，建立基线（{len(doc['blocks'])} 块），不通知")
                    continue
                total = count_changes(changes)
                if not total:
                    if not dry_run:
                        self.save(self.snapshot_name(source["token"]), doc)
                    summary.append(f"{doc['title']}：编辑时间变了但正文无变化")
                    continue
                degraded, sentence = False, ""
                if use_model:
                    try:
                        annotate_with_model(changes, self.mapping, self.ask)
                        try:
                            sentence = summarize_with_model(doc["title"], changes, self.ask)
                        except Exception:
                            sentence = ""
                    except Exception as error:
                        degraded = True
                        self.alert_degraded(str(error)[:200], dry_run)
                # 精炼（含翻译）尽力而为：失败就照旧发段落列表
                condensed = {}
                if self.ask is not None and self.config.get("condense", True) is not False:
                    try:
                        condensed = condense_with_model(changes, self.ask)
                    except Exception:
                        condensed = {}
                # 和上一轮失败的是同一批：已经收到的人不再发
                signature = hashlib.sha1(json.dumps(
                    [[f"{item['id']}:{item['text']}" for item in changes[kind]] for kind in KINDS],
                    ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
                deliveries = self.load("deliveries.json", {})
                previous = deliveries.get(source["token"]) or {}
                state = previous if previous.get("signature") == signature else {"signature": signature, "sent": [], "attempts": 0}
                results, unmatched = self.dispatch(doc, changes, dry_run, degraded, sentence, condensed, set(state["sent"]))
                failed = any(r.startswith("ERR") for r in results.values())
                gave_up = False
                if not dry_run:
                    state["sent"] = list(dict.fromkeys(state["sent"] + [n for n, r in results.items() if r == "OK"]))
                    state["attempts"] = int(state.get("attempts") or 0) + 1
                    gave_up = failed and state["attempts"] >= MAX_ATTEMPTS
                    record = {"time": (state.get("pending") or {}).get("time") or stamp(), "doc": doc["title"],
                              "token": doc["token"], "condensed": condensed, "sentence": sentence,
                              "recipients": {n: ("OK" if n in state["sent"] else r.split("：")[0][:60])
                                             for n, r in results.items()},
                              "items": [{"kind": kind, "section": item.get("section") or "", "text": item["text"][:300]}
                                        for kind in KINDS for item in [i for i in changes[kind] if not i.get("level")][:80]]}
                    # SKIP（没 open_id / 退订）是配置问题，不拦基线；真发送失败保留旧基线下轮重试（最多 MAX_ATTEMPTS 轮）
                    if not failed or gave_up:
                        self.save(self.snapshot_name(source["token"]), doc)
                        deliveries.pop(source["token"], None)
                        self.append("changes-log.jsonl", record)
                        self.ask_unmatched(doc, unmatched)
                        if gave_up:
                            errs = "；".join(f"{n} {r}" for n, r in results.items() if r.startswith("ERR"))
                            self.report(f"[文档监测] 《{doc['title']}》连续 {MAX_ATTEMPTS} 轮有人发送失败，已放弃重试：{errs}")
                    else:
                        deliveries[source["token"]] = {**state, "pending": record}  # 问「改了什么」时也算
                    self.save("deliveries.json", deliveries)
                lines = [f"  → {n}：{r.replace('DRY-RUN', '', 1) if dry_run else r}" for n, r in results.items()]
                note = "" if not failed else (f"，连续 {MAX_ATTEMPTS} 轮有人发送失败，已放弃重试并推进基线" if gave_up
                                               else "，有发送失败，基线不推进，下轮只重发给失败的人")
                summary.append(f"{doc['title']}：{total} 处变更{'（模型不可用，按关键词分发）' if degraded else ''}{note}\n"
                               + "\n".join(lines))
            except Exception as error:
                summary.append(f"{source['name']}：检查失败 {error}")
        return "\n".join(summary)

    def digest(self, dry_run: bool = False) -> str:
        to = (self.config.get("digest") or {}).get("to") or []
        if not to:
            return ""
        day = today()
        by_doc = {}
        for row in self.read_rows("notify-log.jsonl"):
            if not str(row.get("time") or "").startswith(day):
                continue
            owners = by_doc.setdefault(row.get("doc"), {})
            owners[row.get("owner")] = owners.get(row.get("owner"), 0) + int(row.get("count") or 0)
        lines = [f"**{doc}**\n" + "\n".join(f"- {o}：{n} 处" for o, n in owners.items()) for doc, owners in by_doc.items()]
        text = f"📊 **文档变更日报 · {day}**\n\n" + ("\n\n".join(lines) if lines else "今天没有文档变更通知。")
        if not dry_run:
            for name in to:
                self.send_to(name, text)
        return text

    def recent_changes(self, days: int = 7, max_chars: int = 6000) -> str:
        """最近送出的变更批次（新的在前），给「监修文档改了什么」这类问题当资料。"""
        rows = self.read_rows("changes-log.jsonl")
        # 还在给部分人重试的批次也是真实变更
        rows += [s["pending"] for s in self.load("deliveries.json", {}).values() if isinstance(s, dict) and s.get("pending")]
        rows.sort(key=lambda r: r.get("time") or "")
        since = stamp(datetime.datetime.now() - datetime.timedelta(days=days))
        parts, used = [], 0
        for row in reversed([r for r in rows if (r.get("time") or "") >= since]):
            sections = {}
            for item in row.get("items") or []:
                sections.setdefault(item.get("section") or "", []).append(f"{KIND_MARK.get(item.get('kind'), '')} {item.get('text')}")
            body = []
            for section, lines in sections.items():
                points = (row.get("condensed") or {}).get(section or "（无标题）")
                shown = (points if isinstance(points, list) else [points]) if points else lines[:6]
                body.append(f"▸ {section or '（无标题）'}\n" + "\n".join(f"  {x}" for x in shown))
            part = f"【{row.get('doc')}】{row.get('time')} 检测到的变更 {self.wiki_base}/{row.get('token')}\n" + "\n".join(body)
            if used + len(part) > max_chars:
                break
            parts.append(part)
            used += len(part)
        return "\n\n".join(parts)

    # ------------------------------------------------ 看板用的 AI 分析
    def analyze_pending(self, limit: int = 5) -> int:
        """给还没分析过的变更批次（新的先）跑 AI 分析，存 analysis.json。返回这次分析了几批。"""
        if self.ask is None:
            return 0
        done = 0
        for row in sorted(self.read_rows("changes-log.jsonl"), key=lambda r: r.get("time") or "", reverse=True):
            if done >= limit:
                break
            key = batch_id(row)
            analyses = self.load("analysis.json", {})
            if key in analyses and not analyses[key].get("error"):
                continue
            if analyses.get(key, {}).get("tries", 0) >= 3:
                continue  # 连着失败 3 次的不再试
            try:
                result = analyze_batch(row, self.mapping, self.ask)
            except Exception as error:
                result = {"error": str(error)[:200], "tries": analyses.get(key, {}).get("tries", 0) + 1}
                self.report(f"[文档监测] 分析《{row.get('doc')}》{row.get('time')} 失败：{str(error)[:120]}")
            result["at"] = stamp()
            with self._file_lock:
                analyses = self.load("analysis.json", {})
                analyses[key] = result
                self.save("analysis.json", analyses)
            done += 1
        return done

    def start_analysis(self) -> None:
        """后台补分析，不挡定时检查；正在跑就不再开。"""
        if getattr(self, "_analysis_running", False) or self.ask is None:
            return
        self._analysis_running = True

        def run():
            try:
                while self.analyze_pending(limit=3):
                    pass
            except Exception as error:
                self.report(f"[文档监测] 分析出错：{error}")
            finally:
                self._analysis_running = False

        threading.Thread(target=run, name="doc-watch-analysis", daemon=True).start()

    # ------------------------------------------------ 私聊指令
    def handle_direct_message(self, item: dict):
        """item: {text, userId, mentions: [{name, openId}]}。返回回复文字；None 表示不归这里管。"""
        text = item["text"].strip()
        known = self.known_names()
        if text.startswith("/bind") and item.get("mentions"):
            return self.bind_mentioned(item, text, known)
        match = re.match(r"^/bind\s+(\S+)$", text)
        if match:
            name = match.group(1)
            if name not in known:
                return f"「{name}」不在文档通知的人员名单里，请检查姓名。"
            bindings = self.load("bindings.json", {})
            if bindings.get(name) and bindings[name] != item["userId"]:
                return f"「{name}」已绑定其他账号；如需更换请联系维护者{f' {self.asker}' if self.asker else ''}。"
            bindings[name] = item["userId"]
            self.save("bindings.json", bindings)
            return f"✅ 已绑定：之后「{name}」负责模块的文档变更会私聊发给你。"
        name = self.name_of(item["userId"])
        if name and CANCEL.match(text):
            self.save("unsubscribed.json", {**self.load("unsubscribed.json", {}), name: stamp()})
            return "已取消文档更新推送，之后不会再收到通知。想恢复时发「恢复推送」。"
        if name and RESUME.match(text):
            unsubscribed = self.load("unsubscribed.json", {})
            unsubscribed.pop(name, None)
            self.save("unsubscribed.json", unsubscribed)
            return "已恢复文档更新推送。"
        if name or (self.asker and item["userId"] == self.open_id_of(self.asker)):
            return self.answer_clarify(text, name)
        return None

    # ------------------------------------------------ 定时
    def loop(self) -> None:
        """每到 checkMinutes（默认 :00 / :30）检查一次，每天 digest.time 发日报。启动时先查一次建基线。"""
        minutes = [int(m) for m in self.config.get("checkMinutes") or [0, 30]]
        digest_cfg = self.config.get("digest") or {}
        try:
            digest_hour, digest_minute = (int(x) for x in str(digest_cfg.get("time") or "21:00").split(":"))
        except ValueError:
            digest_hour, digest_minute = 21, 0
        try:
            self.log(f"[文档监测 {stamp()}] 启动检查\n{self.check()}")
        except Exception as error:
            self.report(f"[文档监测] 启动检查失败：{error}")
        self.start_analysis()
        last_slot, last_digest = "", ""
        while True:
            time.sleep(20)
            now = datetime.datetime.now()
            slot = now.strftime("%Y-%m-%d %H:%M")
            if now.minute in minutes and slot != last_slot:
                last_slot = slot
                try:
                    self.log(f"[文档监测 {stamp(now)}]\n{self.check()}")
                except Exception as error:
                    self.report(f"[文档监测] 本轮失败：{error}")
                self.start_analysis()
            if digest_cfg.get("to") and now.hour == digest_hour and now.minute == digest_minute and last_digest != today(now):
                last_digest = today(now)
                try:
                    self.digest()
                except Exception as error:
                    self.report(f"[文档监测] 日报发送失败：{error}")
