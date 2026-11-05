"""自检复盘的数据和渲染：读 reviews/<日期>/<人>.json，算概要，把报告里的 markdown / diff 转成安全的 HTML 片段。

只读，不碰 git、不调模型。backend/routers/review.py 用它出接口。
渲染一律先整段转义，再只加 <b>、<code>、链接、标签这几种固定标记。
"""
import datetime
import html
import json
import os
import re

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
REVIEW_DIR = os.path.join(BOT_DIR, "reviews")
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.\-\u4e00-\u9fff]{1,40}$")
FEISHU_LINK = re.compile(r"^https://project\.feishu\.cn/\w+/(story|issue)/detail/\d+$")
LABEL_LINE = re.compile(r"^(改动|影响|提升|隐患)[：:]\s*")
LABEL_CLASS = {"改动": "l-change", "影响": "l-impact", "提升": "l-gain", "隐患": "l-risk"}
WEEKDAYS = "一二三四五六日"
AVATAR_COLORS = ("#4f6bed", "#0ea5a4", "#e8794a", "#8b5cf6", "#d9467a", "#2f9e5b", "#c48a12", "#3b82c4")
FILTERS = (("all", "全部"), ("risk", "需要留意"), ("bomb", "疑似炸线"), ("clean", "没看出问题"), ("empty", "无提交"))


def weekday(date: str) -> str:
    try:
        return "周" + WEEKDAYS[datetime.date.fromisoformat(date).weekday()]
    except ValueError:
        return ""


def link_html(match) -> str:
    url = match.group(1)
    if FEISHU_LINK.match(url):
        text = "缺陷单 ↗" if "/issue/" in url else "需求单 ↗"
        return f'<a class="ext" href="{url}" target="_blank" rel="noopener">{text}</a>'
    return f'<a href="{url}" target="_blank" rel="noopener">{url}</a>'


def render_inline(text: str) -> str:
    """只支持报告里用到的：**粗体**、`代码`、链接。先转义再替换，不放任何原始 HTML。"""
    escaped = html.escape(text or "")
    escaped = re.sub(r"(https?://[^\s<]+)", link_html, escaped)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)


def render_md(text: str) -> str:
    rows = []
    for line in (text or "").split("\n"):
        line = line.strip()
        if not line:
            if rows and rows[-1] != '<div class="gap"></div>':
                rows.append('<div class="gap"></div>')
            continue
        label = LABEL_LINE.match(line)
        if label:
            name = label.group(1)
            rows.append(f'<div class="ln"><span class="lbl {LABEL_CLASS[name]}">{name}</span>{render_inline(line[label.end():])}</div>')
        else:
            rows.append(f'<div class="ln">{render_inline(line)}</div>')
    return f'<div class="md">{"".join(rows)}</div>'


def render_diff(text: str) -> str:
    """把裁剪后的 unified diff 转成带颜色的 <pre>，整行先转义。"""
    if not (text or "").strip() or text.startswith("（没有代码类"):
        return ""
    rows = []
    for line in text.splitlines():
        escaped = html.escape(line)
        if line.startswith("+++") or line.startswith("---") or line.startswith("diff ") or line.startswith("index "):
            klass = "d-meta"
        elif line.startswith("@@"):
            klass = "d-hunk"
        elif line.startswith("+"):
            klass = "d-add"
        elif line.startswith("-"):
            klass = "d-del"
        else:
            klass = ""
        rows.append(f'<span class="{klass}">{escaped}</span>' if klass else escaped)
    return f'<pre class="diffpre">{"".join(rows)}</pre>'


def list_dates() -> list:
    if not os.path.isdir(REVIEW_DIR):
        return []
    return sorted((name for name in os.listdir(REVIEW_DIR) if DATE_PATTERN.match(name)), reverse=True)


def load_record(date: str, name: str) -> dict:
    if not DATE_PATTERN.match(date) or not NAME_PATTERN.match(name):
        return {}
    path = os.path.join(REVIEW_DIR, date, name + ".json")
    try:
        with open(path, encoding="utf-8") as record_file:
            data = json.load(record_file)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def day_records(date: str) -> list:
    folder = os.path.join(REVIEW_DIR, date)
    if not DATE_PATTERN.match(date) or not os.path.isdir(folder):
        return []
    rows = []
    for file_name in sorted(os.listdir(folder)):
        if file_name.endswith(".json"):
            record = load_record(date, file_name[:-5])
            if record:
                record["_file"] = file_name[:-5]
                rows.append(record)
    return rows


def risk_count(report: str) -> int:
    if "需要留意" not in (report or ""):
        return 0
    count = 0
    for line in report.split("需要留意", 1)[1].split("\n")[1:]:
        line = line.strip()
        if line.startswith("**"):
            break
        if line:
            count += 1
    return max(count, 1)


def summary_of(record: dict) -> dict:
    status = record.get("status", "")
    commits = record.get("commits") or []
    report = record.get("report_md") or ""
    risks = risk_count(report) if status == "ok" else 0
    bomb = bool(record.get("bomb_out_md") or record.get("bomb_in_md"))
    tags = []
    if status == "failed":
        tags.append("failed")
        pill = ("p-failed", "生成失败")
    elif bomb:
        pill = ("p-bomb", "疑似炸线")
    elif risks:
        pill = ("p-risk", f"需要留意 {risks}")
    elif status == "empty" or not commits:
        pill = ("p-empty", "无提交")
    else:
        pill = ("p-clean", "没看出问题")
    if bomb:
        tags.append("bomb")
    if risks:
        tags.append("risk")
    if status == "empty" or not commits:
        tags.append("empty")
    elif status == "ok" and not risks and not bomb:
        tags.append("clean")
    overview = next((line.strip() for line in report.split("\n") if line.strip().startswith("共 ")), "")
    if status == "failed":
        text = "复盘没生成成功，可以找朱辉在后台补跑。"
    elif not commits:
        text = "这天在 main 和 CBT2_bugfix 上没有提交。"
    else:
        text = overview or commit_parts(commits[0])[1]
    return {"commits": len(commits), "risks": risks, "bomb": bomb, "tags": tags, "pill": pill, "text": text}


def commit_parts(item: dict) -> tuple:
    parts = str(item.get("line") or "").split("|", 3)
    if len(parts) < 4:
        return "", str(item.get("line") or "")
    return parts[2], parts[3]


def neighbors(dates: list, current: str) -> tuple:
    older = next((date for date in dates if date < current), "")
    newer = next((date for date in reversed(dates) if date > current), "")
    return older, newer
