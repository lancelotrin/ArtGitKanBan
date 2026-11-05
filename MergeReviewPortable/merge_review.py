"""Portable merge review: capture -> serve -> plan -> apply -> manual replay -> finish.

Requires Python 3.10+ and Git; uses only the Python standard library.
"""
import argparse
import csv
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VERSION = "1.0.0"
HELP = """Merge Review Portable — 局域网合并冲突确认与处理

依赖：Python 3.10+、Git。无第三方 Python 包。Windows / macOS / Linux 均可用。

1. 在目标仓库自行发起 git merge，保留未解决冲突，不要切分支。
2. python merge_review.py capture --repo "目标仓库" --out "sessions"
   输出独立会话目录和 review.md 检查文档。默认读取 HEAD、MERGE_HEAD 和真实索引冲突。
   可选 --ours-label 名称 --theirs-label 名称。
   若历史是 squash / 每日同步，可显式加 --replay-base 提交号，指定已同步基线；
   不指定时使用 Git 冲突索引的 base。工具不会猜测每日同步提交。
3. python merge_review.py serve --session "会话目录" --host 0.0.0.0 --port 8767
   用该主机的局域网 IP:8767 访问；页面内填写姓名并选择，导出 CSV。
   Windows 可用 Start.ps1 -RepoPath "仓库" 或 -SessionPath "会话目录" 后台启动。
   防火墙被阻止时，管理员执行 Allow-Lan.ps1 -Port 8767；仅开放 LocalSubnet。
4. python merge_review.py plan --session "会话目录" --csv "导出的CSV"
   也可省略 --csv，读取本会话网页保存的最新选择。
   pending / 未列出的文件默认 both。plan 不改仓库。
5. python merge_review.py apply --session "会话目录"
   先检查提交、索引和工作区仍与采集时一致，再备份，执行明确选边和干净重放。
   ours / theirs 是当前分支 / 合入分支的固定版本，与分支实际叫什么无关。
   both = 先采用 ours 为基底，再重放 theirs 的增量；不是拼接两份文件。
   有重叠语义、二进制、删除/重命名等情况时保留 ours 基底，保留未解决索引，
   生成 manual-replay.md 和 handoff.txt；退出码 2 表示仍有人工整合项。
6. 将 handoff.txt 和会话目录交给本地 AI 或开发者，按项目约定完成手工重放。
   完成并审核后：python merge_review.py finish --session "会话目录" --confirm-manual
   finish 检查冲突标记和 JSON/XML 格式，只暂存人工清单内的文件。
   无变化的基底需要逐文件显式 --accept-baseline "相对路径"，禁止一键假完成。
7. python merge_review.py verify --session "会话目录"
   查看 report.md、report.json。按项目执行编译/运行测试，再自行 commit / push。
   提交前如需撤回工具处理：python merge_review.py restore --session "会话目录" --confirm-restore
   只恢复本会话路径与原冲突索引；若文件又被修改则拒绝覆盖，备份仍可人工取回。

网页每个文件只留最后一次选择、修改人、时间；未选择置顶，按文件夹/文件名排序。
网页只保存选择，不执行 Git，不提供任意仓库文件下载。姓名是自行填写，未做登录。
会话包含代码差异和备份；只分发给应有权限的同事。它不是公网托管服务。
工具不 fetch/pull，不发起 merge，不 commit/push，不运行项目自定义构建或钩子绕过。
所有写入有会话内备份；不支持 rebase/cherry-pick/octopus merge、symlink/submodule 自动解决。
应用中断时不会重复覆盖：查看 apply-state.json 和 backup/；修复仓库后重新采集新会话。

复制整个工程即可复用；每次 capture 新建目录，不携带上一轮意见。不要把 sessions/
放入共享 Git。打包命令：python merge_review.py pack --out "MergeReviewPortable.zip"。
"""


class ReviewError(Exception):
    pass


def dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Git:
    def __init__(self, repo):
        self.repo = Path(repo).resolve()

    def run(self, *args, check=True, input=None):
        result = subprocess.run(["git", "--literal-pathspecs", "-C", str(self.repo), *args],
                                input=input, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if check and result.returncode:
            raise ReviewError(result.stderr.decode("utf-8", errors="replace").strip())
        return result

    def text(self, *args):
        return self.run(*args).stdout.decode("utf-8").strip()

    def target(self, relative):
        # Resolve links as well as '..'; never write through a junction outside the repository.
        if not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise ReviewError(f"非法仓库路径：{relative}")
        target = self.repo / relative
        try:
            target.resolve().relative_to(self.repo)
        except ValueError:
            raise ReviewError(f"路径越出仓库：{relative}")
        return target

    def blob(self, oid):
        return None if not oid else self.run("cat-file", "blob", oid).stdout

    def tree(self, ref, path):
        result = self.run("ls-tree", "-z", ref, "--", path).stdout
        if not result:
            return None
        entries = result.split(b"\0")
        for entry in entries:
            if not entry:
                continue
            meta, name = entry.split(b"\t", 1)
            if name.decode("utf-8") == path:
                mode, kind, oid = meta.decode().split()
                return {"mode": mode, "oid": oid, "kind": kind}
        return None

    def stages(self, paths=None):
        args = ["ls-files", "--stage", "-z"]
        if paths is not None:
            args += ["--", *paths]
        result = {}
        for entry in self.run(*args).stdout.split(b"\0"):
            if not entry:
                continue
            meta, name = entry.split(b"\t", 1)
            mode, oid, stage = meta.decode().split()
            result.setdefault(name.decode("utf-8"), {})[stage] = {"mode": mode, "oid": oid}
        return result

    def identity(self):
        ours = self.text("rev-parse", "HEAD")
        merge_path = Path(self.text("rev-parse", "--git-path", "MERGE_HEAD"))
        if not merge_path.is_absolute():
            merge_path = self.repo / merge_path
        if not merge_path.is_file():
            raise ReviewError("仓库不在 git merge 冲突阶段：没有 MERGE_HEAD。")
        incoming = merge_path.read_text(encoding="ascii").splitlines()
        if len(incoming) != 1:
            raise ReviewError("仅支持一个合入分支的普通 merge，不支持 octopus merge。")
        return ours, incoming[0]


def worktree_state(git, path):
    target = git.target(path)
    if target.is_symlink():
        return {"kind": "symlink", "sha256": sha(os.readlink(target).encode())}
    if not target.exists():
        return {"kind": "missing"}
    if not target.is_file():
        return {"kind": "directory"}
    return {"kind": "file", "sha256": sha(target.read_bytes()), "mode": stat.S_IMODE(target.stat().st_mode)}


def category(path):
    suffix = Path(path).suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".tga", ".psd", ".webp"}:
        return "图片"
    if suffix in {".dll", ".so", ".bytes", ".bnk", ".wem", ".exe", ".zip", ".bin", ".dat"}:
        return "二进制 / 生成物"
    if suffix in {".prefab", ".unity", ".asset", ".meta", ".shadervariants"}:
        return "资源 / 导入配置"
    if suffix in {".xml", ".csv", ".json", ".yaml", ".yml", ".toml"}:
        return "配置 / 数据"
    if suffix in {".md", ".txt", ".rst"}:
        return "文档"
    return "代码 / 其它"


def branch_label(git, oid, ours=False):
    if ours:
        name = git.run("symbolic-ref", "--quiet", "--short", "HEAD", check=False)
        if name.returncode == 0:
            return name.stdout.decode("utf-8").strip()
    refs = git.text("for-each-ref", "--format=%(refname:short)", "--points-at", oid, "refs/heads").splitlines()
    return refs[0] if len(refs) == 1 else oid[:11]


