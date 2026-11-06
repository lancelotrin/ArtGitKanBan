"""大豆回归测试：用真实路由和 GLM 跑历史翻车问题，不发飞书消息。

用法：在项目根目录 python -m bot.regress
"""
import datetime
import inspect
import json
import os
import shutil
import sys
import tempfile
import time

from bot import echo_bot as bot
from common.paths import DATA_DIR

CHAT_ID = "oc_regress"
SENT = []

# (提问, 回复必须包含的词, 回复不能包含的词)；同一组内按顺序发，共享上下文
CASES = [
    [("能帮我建模吗", ["不能"], ["Assets/"])],
    [("以后能把我列在P0的任务发到美术组吗", ["不能"], ["Assets/", "已确认"])],
    [("会不会引用内部的文档", ["不会"], []),
     ("那岂不是很危险", ["分享"], ["没拿到", "图片", "安全可靠"])],
    [("帮我写周报", ["本周", "填写"], ["Assets/"])],
    [("帮我查一下工资文档", ["不能"], [])],
    [("现在几点", ["北京时间"], ["没有记录"])],
    [("今天星期几", ["星期"], ["没有记录"])],
    [("明天是几号", ["月"], ["没有记录", "不知道"])],
    [("项目管理员是谁", ["没有", "记录"], ["张三", "李四"])],
    [("主美是谁", ["没有"], ["张三", "李四"])],
    [("谁是角色组长", ["没有"], ["张三", "李四"])],
    [("美术群叫什么名字", [], ["张三", "李四", "http"])],
    [("项目地址是多少", ["10.234.80.148/pie/onepiece.git"], ["不能提供"]),
     ("为什么", [], ["没有上下文", "不清楚"])],
    [("贴图要不要压成ASTC 6x6", [], []),
     ("为什么", ["ASTC"], ["没有上下文", "不清楚"])],
    [("这是谁", ["图"], [])],
    [("角色模型在哪个路径", ["Assets/Dependences/Character"], ["已确认", "优先采用"])],
    [("艾斯在项目中有几个预制体", ["Role_Ace_CS.prefab"], ["Luffy"])],
    [("项目中有几个路飞预制体", ["Role_Luffy_CS.prefab"], []),
     ("哪4个", ["Role_Luffy"], ["优先采用"])],
    [("Assets/Bundle/Character/Role/Role_Enel/Role_Enelamaru_lod1.prefab这个预制体有多少面", ["三角面", "Role_Enelamaru_hair"], ["72", "Role_Ace"]),
     ("Assets/Bundle/Character/Role/Role_Ace/Role_Ace_CS.prefab 这个呢", ["三角面", "Role_Ace_face"], ["Unity", "没有直接证据", "72"])],
    [("项目中有多少个角色预制体", ["72", "Ace"], ["这一段"])],
    [("项目里有几个怪物预制体", ["105"], ["这一段"])],
    [("项目里有几个预制体", ["要统计哪类"], ["这一段"])],
    [("项目中有多少个Shader", ["237", "包含它下面"], ["127", "prefab", "Shader/Role："]),
     ("列出来", [".shader"], ["prefab", "这一段"])],
    [("Assets/Dependences/Effect/Common/Texture/Tile/T_Tile072.tga 这张纹理被谁引用使用了",
      ["Role_Enelamaru_lod1.prefab", "材质"],
      ["Find References", "没有直接证据", "AssetDatabase", "例如", "按目录"]),
     ("列出来", [".mat", "T_Tile072"], ["这一段", "Find References"])],
    [("Assets/Dependences/Effect/Common/Texture/Daoguang/T_Daoguang111.png 这张纹理在哪里引用了",
      ["Monster_Mihawk_Pugong_elite02.mat", "Monster_Mihawk_Pugong_elite.prefab", "材质", "预制体"],
      ["超过 5MB", "5MB", "例如", "按目录"])],
    [("Assets/Dependences/Effect/Common/Texture/Tile/T_Tile054.png 这张图在哪里引用了",
      ["材质"], ["origin/main", "提交记录", "Find References", "例如", "按目录"])],
    [("Assets/Dependences/Effect/Common/Material/Buff/fx_buff_loop_jiafang001.mat 这张材质在哪里引用了",
      ["fx_buff_jiafang.prefab", "材质 guid", "预制体"], ["origin/main", "提交记录", "例如", "按目录"])],
    [("Assets/Dependences/Character/Model/Role/Role_Ace_cs/Bandpose/Role_Ace_bandpose.fbx 这个模型在哪里引用了",
      ["直接引用", "Role_Ace_CS.prefab", "Mesh"], ["origin/main", "提交记录"])],
    [("粒子系统里的自定义数据，具体哪个数字对应哪个选项", ["Shader"], ["裁剪效果", "局部显示"]),
     ("UVEffect_Manual_UIClip", ["Custom1.z", "溶解", "Custom2.z", "边缘宽度", "Custom2.w", "边缘亮度"], ["裁剪效果", "局部显示", "UV 偏移的 Shader"]),
     ("不对", ["Custom1.z", "溶解", "边缘宽度"], ["正确的是什么", "裁剪效果"]),
     ("你觉得这回答对吗？", ["Custom1.z", "溶解"], ["裁剪效果", "局部显示"])],
    [("这个shader有几个pass", [], ["237", "3个pass", "2个pass", "4个pass"])],
    [("给我生成一个原神的UI界面", ["不能"], ["没搜到"])],
    [("贴图要不要压成ASTC 6x6", ["ASTC"], ["Assets/Dependences", "已确认"])],
    [("Unity里Shader报错Undeclared identifier 一般是什么原因", ["变量"], ["已确认"])],
    [("总结一下市面上付费top10的游戏成功原因和真实数据", ["http", "2026", "不是固定稿"], ["商店简介", "成功原因只保留", "凭借独特", "电竞生态"])],
    [("角色制作文档", ["Assets/Dependences/Character"], ["search:docs:read"])],
    [("米哈游的角色渲染分享", ["米哈游"], ["没有记录", "Assets/", "搜索结果中"])],
    [("原神的卡通渲染是怎么做的", ["渲染"], ["没有记录", "搜索结果中"])],
    [("最近有什么新游戏", [], ["没有找到", "节假日", "日历", "没有记录", "搜索结果中"])],
    [("有什么新的游戏实机首爆", ["bilibili.com"], ["2025-", "2025年", "没有记录", "限流"])],
    [("黑神话悟空用的什么引擎", [], ["没有记录", "没有找到", "虚幻引擎4", "UE4"])],
    [("鸣潮的角色描边是怎么做的", ["描边"], ["没有记录", "没有找到"])],
    [("Assets/Bundle/Sfx/Role_Luffy/Role_Luffy_attack_01.prefab 有几个粒子系统", ["14"], [])],
]


