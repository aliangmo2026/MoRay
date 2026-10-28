# -*- coding: utf-8 -*-
"""tests/test_task_planner.py — U-4 task_planner 兜底策略修复测试（2026-10-01）。

运行（cwd 必须是 lora-training，保证 import 命中工程内模块）：
    cd /d D:\\MoRay\\decision-kernel\\lora-training
    D:\\MoRay\\server\\.venv\\Scripts\\python.exe -m pytest tests\\test_task_planner.py -v

覆盖三类改动（详见 modules/task_planner.py 内 [2026-10-01 U-4] 标注）：
  * 改动 1：file 词表补"创建/新建/生成 + 文件类后缀"组合词（裸动词不入 file 词表）；
  * 改动 2：首子句无信号三级兜底——操作类动词→zcode、代码特征→zcode、搜索保底；
  * 改动 3：_classify 匹配前绝对路径占位化，防路径子串（zcode/python）劫持分类。

BUG 复现背景：改前"创建 fizzbuzz.py"无词表信号 → 兜底 argus → bing 搜索
（argus 日志 14:06:49 实锤 cn.bing.com/search?q=创建 fizzbuzz.py）。
"""
import asyncio

from modules.task_planner import _classify, plan


def _steps(goal):
    return asyncio.run(plan(goal))["steps"]


# ---- 1. 空目标 → steps=[] ----
def test_01_empty_goal_returns_no_steps():
    result = asyncio.run(plan(""))
    assert result["steps"] == []
    assert result["mode"] == "rule"


# ---- 2. 明显代码任务 → zcode ----
def test_02_explicit_code_task_to_zcode():
    steps = _steps("帮我写个 Python 脚本")
    assert len(steps) == 1
    assert steps[0]["tool"] == "zcode"
    assert "脚本" in steps[0]["input"]["code"]


# ---- 3. 明显搜索任务 → argus（或 twin 内部知识闸命中后原位改写 decision） ----
def test_03_explicit_search_task_to_argus():
    steps = _steps("查一下最新的新能源补贴政策")
    assert len(steps) == 1
    # [2026-10-27 修] twin 闸（2026-10-24 落地）命中本地知识库时把 argus 原位改写为
    # decision（跳过外呼搜索），两种结果都合法；旧断言钉死 argus 在有知识命中环境必红。
    assert steps[0]["tool"] in ("argus", "decision")
    if steps[0]["tool"] == "argus":
        assert steps[0]["input"]["query"] == "新能源补贴政策"


# ---- 4. BUG 复现：'创建 fizzbuzz.py' 不再兜底 argus ----
def test_04_create_fizzbuzz_no_longer_argus():
    goal = "创建 fizzbuzz.py，实现 1 到 15 的 FizzBuzz 输出，并运行验证"
    steps = _steps(goal)
    assert len(steps) == 1                       # 虚词子句并入首步，不拆散
    assert [s["tool"] for s in steps] == ["zcode"]   # 改前：argus（bing 搜索实锤）
    assert "fizzbuzz" in steps[0]["input"]["code"].lower()
    assert "并运行验证" in steps[0]["description"]   # 后续子句并入描述而非丢失


# ---- 5. 路径劫持修复：zcode_sandbox 里的 'zcode' 子串不再劫持 ----
def test_05_zcode_in_path_no_hijack():
    goal = r"在 D:\MoRay\workspace\zcode_sandbox\_ui_test 下创建 fizzbuzz.py"
    steps = _steps(goal)
    assert len(steps) == 1
    assert steps[0]["tool"] == "zcode"           # 走一级兜底（操作类动词"创建"）
    code = steps[0]["input"]["code"]
    assert "zcode_sandbox" in code               # 占位只在匹配阶段，路径信息不丢
    assert "__PATH_PLACEHOLDER" not in code      # 占位符绝不外泄到下游入参


# ---- 6. 纯问候 → [2026-10-27 寒暄闸] 零工具步骤 + casual 对话回复（原：兜底 argus 搜索） ----
def test_06_greeting_is_casual_not_argus():
    result = asyncio.run(plan("你好"))
    assert result["steps"] == []                 # 改前：argus 搜索"你好"（实锤事故）
    assert result.get("casual") is True
    assert result["mode"] == "casual"
    assert result["reply"]                       # 带对话式回复，/execute 短路渲染