def capture(repo, out, ours_label=None, theirs_label=None, replay_base=None):
    git = Git(repo)
    git.repo = Path(git.text("rev-parse", "--show-toplevel")).resolve()
    ours, theirs = git.identity()
    index = git.stages()
    conflicts = {path: stages for path, stages in index.items() if any(s != "0" for s in stages)}
    if not conflicts:
        raise ReviewError("没有未解决的索引冲突。")
    replay_oid = git.text("rev-parse", "--verify", replay_base + "^{commit}") if replay_base else None
    stamp = datetime.now().astimezone()
    session = Path(out).resolve() / (stamp.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8])
    session.mkdir(parents=True, exist_ok=False)
    commits = {}

    def history(ref, path):
        ids = git.text("log", "-3", "--format=%H", ref, "--", path).splitlines()
        result = []
        for oid in ids:
            if oid not in commits:
                values = git.text("show", "-s", "--format=%H%x1f%an%x1f%ae%x1f%cn%x1f%ce%x1f%aI%x1f%s", oid).split("\x1f")
                commits[oid] = dict(zip(["sha", "author", "email", "committer", "committer_email", "at", "subject"], values))
            result.append(commits[oid])
        return result

    files = []
    for number, path in enumerate(sorted(conflicts), 1):
        current = worktree_state(git, path)
        stages = conflicts[path]
        left, right = git.tree(ours, path), git.tree(theirs, path)
        base = git.tree(replay_oid, path) if replay_oid else stages.get("1")
        sources = {"ours": left, "theirs": right, "base": base}
        for item in [*stages.values(), *sources.values()]:
            if item and item["mode"] not in {"160000", "040000"}:
                blob_path = session / "blobs" / item["oid"]
                if not blob_path.exists():
                    blob_path.parent.mkdir(exist_ok=True)
                    blob_path.write_bytes(git.blob(item["oid"]))
        left_history, right_history = history(ours, path), history(theirs, path)
        diff = git.text("diff", "--no-ext-diff", "--no-textconv", "--unified=3", ours, theirs, "--", path)
        binary = any(item and item["mode"] == "100644" and b"\0" in (git.blob(item["oid"]) or b"") for item in (left, right))
        binary = binary or category(path) in {"图片", "二进制 / 生成物"}
        note = "both：保留当前分支为基底，重放合入分支修改；重叠内容需人工确认。"
        if left is None:
            note += " 当前分支同路径不存在，注意删除/重命名意图。"
        if right is None:
            note += " 合入分支同路径不存在，注意删除/重命名意图。"
        mapping = any((stages.get(key) or {}).get("oid") != (entry or {}).get("oid") for key, entry in [("2", left), ("3", right)])
        if mapping:
            note += " Git 索引与同路径分支对象存在差异，可能涉及重命名；both 将进入人工队列。"
        if binary:
            note += " 二进制两边均变化时需人工整合或重新生成。"
        files.append({"id": sha(path.encode())[:20], "number": number, "path": path,
                      "category": category(path), "ours": left_history[0] if left_history else None,
                      "theirs": right_history[0] if right_history else None,
                      "ours_history": left_history, "theirs_history": right_history,
                      "sources": sources, "stages": stages, "worktree": current,
                      "mapping_conflict": mapping, "binary": binary,
                      "diff": diff[:40000] + ("\n[仅显示前 40000 字符；完整对象保存在会话 blobs 中]" if len(diff) > 40000 else ""),
                      "advice": note})
    snapshot = {"version": VERSION, "id": session.name, "repo": str(git.repo), "date": stamp.date().isoformat(),
                "ours": ours, "theirs": theirs, "ours_label": ours_label or branch_label(git, ours, True),
                "theirs_label": theirs_label or branch_label(git, theirs), "replay_base": replay_oid, "files": files}
    dump(session / "snapshot.json", snapshot)
    dump(session / "capture-manifest.json", {"snapshot_sha256": sha((session / "snapshot.json").read_bytes())})
    write_review_document(session, snapshot)
    return session