MENTION_CASES = [
    # (飞书原始文本, mentions, 期望回复包含)
    ("@_user_1 主程是 @_user_2", [{"key": "@_user_1", "name": "大豆"}, {"key": "@_user_2", "name": "刘祺卿"}], "刘祺卿"),
    ("@_user_1 主程是谁", [{"key": "@_user_1", "name": "大豆"}], "刘祺卿"),
]


REACTION_CASES = [
    ("谢谢大豆", "FINGERHEART"),
    ("辛苦了", "FINGERHEART"),
    ("大豆真聪明", "BLUSH"),
    ("不对，是在 Assets/Dependences/Character 下", "Get"),
    ("主程是刘祺卿", "Get"),
    ("主美是不是太忙了", "FINGERHEART"),
    ("查 SceneModelPreviewBrowser.cs 的提交记录", "OnIt"),
    ("Role_Luffy_attack_01.prefab 有几个粒子系统", "OnIt"),
    ("角色模型在哪个路径", "OnIt"),
    ("能帮我建模吗", "FINGERHEART"),
    ("主美是谁", "FINGERHEART"),
]


def bomb_fake_ok() -> bool:
    """假 git：合并 M 的父 P1（本地）、P2（远端）。P2 侧改了 a.prefab 和 b.cs，M 保留了 P1 的 a.prefab。"""
    blobs = {("M", "a.prefab"): "x1", ("P1", "a.prefab"): "x1", ("P2", "a.prefab"): "y2", ("T", "a.prefab"): "x1",
             ("M", "b.cs"): "b2", ("P1", "b.cs"): "b1", ("P2", "b.cs"): "b2", ("T", "b.cs"): "b2"}

    def git(args):
        if args[0] == "log" and "--merges" in args:
            return True, "M|P1 P2|maziyang|11-03 12:18|2026-11-03T12:18:00+08:00|Merge branch 'main'"
        if args[0] == "show":
            return True, "@@M\nb.cs\nFolderHashCache.txt\n@@M\na.prefab\n"
        if args[0] == "log" and "P2..P1" in args:
            return True, "FolderHashCache.txt\n"
        if args[0] == "log" and "P1..P2" in args:
            if "--" in args:
                return True, "c79dc92|wangqingyang|11-02 16:46|图集优化 https://x"
            return True, "a.prefab\nb.cs\n"
        if args[0] == "log":
            return True, ""
        if args[0] == "rev-parse":
            rev, path = args[-1].split(":", 1)
            return True, blobs.get(("T" if rev == "origin/main" else rev, path), "")
        return False, ""

    found, count = bot.bomb.scan_merges(git, "origin/main", "2026-11-01")
    report = bot.bomb.format_report("main", found, [])
    sample = [
        {"merge": "abc", "count": 3, "paths": ["BattleLibrary/output/a.dll", "Assets/DataTable/X.xml", "Foo/a.cs"],
         "files": [{"path": "BattleLibrary/output/a.dll", "state": "还丢着", "commits": []},
                   {"path": "Assets/DataTable/X.xml", "state": "还丢着", "commits": []},
                   {"path": "Foo/a.cs", "state": "还丢着", "commits": []}]},
        {"merge": "def", "count": 2, "paths": ["BattleLibrary/output/a.dll", "Assets/DataTable/X.xml"],
         "files": [{"path": "BattleLibrary/output/a.dll", "state": "还丢着", "commits": []},
                   {"path": "Assets/DataTable/X.xml", "state": "还丢着", "commits": []}]},
    ]
    kept, dropped = bot.bomb.filter_merges_for_alert(sample, list(bot.bomb.DEFAULT_IGNORE_PATHS))
    card = bot.bomb.build_card("疑似炸线", "", [("main", found + [dict(found[0], author="zhangsan")], [])], "note")
    tags = [element["tag"] for element in card["body"]["elements"]]
    card_ok = (card["schema"] == "2.0" and tags.count("table") == 2 and "**【main】**" in card["body"]["elements"][0]["content"]
               and bot.bomb.merge_sides("Merge branch 'main' of http://x into main", "main") == ("本地 main", "远端 main")
               and bot.bomb.merge_sides("Merge branch 'shader' into main", "main") == ("本地 main", "shader"))
    people = {"zhangsan": {"name": "张三", "open_id": "ou_abc"}, "lisi": {"name": "李四", "open_id": ""}}
    named = bot.bomb.build_card("疑似炸线", "", [("main", [dict(found[0], author="ZhangSan"), dict(found[0], author="lisi")], [])],
                                "", people)
    texts = [element.get("content", "") for element in named["body"]["elements"]]
    card_ok = card_ok and "<at id=ou_abc></at> 合并 1 次" in texts[1] and texts[3].startswith("**李四**")
    verdicts = bot.bomb.parse_judgement(
        '好的\n[{"path": "a.prefab", "verdict": "像是误丢", "reason": "对方新加的节点被冲掉"},'
        ' {"path": "x/b.cs", "verdict": "瞎写", "reason": ""}]', ["a.prefab", "Foo/b.cs"])
    judged = dict(found[0], files=[dict(found[0]["files"][0], judge=verdicts.get("a.prefab"))])
    judge_card = bot.bomb.build_card("t", "", [("main", [judged], [])])
    judge_cells = [row["judge"] for element in judge_card["body"]["elements"] for row in element.get("rows", [])]
    card_ok = (card_ok and verdicts == {"a.prefab": "像是误丢：对方新加的节点被冲掉", "Foo/b.cs": "看不出"}
               and judge_cells == ["像是误丢：对方新加的节点被冲掉"]
               and "Codex 判断：像是误丢" in bot.bomb.format_report("main", [judged], [])
               and bot.bomb.parse_judgement("没法判断", ["a.prefab"]) == {}
               and bot.bomb_diff_allowed(["diff", "--no-color", "-U2", "abc1234", "def5678", "--", "Client/a.cs"])
               and not bot.bomb_diff_allowed(["diff", "--no-color", "-U2", "abc1234", "--output=x", "--", "a"])
               and not bot.bomb_diff_allowed(["diff", "--no-color", "-U2", "abc1234", "def5678", "--", "--no-index"])
               and not bot.bomb_diff_allowed(["diff", "--no-color", "-U2", "abc1234", "def5678", "--", "../x"])
               and bot.bomb_board.is_open({"status": "还丢着"}) and not bot.bomb_board.is_open({"status": "还丢着", "resolved": True})
               and not bot.bomb_board.is_open({"status": "已补回"})
               and bot.bomb_remind_due({}, datetime.datetime(2026, 11, 6, 10, tzinfo=bot.BEIJING_TZ))
               and not bot.bomb_remind_due({}, datetime.datetime(2026, 11, 6, 23, tzinfo=bot.BEIJING_TZ))
               and bot.bomb_remind_due({"last_remind_at": "2026-11-06T09:50:00+08:00"},
                                       datetime.datetime(2026, 11, 6, 10, tzinfo=bot.BEIJING_TZ)))
    return (card_ok and count == 1 and len(found) == 1 and [f["path"] for f in found[0]["files"]] == ["a.prefab"]
            and found[0]["files"][0]["state"] == "还丢着" and "wangqingyang" in report and "https" not in report
            and found[0]["at"] == "2026-11-03T12:18:00+08:00"
            and found[0].get("paths") == ["a.prefab"]
            and bot.bomb.path_ignored("Client/Assets/DataTable/A.xml", ["Assets/DataTable/"])
            and bot.bomb.normalize_ignore_path("Assets/DataTable") == "Assets/DataTable/"
            and len(kept) == 1 and kept[0]["merge"] == "abc" and kept[0]["count"] == 1
            and [f["path"] for f in kept[0]["files"]] == ["Foo/a.cs"] and dropped == 4)


