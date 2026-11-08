"""监修改动看板的数据：只读 data/doc_watch/ 下大豆文档监测写的文件，网页不改任何东西。

- changes-log.jsonl：每批检测到并送出的改动（deliveries.json 里还在重试的批次也算）
- analysis.json：大豆后台给每批跑的 AI 分析（每个标题的要点、涉及功能、该谁跟进）
- notify-log.jsonl：实际私聊给了谁（老批次没记 recipients 时按时间对上）
- pending.json：归不上、等维护者确认归属的改动
没有 AI 分析的批次用归因规则（关键词 / 功能对照表 / 人工确认）兜底，标明是规则算的。
"""
import datetime
import os

from bot import doc_watch

ROOT = doc_watch.DATA_ROOT
MAX_BATCHES = 300


def _watcher_files():
    """只读用的 DocWatcher：复用它的读文件函数，不连飞书。"""
    try:
        config, mapping = doc_watch.load_settings()
    except (OSError, ValueError):
        config, mapping = None, None
    return config or {}, mapping or {}


def _reader(config: dict, mapping: dict) -> doc_watch.DocWatcher:
    return doc_watch.DocWatcher(lambda *a: {"code": -1}, lambda *a: -1, None, mapping, dict(config, docs=[]), root=ROOT,
                                log=lambda *_: None, report=lambda *_: None)


def _parse(text: str):
    try:
        return datetime.datetime.strptime(str(text or "")[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _matched_recipients(row: dict, notify_rows: list) -> dict:
    """老批次没存 recipients：同一文档、批次记录前 30 分钟内发出的通知就是这批的。"""
    end = _parse(row.get("time"))
    if not end:
        return {}
    start = end - datetime.timedelta(minutes=30)
    found = {}
    for n in notify_rows:
        sent = _parse(n.get("time"))
        if n.get("token") == row.get("token") and sent and start <= sent <= end + datetime.timedelta(minutes=2):
            found[n.get("owner")] = "OK"
    return found


def _rule_view(row: dict, mapping: dict, learned: dict) -> dict:
    """没有 AI 分析时的兜底：每个标题按归因规则算模块负责人和功能策划。"""
    names = {r["key"]: r.get("name") or r["key"] for r in mapping.get("routes") or []}
    roles = doc_watch.mapping_people(mapping)
    sections, everyone = [], {}
    for group in doc_watch.batch_sections(row):
        keys, people, features, via = [], [], [], ""
        for item in group["items"]:
            found = doc_watch.attribute({"section": group["section"], "text": item["text"]}, mapping, learned)
            keys += found["keys"]
            people += doc_watch.owners_for(found["keys"], mapping) + found["people"]
            features += [f.get("name") for f in doc_watch.feature_matches({"section": group["section"], "text": item["text"]}, mapping)]
            via = via or found["via"].split("；")[0]
        modules = [names.get(k, k) for k in dict.fromkeys(keys)]
        reason = f"{'、'.join(modules)}相关（{via}）" if modules else "规则没归上"
        persons = [{"name": n, "role": roles.get(n, ""), "reason": reason} for n in dict.fromkeys(people)]
        for p in persons:
            everyone.setdefault(p["name"], p)
        sections.append({"section": group["section"], "points": [], "features": list(dict.fromkeys(features)),
                         "people": persons, "modules": modules})
    return {"summary": "", "sections": sections, "people": list(everyone.values())}


def board_view() -> dict:
    config, mapping = _watcher_files()
    reader = _reader(config, mapping)
    wiki_base = (config.get("wikiBase") or doc_watch.DEFAULT_WIKI_BASE).rstrip("/")
    rows = reader.read_rows("changes-log.jsonl")
    retrying = []
    for state in reader.load("deliveries.json", {}).values():
        if isinstance(state, dict) and state.get("pending"):
            retrying.append(dict(state["pending"], retrying=True))
    rows = sorted(rows + retrying, key=lambda r: r.get("time") or "", reverse=True)[:MAX_BATCHES]
    analyses = reader.load("analysis.json", {})
    notify_rows = reader.read_rows("notify-log.jsonl")
    learned = reader.load("learned.json", {})
    batches = []
    for row in rows:
        key = doc_watch.batch_id(row)
        analysis = analyses.get(key)
        ai = bool(analysis and not analysis.get("error") and analysis.get("sections") is not None)
        view = analysis if ai else _rule_view(row, mapping, learned)
        by_section = {s["section"]: s for s in view.get("sections") or []}
        rule = None if ai else view
        sections = []
        for group in doc_watch.batch_sections(row):
            info = by_section.get(group["section"]) or {}
            condensed = (row.get("condensed") or {}).get(group["section"] or "（无标题）") or []
            sections.append({
                "section": group["section"] or "（无标题）",
                "items": group["items"],
                "points": info.get("points") or (condensed if isinstance(condensed, list) else [condensed]),
                "features": info.get("features") or [],
                "people": info.get("people") or [],
            })
        counts = {kind: sum(1 for i in row.get("items") or [] if i.get("kind") == kind) for kind in doc_watch.KINDS}
        recipients = row.get("recipients") or _matched_recipients(row, notify_rows)
        batches.append({
            "id": key, "time": row.get("time") or "", "doc": row.get("doc") or "", "token": row.get("token") or "",
            "url": f"{wiki_base}/{row.get('token')}", "counts": counts, "sections": sections,
            "summary": (analysis or {}).get("summary") if ai else (row.get("sentence") or ""),
            "people": view.get("people") or [], "features": list(dict.fromkeys(f for s in sections for f in s["features"])),
            "source": "ai" if ai else "rule", "analyzed_at": (analysis or {}).get("at") if ai else "",
            "recipients": recipients, "retrying": bool(row.get("retrying")),
        })
    pending = [{"section": section or "（无标题）", "doc": info.get("docTitle") or "", "text": info.get("text") or "",
                "url": info.get("link") or "", "asked_at": info.get("askedAt") or ""}
               for section, info in reader.load("pending.json", {}).items() if isinstance(info, dict)]
    pending.sort(key=lambda p: p["asked_at"], reverse=True)
    docs = [{"name": d["name"], "url": f"{wiki_base}/{d['token']}"}
            for d in (doc_watch.parse_doc_source(e) for e in config.get("docs") or [])]
    return {
        "enabled": bool(config), "docs": docs, "batches": batches, "pending": pending,
        "asker": (config.get("clarify") or {}).get("asker") or "",
        "check_minutes": [int(m) for m in config.get("checkMinutes") or [0, 30]],
        "digest": (config.get("digest") or {}).get("time") or "",
    }