# ---- 6b. [2026-10-27 寒暄闸] 变体全拦 + 含任务信号短句零误伤 ----
def test_06b_casual_gate_variants():
    from modules.task_planner import _is_casual_text

    for g in ("你好", "hi", "Hello!", "在吗", "嗯。", "早上好！", "谢谢！"):
        assert _is_casual_text(g) is True, g
        r = asyncio.run(plan(g))
        assert r["steps"] == [] and r.get("casual") is True, g

    # 含任务信号的短句 → 绝不拦截（保真任务）
    for g in ("改bug", "查价格", "写个脚本", "跑测试", "搜新闻"):
        assert _is_casual_text(g) is False, g

    # 混合句：寒暄子句被跳过，真任务子句正常拆解，query 不混入寒暄词
    steps = _steps("你好，帮我搜一下最新的AI资讯")
    assert steps, "真任务子句不得被寒暄闸吞掉"
    for s in steps:
        if s["tool"] == "argus":
            assert "你好" not in s["input"].get("query", "")


# ---- 6c. [2026-10-27 寒暄闸 v2·前缀判据] "晚上好呀moray" 实锤复盘用例 ----
# 事故链：整句剥词闸被"呀moray"后缀绕过 → 三级兜底 argus → 百度搜"晚上好"。
# v2 = 以寒暄词开头且全文零任务词 → plan() 最前直接返回 casual 帧（零工具步骤）。
def test_06c_greeting_opening_prefix_gate():
    from modules.task_planner import _is_casual_text, _is_greeting_opening

    for g in ("晚上好呀moray", "你好呀moray", "moray晚上好呀", "moray, hi",
              "hi moray", "moray 晚上好", "嗯，我觉得你说得对"):
        assert _is_greeting_opening(g) is True, g
        assert _is_casual_text(g) is True, g
        r = asyncio.run(plan(g))
        assert r["steps"] == [] and r.get("casual") is True, g
        assert r["mode"] == "casual" and r["reply"], g

    # 含任务动作词的寒暄句 → 绝不拦截（真任务零误伤）
    for g in ("晚上好，帮我查下价格", "你好，帮我搜一下最新的AI资讯", "moray早上好，写个脚本"):
        assert _is_greeting_opening(g) is False, g
        assert _is_casual_text(g) is False, g
        assert asyncio.run(plan(g))["steps"], g

    # 英文词前缀误伤防护：hi→history / hello→helloworld 不得判寒暄
    for g in ("history of python", "himawari 是什么"):
        assert _is_greeting_opening(g) is False, g


# ---- 7. 混合任务：搜索在前、代码在后 ----
def test_07_search_then_code_two_steps():
    steps = _steps("查一下 xx 然后写个脚本")
    assert len(steps) == 2
    assert [s["tool"] for s in steps] == ["argus", "zcode"]
    assert steps[0]["input"]["query"] == "xx"
    assert steps[1]["input"]["code"] == "写个脚本"


# ---- 8. 路径里的其他词不劫持：python/lib/test 均不参与分类 ----
def test_08_words_in_path_do_not_hijack_file_task():
    steps = _steps(r"读取 D:\python\lib\test\file.txt")
    assert len(steps) == 1
    assert steps[0]["tool"] == "file"            # 'python' 子串不得劫持成 zcode
    assert steps[0]["input"]["op"] == "read"
    assert steps[0]["input"]["path"] == r"D:\python\lib\test\file.txt"


# ---- 9. _classify 单元级：路径占位生效的直接证据 ----
def test_09_classify_path_placeholder_unit():
    # 改前：路径里 'zcode' 子串命中 zcode 词表 → 返回 "zcode"（劫持实锤）
    assert _classify(r"在 D:\MoRay\workspace\zcode_sandbox\_ui_test 下创建 fizzbuzz.py") is None
    # 路径里的 'python' 不劫持，'读取' 正常归 file
    assert _classify(r"读取 D:\python\lib\test\file.txt") == "file"
    # 无路径的纯代码任务分类不受占位影响
    assert _classify("帮我写个 Python 脚本") == "zcode"


# ---- 10. 三级兜底第二级：纯代码特征（无动作词）仍归 zcode ----
def test_10_bare_code_hint_still_zcode():
    steps = _steps("print(1+2)")
    assert len(steps) == 1
    assert steps[0]["tool"] == "zcode"           # 2026-09-30 修复不得回退成 argus
    assert steps[0]["input"]["code"] == "print(1+2)"


# ---- 11. file 词表新组合词：'创建文件' 归 file 且 op=write ----
def test_11_create_file_combo_word():
    steps = _steps("创建文件 report.md")
    assert len(steps) == 1
    assert steps[0]["tool"] == "file"
    assert steps[0]["input"]["op"] == "write"
    assert steps[0]["input"]["path"] == "report.md"


# ---- 12. 三级兜底第一级：'做一个/实现' 类操作动词 → zcode ----
def test_12_action_verbs_go_zcode():
    for goal in ("做一个贪吃蛇游戏", "实现用户登录功能"):
        steps = _steps(goal)
        assert len(steps) == 1
        assert steps[0]["tool"] == "zcode", goal     # 改前双双兜底 argus 搜索