def bomb_judge_cache_ok() -> bool:
    """Codex 判断过的存下来：再提醒、重扫同一合并、缓存被清掉都不再调 Codex。"""
    root = tempfile.mkdtemp()
    saved = (bot.bomb_board.HISTORY_PATH, bot.BOMB_PATH, bot.USE_CODEX, bot.judge_one_merge)
    calls = []
    try:
        bot.bomb_board.HISTORY_PATH = os.path.join(root, "h.json")
        bot.BOMB_PATH = os.path.join(root, "s.json")
        bot.save_bomb_state({})
        bot.USE_CODEX = True
        bot.judge_one_merge = lambda branch, item: calls.append(item["merge"]) or {"a/x.prefab": "像是误丢：节点被冲掉"}
        item = lambda: {"merge": "abc1234", "author": "ge", "at": "2026-11-06T10:00", "count": 1,
                        "files": [{"path": "a/x.prefab", "state": "还丢着", "commits": []}]}
        bot.bomb_board.record_scan([("main", [item()], [])], [])
        bot.judge_bomb_results([("main", [item()], [])])
        pending = bot.bomb_board.open_results()
        bot.judge_bomb_results(pending)
        bot.save_bomb_state({})  # 缓存丢了，看板记录里还有
        again = bot.bomb_board.open_results()
        bot.judge_bomb_results(again)
        return (calls == ["abc1234"] and pending[0][1][0]["files"][0]["judge"] == "像是误丢：节点被冲掉"
                and again[0][1][0]["files"][0]["judge"] == "像是误丢：节点被冲掉")
    finally:
        bot.bomb_board.HISTORY_PATH, bot.BOMB_PATH, bot.USE_CODEX, bot.judge_one_merge = saved
        shutil.rmtree(root, ignore_errors=True)


def run_reactions() -> int:
    failed = 0
    for text, expect in REACTION_CASES:
        got = bot.pick_reaction(text)
        ok = got == expect
        failed += 0 if ok else 1
        print(f"[{'PASS' if ok else 'FAIL'}] 表情 {text} -> {got}" + ("" if ok else f"（应为 {expect}）"))
    return failed


def fake_reply(message_id: str, text: str, markdown: bool = False, route: str = "") -> None:
    del message_id, markdown, route
    bot.remember_turn("assistant", text)
    SENT.append(text)


def run_question(index: int, question: str) -> str:
    SENT.clear()
    message = {
        "message_id": f"om_regress_{index}",
        "chat_id": CHAT_ID,
        "chat_type": "p2p",
        "message_type": "text",
        "content": json.dumps({"text": question}, ensure_ascii=False),
    }
    bot.handle(message["message_id"], message, [])
    return "\n".join(SENT)


REPORT_PATH = os.path.join(DATA_DIR, "regress_out.txt")
REPORT = []


def print(text: str = "") -> None:  # noqa: A001
    REPORT.append(text)
    sys.stdout.buffer.write((text + "\n").encode("utf-8", "replace"))


def main() -> int:
    try:
        return run_all()
    finally:
        with open(REPORT_PATH, "w", encoding="utf-8") as file:
            file.write("\n".join(REPORT) + "\n")


def run_mentions() -> int:
    failed = 0
    for index, (raw, mentions, expect) in enumerate(MENTION_CASES, 1):
        SENT.clear()
        message = {"message_id": f"om_mention_{index}", "chat_id": "oc_regress_mention", "chat_type": "group",
                   "message_type": "text", "mentions": mentions,
                   "content": json.dumps({"text": raw}, ensure_ascii=False)}
        bot.handle(message["message_id"], message, [])
        answer = "\n".join(SENT)
        ok = expect in answer
        failed += 0 if ok else 1
        print(f"[{'PASS' if ok else 'FAIL'}] {raw}\n       -> {' '.join(answer.split())[:110]}")
    return failed