def write_review_document(session, snapshot):
    def escape(value):
        return str(value).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('|', '\\|').replace('`', '&#96;').replace('\n', ' ')
    def attribution(file, side):
        commit = file[side]
        absent = '（同路径不存在）' if not file['sources'][side] else ''
        if not commit:
            return absent or '无同路径历史'
        return escape(f"{commit['sha'][:11]} · 作者 {commit['author']} · 提交人 {commit['committer']} · {commit['subject']}") + absent
    lines = ['# 合并冲突检查及提交人清单', '', f"日期：{snapshot['date']}；会话：{snapshot['id']}", '',
             f"- 仓库：{escape(snapshot['repo'])}",
             f"- 当前分支：{escape(snapshot['ours_label'])} / {snapshot['ours']}",
             f"- 合入分支：{escape(snapshot['theirs_label'])} / {snapshot['theirs']}",
             f"- 重放基线：{snapshot['replay_base'] or '每个冲突路径的 Git 索引 stage 1'}",
             f"- 冲突路径：{len(snapshot['files'])}；采集不修改仓库。", '',
             '## 选择规则', '', '未选择默认两边都要：以当前分支为基础，再重放合入分支修改。不是拼接两份文件。', '',
             '作者/提交人来自固定分支中最近触及同一路径的提交，不等于推送者或最终模块负责人。自动同步提交不代表原始开发者；最近三条历史保存在 snapshot.json 中。', '',
             '本文是静态元数据清单，业务含义、二进制内容、生成物配套与项目编译需结合目标项目继续核对。', '',
             '| 路径 | 分类 | 当前分支最近提交 | 合入分支最近提交 | 提示 |', '| --- | --- | --- | --- | --- |']
    for file in snapshot['files']:
        lines.append(f"| {escape(file['path'])} | {escape(file['category'])} | {attribution(file,'ours')} | {attribution(file,'theirs')} | {escape(file['advice'])} |")
    (session / 'review.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def load_session(session):
    session = Path(session).resolve()
    snapshot = read(session / "snapshot.json")
    manifest = read(session / "capture-manifest.json")
    if sha((session / "snapshot.json").read_bytes()) != manifest["snapshot_sha256"]:
        raise ReviewError("快照文件在采集后被修改，请重新采集。")
    return session, snapshot, Git(snapshot["repo"])


def plan_session(session, csv_path=None):
    session, snapshot, git = load_session(session)
    allowed = {f["path"] for f in snapshot["files"]}
    choices = {}
    if csv_path:
        with Path(csv_path).open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if not {"snapshot", "path", "choice"} <= set(reader.fieldnames or []):
                raise ReviewError("CSV 缺少 snapshot/path/choice 列。")
            for row in reader:
                if row["snapshot"] != snapshot["id"] or row["path"] not in allowed:
                    raise ReviewError("CSV 快照或路径不属于当前会话。")
                if row["path"] in choices:
                    raise ReviewError("CSV 含重复路径。")
                choices[row["path"]] = row
    elif (session / "reviews.sqlite3").exists():
        import server
        conn = server.database(session / "reviews.sqlite3")
        try:
            by_id = {f["id"]: f["path"] for f in snapshot["files"]}
            for row in conn.execute("SELECT file_id,reviewer,choice,comment FROM decisions WHERE snapshot=?", (snapshot["id"],)):
                if row["file_id"] not in by_id:
                    raise ReviewError("评审库包含未知文件。")
                choices[by_id[row["file_id"]]] = dict(row)
        finally:
            conn.close()
    plan = {"snapshot": snapshot["id"], "files": []}
    for file in snapshot["files"]:
        row = choices.get(file["path"], {})
        choice = row.get("choice", "pending")
        if choice in {"pending", ""}:
            choice = "both"
        if choice not in {"ours", "theirs", "both"}:
            raise ReviewError(f"未知选择 {choice}：{file['path']}")
        plan["files"].append({"path": file["path"], "choice": choice,
                              "reviewer": row.get("reviewer", ""), "comment": row.get("comment", "")})
    if (session / "apply-state.json").exists():
        raise ReviewError("该会话已开始执行，不能覆盖原执行计划。")
    dump(session / "plan.json", plan)
    return plan


def context_check(git, snapshot):
    if git.identity() != (snapshot["ours"], snapshot["theirs"]):
        raise ReviewError("HEAD / MERGE_HEAD 已变化；停止，未修改文件。请重新采集。")


def inspect_text(path, data):
    if data is None:
        return None
    if b"\0" in data:
        return None
    if re.search(rb"^(?:<{7,8}(?: |$)|={7,8}\s*$|>{7,8}(?: |$))", data, re.M):
        return "仍有冲突标记"
    try:
        if path.endswith(".json"):
            json.loads(data.decode("utf-8-sig"))
        if path.endswith(".xml"):
            ET.fromstring(data)
    except (ValueError, UnicodeError, ET.ParseError) as exc:
        return f"格式检查失败：{exc}"
    return None


def blob_data(session, entry):
    if not entry:
        return None
    content = (session / "blobs" / entry["oid"]).read_bytes()
    digest = hashlib.sha1 if len(entry["oid"]) == 40 else hashlib.sha256
    if digest(f"blob {len(content)}\0".encode() + content).hexdigest() != entry["oid"]:
        raise ReviewError("会话中的源对象被修改：" + entry["oid"])
    return content


def stage_content(git, path, entry, data):
    target = git.target(path)
    if data is None:
        if target.exists():
            if not target.is_file() or target.is_symlink():
                raise ReviewError(f"拒绝删除非普通文件：{path}")
            target.unlink()
        git.run("update-index", "--force-remove", "--", path)
    else:
        write_content(git, path, entry, data)
        git.run("add", "--", path)
        if entry:
            git.run("update-index", "--chmod=" + ("+x" if entry["mode"] == "100755" else "-x"), "--", path)


def write_content(git, path, entry, data):
    target = git.target(path)
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise ReviewError(f"路径不是普通文件：{path}")
    if data is None:
        if target.exists():
            target.unlink()
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".merge-review-", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        if entry and os.name != "nt":
            os.chmod(name, 0o755 if entry["mode"] == "100755" else 0o644)
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def replay(session, file):
    def data(side):
        return blob_data(session, file["sources"][side])
    sources = file["sources"]
    if any(e and e["mode"] not in {"100644", "100755"} for e in sources.values()):
        return None, None, "符号链接或子模块需要人工处理"
    if file["mapping_conflict"]:
        return None, None, "重命名/路径映射需要人工确认"
    ours, base, theirs = (data(x) for x in ("ours", "base", "theirs"))
    def merged_entry(preferred):
        entry = sources[preferred]
        if not entry:
            return None
        entry = dict(entry)
        if sources["ours"] and sources["base"] and sources["ours"]["mode"] != sources["base"]["mode"]:
            entry["mode"] = sources["ours"]["mode"]
        elif sources["theirs"]:
            entry["mode"] = sources["theirs"]["mode"]
        return entry
    if ours == theirs or theirs == base:
        return ours, merged_entry("ours"), None
    if ours == base:
        return theirs, merged_entry("theirs"), None
    if ours is None or theirs is None:
        return None, None, "删除与修改同时发生，需要人工重放"
    if file["binary"]:
        return None, None, "二进制两边均修改，需要人工整合/重新生成"
    with tempfile.TemporaryDirectory(dir=session) as temp:
        paths = [Path(temp) / str(i) for i in range(3)]
        for p, content in zip(paths, (ours, base or b"", theirs)):
            p.write_bytes(content)
        result = subprocess.run(["git", "merge-file", "--stdout", *map(str, paths)], capture_output=True)
    if result.returncode != 0:
        candidate = session / "manual" / (file["id"] + ".candidate")
        candidate.parent.mkdir(exist_ok=True)
        candidate.write_bytes(result.stdout)
        return None, None, "文本修改重叠，需要在当前分支基底上人工重放"
    problem = inspect_text(file["path"], result.stdout)
    if problem:
        return None, None, problem
    return result.stdout, merged_entry("ours"), None


def apply_session(session):
    session, snapshot, git = load_session(session)
    plan = read(session / "plan.json")
    if plan.get("snapshot") != snapshot["id"]:
        raise ReviewError("计划与快照不一致。")
    items = {f["path"]: f for f in snapshot["files"]}
    paths = [f["path"] for f in plan["files"]]
    if len(paths) != len(set(paths)) or set(paths) != set(items):
        raise ReviewError("执行计划路径不完整或已被修改。")
    if any(f["choice"] not in {"ours", "theirs", "both"} for f in plan["files"]):
        raise ReviewError("执行计划含未知选择。")
    if (session / "apply-state.json").exists():
        raise ReviewError("本会话已执行或中断；不重复覆盖，请查看 apply-state.json。")
    context_check(git, snapshot)
    index = git.stages(paths)
    for path in paths:
        file = items[path]
        if index.get(path) != file["stages"] or worktree_state(git, path) != file["worktree"]:
            raise ReviewError(f"采集后索引/工作区已变化，未写入：{path}")
        if file["worktree"]["kind"] not in {"file", "missing"}:
            raise ReviewError(f"非普通文件不支持自动写入：{path}")
        for entry in file["sources"].values():
            if entry and entry["mode"] in {"100644", "100755"}:
                blob_data(session, entry)
    backup = session / "backup"
    backup.mkdir()
    for file in items.values():
        target = git.target(file["path"])
        if target.is_file():
            shutil.copy2(target, backup / (file["id"] + ".worktree"))
    dump(backup / "index-stages.json", index)
    dump(backup / "plan.json", plan)
    state = {"snapshot": snapshot["id"], "status": "applying", "files": []}
    dump(session / "apply-state.json", state)
    try:
        for row in plan["files"]:
            file, choice = items[row["path"]], row["choice"]
            context_check(git, snapshot)
            if git.stages([file["path"]]).get(file["path"]) != file["stages"] or worktree_state(git, file["path"]) != file["worktree"]:
                raise ReviewError("执行期间文件被其它程序修改，已停止：" + file["path"])
            entry = file["sources"].get(choice)
            problem = None
            if choice == "both":
                content, entry, problem = replay(session, file)
            elif entry and entry["mode"] not in {"100644", "100755"}:
                problem = "符号链接/子模块需要人工处理"
            else:
                content = blob_data(session, entry)
                problem = inspect_text(file["path"], content)
            if problem:
                # Keep ours as requested, but do not mark unperformed replay as resolved.
                baseline = file["sources"]["ours"]
                if not baseline or baseline["mode"] in {"100644", "100755"}:
                    data = blob_data(session, baseline)
                    write_content(git, file["path"], baseline, data)
                result = {"path": file["path"], "choice": choice, "status": "manual", "reason": problem,
                          "baseline": worktree_state(git, file["path"])}
            else:
                stage_content(git, file["path"], entry, content)
                result = {"path": file["path"], "choice": choice, "status": "resolved", "reason": "明确选边或干净重放",
                          "final": worktree_state(git, file["path"]), "index": git.stages([file["path"]]).get(file["path"], {})}
            state["files"].append(result)
            dump(session / "apply-state.json", state)
        state["status"] = "manual_required" if any(f["status"] == "manual" for f in state["files"]) else "applied"
    except Exception as exc:
        state["status"] = "interrupted"
        state["error"] = str(exc)
        dump(session / "apply-state.json", state)
        raise
    dump(session / "apply-state.json", state)
    handoff(session, snapshot, state)
    verify_session(session)
    return state


def handoff(session, snapshot, state):
    manual = [f for f in state["files"] if f["status"] == "manual"]
    lines = ["# 人工重放清单", "", f"仓库：`{snapshot['repo']}`", "",
             "以下文件已保留当前分支为基底，但未标记解决。读取项目 AGENTS.md 后，在该基底上重放合入分支修改。", "",
             "| 路径 | 原因 |", "| --- | --- |"]
    lines += [f"| `{r['path'].replace('|', chr(92)+'|')}` | {r['reason']} |" for r in manual]
    (session / "manual-replay.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    prompt = (f"请在仓库 {snapshot['repo']} 完成会话 {session} 的人工冲突重放。\n"
              f"固定当前分支 {snapshot['ours']}，合入分支 {snapshot['theirs']}，重放基线 {snapshot['replay_base'] or '各路径索引 stage 1'}。\n"
              "先读仓库 AGENTS.md 与项目约定，再读 snapshot.json、plan.json、apply-state.json、manual-replay.md。\n"
              "明确选边且已解决的路径不要重新选边；both/pending 均是以当前分支为基底，再重放合入分支有效修改。\n"
              "重命名须按正确同路径源对象核对，不能直接采用跨资源配错的 stage 2/3；二进制需理解修改或重新生成，不能拼接。\n"
              "先保留当前分支新增行为与优化，不复活已被有意删除的对象；已包含的修改不重复应用。\n"
              "有业务歧义时列出具体差异并询问；不可把未实施的重放标记完成。项目编译/运行和生成物配套检查后给出限制。\n"
              "仅处理本会话路径，不提交、不推送。确认人工清单完成后执行 finish --confirm-manual；无变化基底需逐路径解释并显式接受。\n")
    prompt += f'完成确认命令：python "{ROOT / "merge_review.py"}" finish --session "{session}" --confirm-manual\n'
    (session / "handoff.txt").write_text(prompt, encoding="utf-8")


def finish_session(session, confirmed=False, accept_baseline=()):
    session, snapshot, git = load_session(session)
    state = read(session / "apply-state.json")
    if state["status"] == "interrupted":
        raise ReviewError("执行中断状态不能一键完成；请核对备份并重新采集。")
    manual = [f for f in state["files"] if f["status"] == "manual"]
    if not confirmed and manual:
        raise ReviewError("需先人工重放并审核，再传 --confirm-manual。")
    context_check(git, snapshot)
    for file in state["files"]:
        if file["status"] == "resolved" and (git.stages([file["path"]]).get(file["path"], {}) != file["index"] or worktree_state(git, file["path"]) != file["final"]):
            raise ReviewError("已解决路径又被修改，先核对再 finish：" + file["path"])
    accepted = set(accept_baseline)
    if not accepted <= {f["path"] for f in manual}:
        raise ReviewError("--accept-baseline 包含不在人工清单的路径。")
    # Preflight all manual paths before staging any of them.
    for file in manual:
        path = file["path"]
        target = git.target(path)
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise ReviewError("符号链接/子模块请在 Git 工具中人工解决并重新采集核验。")
        current = worktree_state(git, path)
        if current == file["baseline"] and path not in accepted:
            raise ReviewError(f"仍是未重放的原基底，请实施修改或逐路径 --accept-baseline：{path}")
        problem = inspect_text(path, target.read_bytes() if target.exists() else None)
        if problem:
            raise ReviewError(f"{path}: {problem}")
    for file in manual:
        path = file["path"]
        target = git.target(path)
        if target.exists():
            git.run("add", "--", path)
        else:
            git.run("update-index", "--force-remove", "--", path)
        file.update(status="resolved", reason="人工审核接受基底" if path in accepted else "人工重放后确认",
                    final=worktree_state(git, path), index=git.stages([path]).get(path, {}))
    state["status"] = "applied"
    dump(session / "apply-state.json", state)
    handoff(session, snapshot, state)
    return verify_session(session)


def verify_session(session):
    session, snapshot, git = load_session(session)
    state = read(session / "apply-state.json")
    index = git.stages()
    problems = []
    try:
        context_check(git, snapshot)
    except ReviewError as exc:
        problems.append({"path": "(repository)", "problem": str(exc)})
    for file in state["files"]:
        path = file["path"]
        if file["status"] != "resolved":
            problems.append({"path": path, "problem": "已恢复原冲突" if file["status"] == "restored" else "尚待人工重放"})
            continue
        if index.get(path, {}) != file["index"] or worktree_state(git, path) != file["final"]:
            problems.append({"path": path, "problem": "解决后文件或索引再次变化"})
        target = git.target(path)
        problem = inspect_text(path, target.read_bytes() if target.is_file() and not target.is_symlink() else None)
        if problem:
            problems.append({"path": path, "problem": problem})
    unresolved = [p for p, stages in index.items() if any(s != "0" for s in stages)]
    report = {"snapshot": snapshot["id"], "repo": str(git.repo), "status": state["status"],
              "total": len(snapshot["files"]), "processed": len(state["files"]),
              "resolved": sum(f["status"] == "resolved" for f in state["files"]),
              "unresolved_in_repo": unresolved, "problems": problems, "files": state["files"],
              "project_validation": "未自动执行项目编译/运行；请按目标仓库约定验证", "committed_by_tool": False}
    dump(session / "report.json", report)
    lines = ["# 合并冲突处理报告", "", f"会话：`{snapshot['id']}`", "",
             f"处理 {report['processed']} / {report['total']}；已解决 {report['resolved']}；仓库未解决 {len(unresolved)}。", "",
             "当前分支与合入分支为固定快照；both 以当前分支为基础重放合入分支增量。", "",
             "本报告是静态文件/索引检查，不能代替项目编译、资源导入和运行验收。工具没有提交或推送。", "",
             "## 待处理项", ""]
    lines += [f"- `{p['path']}`：{p['problem']}" for p in problems] or ["无本工具发现的待处理项。"]
    if state["status"] == "interrupted":
        lines += ["", "执行曾中断，需检查 apply-state.json 和 backup/，不能认定已完整处理。"]
    (session / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def restore_session(session, confirmed=False):
    if not confirmed:
        raise ReviewError("恢复会覆盖本会话处理结果；确认后传 --confirm-restore。")
    session, snapshot, git = load_session(session)
    state = read(session / "apply-state.json")
    if state["status"] not in {"applied", "manual_required"}:
        raise ReviewError("仅支持恢复完整执行过且尚未提交的会话；中断状态请人工核对备份。")
    context_check(git, snapshot)
    index = git.stages([f["path"] for f in snapshot["files"]])
    results = {f["path"]: f for f in state["files"]}
    for original in snapshot["files"]:
        row = results[original["path"]]
        expected = row["final"] if row["status"] == "resolved" else row["baseline"]
        expected_index = row["index"] if row["status"] == "resolved" else original["stages"]
        if worktree_state(git, original["path"]) != expected or index.get(original["path"], {}) != expected_index:
            raise ReviewError("存在后续修改，恢复拒绝覆盖：" + original["path"])
        saved = session / "backup" / (original["id"] + ".worktree")
        if original["worktree"]["kind"] == "file" and (not saved.is_file() or sha(saved.read_bytes()) != original["worktree"]["sha256"]):
            raise ReviewError("备份内容缺失或被修改：" + original["path"])
    for original in snapshot["files"]:
        saved = session / "backup" / (original["id"] + ".worktree")
        data = saved.read_bytes() if original["worktree"]["kind"] == "file" else None
        entry = original["sources"]["ours"]
        write_content(git, original["path"], entry, data)
        if data is not None and os.name != "nt":
            os.chmod(git.target(original["path"]), original["worktree"]["mode"])
    oid_length = len(snapshot["ours"])
    records = []
    for original in snapshot["files"]:
        path = original["path"]
        records.append(f"0 {'0' * oid_length}\t{path}\0")
        for stage, entry in sorted(original["stages"].items()):
            records.append(f"{entry['mode']} {entry['oid']} {stage}\t{path}\0")
    git.run("update-index", "-z", "--index-info", input="".join(records).encode("utf-8"))
    state["status"] = "restored"
    for row in state["files"]:
        row.update(status="restored", reason="恢复采集时工作区与原冲突索引")
    dump(session / "apply-state.json", state)
    verify_session(session)
    return state


def pack(out):
    names = ["merge_review.py", "server.py", "app.js", "index.html", "style.css", "Start.ps1",
             "Stop.ps1", "Allow-Lan.ps1", "test_server.py", "test_workflow.py", ".gitignore"]
    out = Path(out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in names:
            archive.write(ROOT / name, "MergeReviewPortable/" + name)
        archive.writestr("MergeReviewPortable/README.md", "# Merge Review Portable\n\n```text\n" + HELP + "\n```\n")
    return out


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter, epilog=HELP)
    parser.add_argument("--version", action="version", version=VERSION)
    commands = parser.add_subparsers(dest="command", required=True)
    cap = commands.add_parser("capture", help="读取当前 merge 冲突，新建独立会话")
    cap.add_argument("--repo", required=True)
    cap.add_argument("--out", default=str(ROOT / "sessions"))
    cap.add_argument("--ours-label")
    cap.add_argument("--theirs-label")
    cap.add_argument("--replay-base")
    cap.add_argument("--json", action="store_true")
    for name in ("serve", "plan", "apply", "finish", "verify", "restore"):
        sub = commands.add_parser(name)
        sub.add_argument("--session", required=True)
        if name == "serve":
            sub.add_argument("--host", default="0.0.0.0")
            sub.add_argument("--port", type=int, default=8767)
        if name == "plan":
            sub.add_argument("--csv")
        if name == "finish":
            sub.add_argument("--confirm-manual", action="store_true")
            sub.add_argument("--accept-baseline", action="append", default=[])
        if name == "restore":
            sub.add_argument("--confirm-restore", action="store_true")
    pkg = commands.add_parser("pack")
    pkg.add_argument("--out", default=str(ROOT / "dist/MergeReviewPortable.zip"))
    args = parser.parse_args(argv)
    try:
        if args.command == "capture":
            path = capture(args.repo, args.out, args.ours_label, args.theirs_label, args.replay_base)
            print(json.dumps({"session": str(path)}, ensure_ascii=False) if args.json else str(path))
        elif args.command == "serve":
            import server
            session, snapshot, _ = load_session(args.session)
            server.serve_session(session, snapshot, args.host, args.port)
        elif args.command == "plan":
            plan = plan_session(args.session, args.csv)
            print(f"Plan: {len(plan['files'])} files -> {Path(args.session).resolve() / 'plan.json'}")
        elif args.command == "apply":
            result = apply_session(args.session)
            print(json.dumps({"status": result["status"], "session": str(Path(args.session).resolve())}, ensure_ascii=False))
            return 2 if result["status"] == "manual_required" else 0
        elif args.command == "finish":
            result = finish_session(args.session, args.confirm_manual, args.accept_baseline)
            print(json.dumps({"resolved": result["resolved"], "problems": result["problems"]}, ensure_ascii=False))
            return 2 if result["problems"] or result["unresolved_in_repo"] else 0
        elif args.command == "verify":
            result = verify_session(args.session)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 2 if result["problems"] or result["unresolved_in_repo"] or result["status"] != "applied" else 0
        elif args.command == "restore":
            result = restore_session(args.session, args.confirm_restore)
            print(result["status"])
        elif args.command == "pack":
            print(pack(args.out))
    except (ReviewError, OSError, ValueError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