def run_miss_log() -> tuple:
    bot.RECORD_MISSES = True
    bot.MISSES_PATH = os.path.join(tempfile.mkdtemp(), "misses.jsonl")
    chat = "oc_miss"
    bot.CURRENT_CHAT.id = chat
    failed = 0
    checks = [
        (bot.is_reask("Assets/A.png 在哪里引用了", "Assets/A.png 被谁引用了"), True, "同一路径换说法"),
        (bot.is_reask("Assets/A.png 在哪里引用了", "列出来"), False, "列出来"),
        (bot.is_reask("Assets/A.png 这个预制体有多少面", "Assets/B.prefab 这个呢"), False, "换路径跟问"),
    ]
    for got, expect, name in checks:
        ok = got is expect
        failed += 0 if ok else 1
        print(f"[{'PASS' if ok else 'FAIL'}] 失败记录 {name} -> {got}")
    bot.LAST_TURN[chat] = {
        "time": time.time(), "question": "Assets/A.png 在哪里引用了", "route": "git",
        "preview": "最近 1 条提交", "message_id": "om_1",
    }
    bot.mark_miss(chat, "撤回", "om_1")
    bot.LAST_TURN[chat] = {
        "time": time.time(), "question": "Assets/A.png 在哪里引用了", "route": "git",
        "preview": "最近 1 条提交", "message_id": "om_2",
    }
    bot.mark_miss(chat, "又问了一遍")
    bot.mark_miss(chat, "不对")
    with open(bot.MISSES_PATH, encoding="utf-8") as file:
        lines = [line for line in file.read().splitlines() if line.strip()]
    ok = len(lines) == 2 and "撤回" in lines[0] and "git" in lines[0] and "又问了一遍" in lines[1]
    failed += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] 失败记录写入 {len(lines)} 条")
    bot.RECORD_MISSES = False
    return failed, len(checks) + 1


def run_memory() -> int:
    failed = 0
    bot.LAST_GIT_BRANCH.pop("oc_git", None)
    branch, error = bot.select_git_branch("oc_git", "切到 CBT2_bugfix")
    ok = branch == "CBT2_bugfix" and not error and bot.LAST_GIT_BRANCH.get("oc_git") == "CBT2_bugfix"
    failed += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] 切到 CBT2_bugfix")
    branch, error = bot.select_git_branch("oc_git", "查 Foo.cs 的提交记录")
    ok = branch == "CBT2_bugfix" and not error
    failed += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] 没写分支就沿用上次")
    branch, error = bot.select_git_branch("oc_git", "切回 main")
    ok = branch == "main" and not error
    failed += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] 切回 main")
    bare = bot.parse_git_branch("Assets/Main/Foo.cs 提交记录")
    ok = bare == ""
    failed += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] 路径里的 Main 不当成分支")
    denied, message = bot.run_git_readonly(r"D:\not-a-repo", ["fetch", "origin", "../evil"])
    ok = denied is False and "合法分支名" in message
    failed += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] 非法分支名不能 fetch")
    bot.LAST_GIT_BRANCH.pop("oc_git", None)
    post = {
        "message_type": "post",
        "content": json.dumps({"zh_cn": {"content": [[{"tag": "md", "text": "名称拼写 UVEffect_Manual_UIClip"}]]}}, ensure_ascii=False),
    }
    got = bot.extract_text(post)
    ok = "UVEffect_Manual_UIClip" in got
    failed += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] 回复正文能读出 markdown")
    old_path = bot.HISTORY_PATH
    old_persist = bot.PERSIST_HISTORY
    bot.PERSIST_HISTORY = True
    bot.HISTORY_PATH = os.path.join(tempfile.mkdtemp(), "chat_history.json")
    bot.CURRENT_CHAT.id = "oc_mem"
    bot.CHAT_HISTORY.pop("oc_mem", None)
    bot.remember_turn("user", "粒子自定义数据")
    bot.remember_turn("assistant", "Custom1.z 溶解")
    bot.CHAT_HISTORY.pop("oc_mem", None)
    bot.load_chat_state()
    kept = bot.CHAT_HISTORY.get("oc_mem") or []
    ok = any("溶解" in turn["content"] for turn in kept)
    failed += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] 对话重启后还在")
    bot.CHAT_HISTORY["oc_mem"] = [
        {"role": "user", "content": "第一问"},
        {"role": "assistant", "content": "上一句答案溶解"},
    ]
    followed = bot.with_previous_turn("你觉得这回答对吗？")
    ok = "上一句答案溶解" in followed and "你觉得这回答对吗" in followed
    failed += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] 追问带上上一句")
    plain = {
        "message_type": "text",
        "content": "Assets/Dependences/Scenes/Common/Trees/Textures/OP_TY_treeBJ05_new_d.tga 上传记录",
    }
    got = bot.extract_text(plain)
    ok = "OP_TY_treeBJ05_new_d.tga" in got
    failed += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] 引用的纯文本能读出路径")
    packed = {"message_type": "text", "content": json.dumps({"text": "Assets/A.tga 上传记录"}, ensure_ascii=False)}
    got = bot.extract_text(packed)
    ok = "Assets/A.tga" in got
    failed += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] 事件里的 JSON 正文还能读")
    bot.CURRENT_CHAT.id = "oc_git_follow"
    bot.CHAT_HISTORY.pop("oc_git_follow", None)
    bot.remember_turn("user", "Assets/Dependences/Scenes/Common/Trees/Textures/OP_TY_treeBJ05_new_d.tga 上传记录")
    bot.remember_turn("assistant", "origin/main")
    bot.remember_turn("user", "CBT2_bugfix分支")
    merged = bot.git_query_text("CBT2_bugfix分支")
    ok = "OP_TY_treeBJ05_new_d.tga" in merged and "CBT2_bugfix" in merged
    failed += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] 只说分支时带上上次的文件")
    bot.CURRENT_CHAT.id = "oc_git_empty"
    bot.CHAT_HISTORY["oc_git_empty"] = [{"role": "user", "content": "CBT2_bugfix分支"}]
    same = bot.git_query_text("CBT2_bugfix分支")
    ok = same == "CBT2_bugfix分支"
    failed += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] 没有上次文件就还是这句")
    rel_path, fuzzy_hits = bot.fuzzy_git_file("UIUL_Raid_Chat_Team_Invite.cs", bot.get_git_root())
    ok = rel_path == "Client/Assets/Scripts/Logic/UI/UI_Raid/UI_Raid_Chat_Team_Invite.cs" and not fuzzy_hits
    failed += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] 抄错的脚本名能对上仓库文件 {rel_path}")
    named = bot.asset_name_from_vision(
        "Assets/Scripts/Logic/UI/UI_Raid/UI_Raid_Chat_Team_Invite.cs",
        "这个脚本的提交记录",
    )
    ok = named.endswith("UI_Raid_Chat_Team_Invite.cs") and not named.endswith(".prefab")
    failed += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] 识图路径保持完整 {named}")
    terms = bot.doc_terms("入职文档", ["入职文档", "美术规范", "新人指引"])
    short = bot.loosen_doc_term("入职文档")
    kept = bot.filter_doc_titles(
        [{"title": "3D场景美术制作规范V2.0"}, {"title": "新人入职指引"}],
        ["入职"],
    )
    ok = (
        "美术规范" not in terms
        and "新人指引" not in terms
        and "入职" in terms
        and short == "入职"
        and [item["title"] for item in kept] == ["新人入职指引"]
    )
    failed += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] 入职文档不改搜美术规范 {terms} {short}")
    topic = bot.build_query("帮我找下视差贴图的文档")
    ok = (
        topic == "视差贴图"
        and bot.doc_terms("帮我找下视差贴图的文档", []) == ["视差贴图"]
        and bot.loosen_asset_term("视差贴图") == "视差"
        and bot.loosen_asset_term("入职") == ""
    )
    failed += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] 找文档抽主题词 {topic}")
    rules = bot.parse_watch_rules("Assets/Bundle/Shader .shader .hlsl")
    sample = "--abc1234\x1fzhang\x1f2026-11-02 16:20\x1f改描边\nClient/Assets/Bundle/Shader/Uber.shader\nClient/Assets/Scripts/Foo.cs\n"
    notice = bot.format_watch_commits("CBT2_bugfix", bot.parse_name_log(sample), rules)
    ok = (
        bot.watch_hit("Client/Assets/Bundle/Shader/Role/A.hlsl", rules)
        and not bot.watch_hit("Client/Assets/Bundle/Shader/Role/A.cs", rules)
        and not bot.watch_hit("Client/Assets/Bundle/ShaderBak/A.shader", rules)
        and "Uber.shader" in notice
        and "Foo.cs" not in notice
        and "abc1234" in notice
    )
    failed += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] 盯目录只认 shader 和 hlsl")
    told = bot.unseen_watch_commits(bot.parse_name_log(sample), {"abc1234"})
    ok = (
        bot.clamp_watch_days(2) == 2
        and bot.clamp_watch_days(0) == 1
        and bot.clamp_watch_days(9) == 7
        and told == []
        and "Client/Assets/Bundle/Shader" in bot.watch_pathspecs(rules)
    )
    failed += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] 盯目录按天数去重")
    ok = (
        bot.watch_report_full(True, 5, 5)
        and not bot.watch_report_full(False, 5, 5)
        and bot.watch_report_full(False, 1, 5)
    )
    failed += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] 改天数或手动查要列出全部")
    shared = set()
    commits = bot.parse_name_log(sample)
    first = bot.format_watch_commits("main", commits, rules, shared)
    second = bot.format_watch_commits("CBT2_bugfix", commits, rules, shared)
    ok = "Uber.shader" in first and second == ""
    failed += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] 两条分支的同一提交只列一次")
    ok = bot.watch_title(5) == "盯目录 · 最近 5 天"
    failed += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] 盯目录标题 {bot.watch_title(5)}")
    ok = bot.is_price_question("现在的金价") and not bot.is_price_question("这个shader有几个pass") and "元/克" in bot.PRICE_ANSWER_RULES
    ok = ok and bot.is_gold_price_question("今天金价") and not bot.is_gold_price_question("推荐我什么时候买入黄金比较好")
    ok = ok and bot.is_gold_advice_question("推荐我什么时候买入黄金比较好") and not bot.is_gold_advice_question("今天金价")
    failed += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] 金价按人民币每克报，不写项目建议")
    token_answer = bot.answer_token_status("你还有多少token")
    ok = (
        bot.is_token_status_question("你还有多少token")
        and bot.is_token_status_question("额度用完了吗")
        and bot.is_token_status_question("限流了吗")
        and not bot.is_token_status_question("这个shader有几个pass")
        and not bot.is_token_status_question("贴图要不要压成ASTC 6x6")
        and "查不到" in token_answer
        and "今天" in token_answer
        and "动态变化" not in token_answer
        and "保持问题简洁" not in token_answer
    )
    failed += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] token 问法报真实调用、不编剩余额度")
    quoted = bot.format_gold_quote("2026-10-26,865.00,860.80,872.99,855.00,1,1,2.08,-0.37,-3.20,0.00")
    ok = "860.80 元/克" in quoted and "-3.20" in quoted and "金条零售价" in quoted and "Au99.99" in quoted
    sina = 'var hq_str_gds_AU9999="860.51,0,860.21,860.51,872.99,855.00,15:18:56,864.00,865.00,1,1,1,2026-10-26,黄金9999";'
    sina_quote = bot.format_gold_quote(bot.sina_to_bar(sina))
    ok = ok and "860.51 元/克" in sina_quote and "-3.49" in sina_quote and "872.99" in sina_quote
    failed += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] 黄金9999按行情接口换算")
    bar = {"title": "金至尊金条今日价格", "snippet": "1058 元/克"}
    spot = {"title": "上海黄金交易所 Au99.99", "snippet": "今日 元/克"}
    ok = not bot.keep_spot_quote(bar) and bot.keep_spot_quote(spot)
    failed += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] 金条零售价不当成金价")
    ok = "Eagle" in bot.VISION_PROMPT and "Windows Defender" in bot.VISION_PROMPT and "不猜" in bot.VISION_BUSY_TEXT
    failed += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] 认软件不把 Eagle 图标说成系统工具")
    _pull_now = bot.datetime.datetime(2026, 10, 30, 16, 0, tzinfo=bot.BEIJING_TZ)
    _pull_future = _pull_now + bot.datetime.timedelta(hours=5)
    _pull_past = _pull_now - bot.datetime.timedelta(hours=1)
    _remind_now = bot.datetime.datetime(2026, 10, 30, 16, 0, tzinfo=bot.BEIJING_TZ)
    _remind_when, _remind_rolled = bot.plan_reminder_when("18点提醒胡诚更新艾尼路蒙皮", _remind_now)
    _remind_late, _remind_late_rolled = bot.plan_reminder_when(
        "18点提醒胡诚", bot.datetime.datetime(2026, 10, 30, 19, 0, tzinfo=bot.BEIJING_TZ))
    _remind_pm, _remind_pm_rolled = bot.plan_reminder_when("下午6点提醒", _remind_now)
    ok = (
        bot.HISTORY_TURNS >= 16
        and bot.HISTORY_CHARS >= 1500
        and bot.DEFAULT_LLM_MODEL == "glm-4.5-flash"
        and bot.DEFAULT_LLM_VISION_MODEL == "glm-4.6v-flash"
        and "24 行" in bot.WEB_GENERAL_PROMPT
        and "不要整题拒答" in bot.WEB_OFFLINE_PROMPT
        and "禁止写" not in bot.STYLE_RULES
        and bot.should_coach("gold-advice", "什么时候买入黄金")
        and not bot.should_coach("gold", "今天金价")
        and bot.is_which_branch_question("这是哪条分支")
        and not bot.is_which_branch_question("切到 CBT2_bugfix")
        and bot.is_pull_request("拉新项目")
        and not bot.is_pull_request("从哪拉项目")
        and bot.is_pull_schedule_request("定时拉新 每隔5小时 拉新一次")
        and not bot.is_pull_request("定时拉新 每隔5小时 拉新一次")
        and bot.parse_pull_interval("每隔5小时") == 5 * 3600
        and bot.PULL_SCHEDULE_POLL_SEC <= 30
        and bot.plan_pull_next(True, 18000, 18000, _pull_future, _pull_now) == _pull_future
        and bot.plan_pull_next(True, 18000, 7200, _pull_future, _pull_now) == _pull_now + bot.datetime.timedelta(hours=2)
        and bot.plan_pull_next(True, 18000, 18000, _pull_past, _pull_now) == _pull_now + bot.datetime.timedelta(hours=5)
        and bot.plan_pull_next(False, 18000, 3600, _pull_future, _pull_now) == _pull_future
        and "只认朱辉" in bot.answer_pull_schedule("定时拉新", "ou_not_owner", "oc_x")
        and "只认朱辉" in bot.answer_pull("ou_not_owner")
        and bot.git_write_allowed(["reset", "--hard", "HEAD"])
        and not bot.git_write_allowed(["reset", "--hard", "origin/main"])
        and not bot.git_write_allowed(["clean", "-fd"])
        and "send_owner_pull" in inspect.getsource(bot.pull_schedule_loop)
        and "send_chat(" not in inspect.getsource(bot.pull_schedule_loop)
        and "已清掉" in inspect.getsource(bot.answer_pull)
        and "先提交或暂存" not in inspect.getsource(bot.answer_pull)
        and bool(bot.REFERENCE_IN_PATTERN.search("Assets/A/B.prefab 引用关系"))
        and bot.is_git_request("Fx_Monster_Ace_Fire_01这个贴图资源是谁最新修改上传的")
        and bot.is_git_request("Assets/Bundle/UI/Animation/UI_Raid/UI_Raid_Settlement_BigStage_AN_Fail.anim\n谁更改了这个文件")
        and bot.is_git_request("Assets/UI/A.anim 什么时候改的")
        and not bot.is_git_request("Assets/a.prefab 谁引用了，帮我改一下")
        and not bot.is_git_request("谁能帮我改个文案")
        and "哪个文件" in bot.missing_target_ask("这个文件谁负责", False)
        and "哪个问题" in bot.missing_target_ask("这个报错怎么处理", False)
        and bot.missing_target_ask("这个文件谁负责", True) == ""
        and bot.missing_target_ask("UI_Raid_Settlement_BigStage_AN_Fail 这个文件谁更改了", False) == ""
        and bot.missing_target_ask("收藏这个表情 狗头", False) == ""
        and bot.missing_target_ask("应该怎么做", False) == ""
        and "没有真实记录" in bot.guard_fabrication("最近一次修改是由同事 \"zhangsan\" 在 2026 年 10 月 15 日 14:32 提交的", "谁改了")
        and "没有真实记录" in bot.guard_fabrication("2026年10月15日 wangqingyang 提交了这个文件。", "谁改了")
        and bot.guard_fabrication("可以用 git log 查看最近一次修改。", "怎么查") == "可以用 git log 查看最近一次修改。"
        and bot.guard_fabrication("建议由策划修改配置表。", "谁来改") == "建议由策划修改配置表。"
        and bot.guard_fabrication("张三请假三天。", "用张三举例写请假条") == "张三请假三天。"
        and bot.parse_author_commit("zhuhui今天的提交内容") == ("zhuhui", "day", 0)
        and bot.parse_author_commit("今天zhuhui的提交") == ("zhuhui", "day", 0)
        and bot.parse_author_commit("朱辉昨天提交了什么") == ("朱辉", "day", 1)
        and bot.parse_author_commit("今天的提交内容") == ("", "day", 0)
        and bot.parse_author_commit("zhuhui最近10条提交") == ("zhuhui", "recent", 10)
        and bot.parse_author_commit("zhuhui最近的10条修改 分析总结") == ("zhuhui", "recent", 10)
        and bot.is_git_request("zhuhui最近的10条修改 分析总结")
        and bot.parse_author_commit("朱辉最近的10条修改 分析报告") == ("朱辉", "recent", 10)
        and bool(bot._COMMIT_REPORT_PATTERN.search("zhuhui最近的10条修改 分析报告"))
        and "Library/a.prefab" not in bot._report_diff_text("diff --git a/Library/a.prefab b/Library/a.prefab\n+x\n")
        and "a.shader" in bot._report_diff_text("diff --git a/a.shader b/a.shader\n+x\n")
        and "场景" in bot._report_diff_text("diff --git a/a.asset b/a.asset\n+name: \\u573A\\u666F\n")
        and bot.commit_overview([("a1", "", "【Shader】x"), ("b2", "", "【Shader】y"), ("c3", "", "z")], 2)
        == "共 5 条：Shader 2，其他 1，合并 2（无自身改动，未分析）。"
        and bool(bot._REPORT_EMPTY_LINE.match("隐患：无。"))
        and bool(bot._REPORT_EMPTY_LINE.match("提升：看不出。"))
        and not bot._REPORT_EMPTY_LINE.match("隐患：删了 _Foo 仍被引用")
        and bool(bot._REPORT_EMPTY_LINE.match("隐患：新增属性可能存在精度问题"))
        and bot.parse_author_commit("zhuhui 今天的提交 自检报告") == ("zhuhui", "day", 0)
        and bot.stickers.parse_collect("收藏 叫狗头") == "狗头"
        and bot.stickers.parse_collect("收藏这个表情 狗头") == "狗头"
        and bot.stickers.parse_collect("收藏夹在哪") == ""
        and bot.stickers.parse_collect("存一下") == ""
        and bot.stickers.parse_send("发个狗头表情") == "狗头"
        and bot.stickers.parse_send("来个表情包") == ""
        and bot.stickers.parse_send("发个提醒") is None
        and bot.stickers.parse_send("帮我发个邮件") is None
        and bot.stickers.parse_delete("删除表情 狗头") == "狗头"
        and bot.stickers.is_list_request("表情列表")
        and bot.stickers.safe_name("../x") == ""
        and bot.tame_chat_emoji("帮我润色一下", "今日完成娜美贴图修改。🎨") == "今日完成娜美贴图修改。"
        and bot.tame_chat_emoji("你好", "嘿嘿") == "嘿嘿"
        and bot.tame_chat_emoji("你好", "大豆：嘿嘿") == "嘿嘿"
        and bot.is_chat_opinion("你觉得路飞和索隆谁更强")
        and not bot.is_chat_opinion("贴图压缩用ASTC还是ETC2")
        and not bot.is_chat_opinion("项目里谁负责路飞模型")
        and bomb_fake_ok()
        and bomb_judge_cache_ok()
        and bot.bomb.is_bomb_request("今天有没有炸线")
        and bot.bomb.is_bomb_request("谁合并把我的修改丢了")
        and not bot.bomb.is_bomb_request("zhuhui今天的提交")
        and bot.bomb.parse_days("今天谁炸线了") == 0
        and bot.bomb.parse_days("最近5天炸线") == 5
        and bot.bomb.parse_days("炸线") == 3
        and bot.bomb.parse_branches("main 炸线", ["main", "CBT2_bugfix"]) == ["main"]
        and bot.bomb.parse_branches("cbt2_bugfix 炸线", ["main", "CBT2_bugfix"]) == ["CBT2_bugfix"]
        and bot.bomb.parse_branches("炸线", ["main", "CBT2_bugfix"]) == ["main", "CBT2_bugfix"]
        and bot.normalize_bilibili_cookie("abc123%2C") == "SESSDATA=abc123%2C"
        and bot.normalize_bilibili_cookie("Cookie: buvid3=x; SESSDATA=y") == "buvid3=x; SESSDATA=y"
        and bot.normalize_bilibili_cookie("buvid3=x") == ""
        and bot.normalize_bilibili_cookie("SESSDATA=a\nb") == ""
        and "没法总结视频内容" in bot.bilibili_no_subtitle_text("t", "u", {"duration": 422}, "简介", "L")
        and "7 分 02 秒" in bot.bilibili_no_subtitle_text("t", "u", {"duration": 422}, "简介", "L")
        and bot.review_answer("今天加班到十点好累", "早点睡", allow_short=True) == ""
        and bool(bot._COMMIT_REPORT_PATTERN.search("zhuhui 今天的提交 自检"))
        and bot.format_author_commits("zhuhui", "今天（2026-11-03）", "origin/main", "")
        == "今天（2026-11-03）在 `origin/main` 上没有 zhuhui 的提交。"
        and bot.parse_author_commit("这个shader怎么改") is None
        and bot.parse_author_commit("SceneModelPreviewBrowser.cs 今天的提交记录") is None
        and bot.is_git_request("zhuhui今天的提交内容")
        and bot.is_git_request("zhuhui最近10条提交")
        and bot.is_commit_self_check_request("自检一下这些修改")
        and bot.commit_hash_in("b5e16814509 这条改了什么内容 有没有传bug") == "b5e16814509"
        and bot.is_commit_detail_request("b5e16814509 这条改了什么内容 有没有传bug")
        and not bot.is_commit_detail_request("zhuhui今天的提交内容")
        and bot.is_git_request("b5e16814509 这条改了什么内容 有没有传bug")
        and bot.parse_checkout_branch("分支切换到CBT2_bugfix_shader") == "CBT2_bugfix_shader"
        and bot.is_checkout_request("切换到 CBT2_bugfix_shader")
        and not bot.is_checkout_request("现在是哪个分支")
        and bot.safe_branch_name("origin/main") == "main"
        and bot.safe_branch_name("../evil") == ""
        and bot.git_write_allowed(["checkout", "-B", "CBT2_bugfix_shader", "origin/CBT2_bugfix_shader"])
        and not bot.git_write_allowed(["checkout", "main"])
        and bot.commit_hash_in("https://www.bilibili.com/video/BV1rrYi6dEtV?vd_source=78dad3aee97cbc3897e9659621cc3313 总结视频内容") == ""
        and not bot.is_commit_detail_request("https://www.bilibili.com/video/BV1rrYi6dEtV?vd_source=78dad3aee97cbc3897e9659621cc3313 总结视频内容")
        and not bot.is_git_request("https://www.bilibili.com/video/BV1rrYi6dEtV?vd_source=78dad3aee97cbc3897e9659621cc3313 总结视频内容")
        and bot.is_bilibili_summary_request("https://www.bilibili.com/video/BV1rrYi6dEtV?vd_source=78dad3aee97cbc3897e9659621cc3313 总结视频内容")
        and bot.bilibili_bvid("https://www.bilibili.com/video/BV1rrYi6dEtV?spm_id_from=333.788") == "BV1rrYi6dEtV"
        and not bot.is_bilibili_summary_request("https://www.bilibili.com/video/BV1rrYi6dEtV")
        and not bot.run_git_readonly(bot.PROJECT_ROOT, ["show", "--stat"])[0]
        and "没有 zhuhui 的提交" in bot.format_author_commits("zhuhui", "今天（2026-10-30）", "origin/CBT2_bugfix", "")
        and "`abc1234`" in bot.format_author_commits("zhuhui", "今天（2026-10-30）", "origin/CBT2_bugfix", "abc1234|zhuhui|2026-10-30 15:30|真实说明")
        and bot.BARE_ASSET_NAME_PATTERN.search("Fx_Monster_Ace_Fire_01这个贴图").group(1) == "Fx_Monster_Ace_Fire_01"
        and "coach_cursor_prompt" in inspect.getsource(bot.run_coach)
        and "Agent.prompt" not in inspect.getsource(bot.run_coach)
        and bot.local_edit.is_local_edit_request("帮我改我本地的 Assets/Scripts/Foo.cs 把 speed 改成 2")
        and not bot.local_edit.is_local_edit_request("怎么改我本地的文件")
        and not bot.local_edit.is_local_edit_request("拉新项目")
        and bot.local_edit.extract_local_edit_path("帮我改我本地的 Assets/Scripts/Foo.cs") == "Assets/Scripts/Foo.cs"
        and bot.local_edit.safe_local_path("../.env") == ""
        and bot.local_edit.extract_rewritten("说明\n<<<FILE>>>\nhello\n<<<END>>>") == "hello"
        and _remind_when.hour == 18 and _remind_when.day == 30 and not _remind_rolled
        and _remind_late.day == 31 and _remind_late_rolled
        and _remind_pm.hour == 18 and not _remind_pm_rolled
        and bot.remind_task("18点提醒胡诚更新艾尼路蒙皮", ["胡诚"]) == "更新艾尼路蒙皮"
        and bot.remind_task("18点加急提醒胡诚更新艾尼路蒙皮", ["胡诚"]) == "更新艾尼路蒙皮"
        and bot.reminder_is_urgent("18点加急提醒胡诚更新艾尼路蒙皮")
        and not bot.reminder_is_urgent("18点提醒胡诚这单加急")
        and bot.is_reminder_request("18点提醒胡诚更新艾尼路蒙皮")
        and not bot.is_reminder_request("这个shader怎么改")
    )
    old_coach = bot.COACH_PATH
    bot.COACH_PATH = os.path.join(tempfile.mkdtemp(), "coach.json")
    bot.save_coach("推荐我什么时候买入黄金比较好", "问买入时机时讲分批，不要只报现价")
    ok = ok and "分批" in bot.matching_coach("什么时候买入黄金比较好") and bot.matching_coach("今天金价") == ""
    old_reviews = bot.REVIEWS_PATH
    bot.REVIEWS_PATH = os.path.join(tempfile.mkdtemp(), "reviews.json")
    bot.record_review("终末地的角色是什么渲染", "web", "用了描边", "对", "")
    saved = bot.load_reviews()
    ok = ok and saved and saved[-1].get("verdict") == "对" and saved[-1].get("route") == "web"
    bot.REVIEWS_PATH = old_reviews
    bot.COACH_PATH = old_coach
    saved_preview = bot.PREVIEWING
    bot.PREVIEWING = True
    preview_edit = bot.answer_local_edit("帮我改我本地的 Assets/A.cs", "ou_x")
    bot.PREVIEWING = saved_preview
    edit_dir = tempfile.mkdtemp()
    read_back = ""
    blocked = False
    try:
        os.makedirs(os.path.join(edit_dir, "Assets"))
        bot.local_edit.write_under(edit_dir, "Assets/A.txt", "one\n")
        read_back = bot.local_edit.read_under(edit_dir, "Assets/A.txt")
        blocked = False
        try:
            bot.local_edit.write_under(edit_dir, "../A.txt", "x")
        except ValueError:
            blocked = True
    finally:
        shutil.rmtree(edit_dir, ignore_errors=True)
    ok = ok and "不会改这台机器的工程" in preview_edit and read_back == "one\n" and blocked
    failed += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] 回答可以展开，模型仍是 flash")
    bot.CHAT_HISTORY.pop("oc_mem", None)
    bot.CHAT_HISTORY.pop("oc_git_follow", None)
    bot.CHAT_HISTORY.pop("oc_git_empty", None)
    bot.HISTORY_PATH = old_path
    bot.PERSIST_HISTORY = old_persist
    return failed


def run_all() -> int:
    bot.reply = fake_reply
    bot.RECORD_MISSES = False
    bot.RECORD_QUESTIONS = False
    bot.PERSIST_HISTORY = False
    bot.PEOPLE_PATH = os.path.join(tempfile.mkdtemp(), "people.json")
    bot.add_reaction = lambda *args, **kwargs: None
    bot.DIR_INDEX.extend(bot.load_dir_index())
    failed = 0
    index = 0
    only = os.environ.get("REGRESS_ONLY", "")
    full = os.environ.get("REGRESS_FULL") == "1"
    for group in CASES:
        if only and not any(only in question for question, _, _ in group):
            continue
        bot.CHAT_HISTORY.pop(CHAT_ID, None)
        bot.LAST_QUESTION.pop(CHAT_ID, None)
        bot.LAST_PREFAB_HITS.pop(CHAT_ID, None)
        bot.LAST_REF_HITS.pop(CHAT_ID, None)
        for question, must, forbid in group:
            index += 1
            started = time.time()
            answer = run_question(index, question)
            question = f"{question}（{time.time() - started:.0f}s）"
            missing = [word for word in must if word not in answer]
            leaked = [word for word in forbid if word in answer]
            ok = bool(answer) and not missing and not leaked
            failed += 0 if ok else 1
            status = "PASS" if ok else "FAIL"
            preview = answer if full else " ".join(answer.split())[:110]
            print(f"[{status}] {question}\n       -> {preview}")
            if missing:
                print(f"       缺少：{missing}")
            if leaked:
                print(f"       不该出现：{leaked}")
    run_extra = not only
    memory_failed = run_memory()
    failed += memory_failed
    index += 20
    run_miss = run_extra or only in ("miss", "失败")
    if run_miss:
        miss_failed, miss_count = run_miss_log()
        failed += miss_failed
        index += miss_count
    if run_extra:
        failed += run_mentions()
        index += len(MENTION_CASES)
        failed += run_reactions()
        index += len(REACTION_CASES)
    print(f"\n{index - failed}/{index} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
