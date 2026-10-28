# -*- coding: utf-8 -*-
"""L7.6 任务规划器：自然语言目标 → 结构化步骤列表。

主路径 = 确定性规则引擎（分句 → 关键词识别工具 → 抽取入参），保证：
  * 零依赖、毫秒级、结果可复现（测试不碰网络也能验证拆解形状）；
  * 与主服务 /api/decide 的 RULE_MAP 关键词族同源（视觉→iris、资料→argus、
    代码→zcode、文件→file），风格一致。

可选增强：环境变量 LAYA_PLANNER_LLM=1 时，先让小模型（moray-laya-final）做一次
结构化拆解，产出同样五字段；任何异常/形状不符 → 静默回落到规则拆解。

步骤五字段（任务书 三.1）：
  step_id / description / tool(iris|argus|zcode|file) / input(dict) / expected_output

[L7.7 体验打磨（2026-09-29，纯加性）]：
  * TOOL_KEYWORDS 每工具扩充 ≥12 个常用触发词（降低错配/漏配）；
  * 分句连接词扩充（首先/随后/紧接着/而后/顺便/顺手）；
  * 虚词子句并入前一步（原实现会把无信号子句兜底成 argus 检索，属典型漏配）；
  * 新增 3 种复合任务模式（先整体匹配、命中即产出带 {prev_output} 串联的多步计划，
    优先于分句拆解）：
      1) 查资料→写文件     argus → file(write)
      2) 读文件→改代码→执行 file(read) → zcode(改) → zcode(执行)
      3) 截图识别→分析→生成修复代码 iris → zcode(分析) → zcode(生成修复代码)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

import httpx

from . import self_check as _sc
from . import tool_registry as _tool_registry

_TOOLS = ("iris", "argus", "zcode", "file")

# 工具识别词表（命中即归类；顺序敏感：先具体后泛化）
# [L7.7] 尾部为本次扩充的常用触发词（每工具新增 ≥12 个，均不与其它工具词表重叠）
TOOL_KEYWORDS = [
    ("iris", ["看图", "读截图", "截图", "识别报错", "识别图片", "报错截图", "ocr", "图片",
              "read image", "screenshot", "照片", "界面长啥样", "pdf", "读文档",
              # [L7.7 扩充]
              "识图", "读图", "图像", "视觉", "截屏", "屏幕截图", "看看这张图", "图里",
              "照片里", "识别文字", "提取文字", "扫描件", "票据", "报错图"]),
    ("file", ["读文件", "写文件", "遍历", "列目录", "列出目录", "打开文件", "保存", "写入",
              "读取", "文件", "目录", "read file", "write file", "list dir", "落盘",
              # [L7.7 扩充]
              "文件夹", "清单", "列出文件", "文件列表", "另存", "导出到", "追加写",
              "看看文件", "打开目录", "读一遍", "存成", "统计文件",
              # [2026-10-01 U-4] 只收"创建/新建/生成 + 文件类后缀"组合词（明确文件操作）。
              # 裸动词"创建/新建/生成/做一个/做个"刻意不入本词表：它们由 _rule_plan
              # 兜底层的 _ACTION_HINT_RE 归 zcode（改前"创建 fizzbuzz.py"被兜底给
              # argus 搜索），若入本词表反而会使命中 file 且 _file_op 误判成 read。
              "创建文件", "新建文件", "生成文件", "创建目录", "新建目录", "新建文件夹"]),
    ("zcode", ["写代码", "跑脚本", "执行代码", "运行代码", "运行脚本", "脚本", "代码",
               "编程", "修复代码", "跑一下", "执行", "run code", "python", "函数", "接口实现",
               # [L7.7 扩充]
               "改代码", "修bug", "重构", "跑测试", "执行脚本", "运行程序", "执行命令",
               "跑命令", "编译", "调试代码", "生成脚本", "统计数量", "数一下", "批量处理",
               # [2026-09-30 自然语言] 显式点名 zcode 的工具指令（用zcode/通过zcode/调用zcode）
               # 必须路由到 zcode 工具，避免被 argus 搜索兜底吞掉
               "zcode"]),
    ("argus", ["查资料", "搜", "调研", "检索", "行业", "资讯", "新闻", "查最新", "查一下",
               "了解", "search", "研究", "行情", "政策", "最近的",
               # [L7.7 扩充]
               "查查", "帮我查", "查询", "热点", "趋势", "市场", "评测", "对比评测",
               "最新动态", "榜单", "价格", "多少钱", "搜集", "爬取", "全网"]),
]

# 期望产出模板（按工具 + file 的子操作）
_EXPECT = {
    "iris": "识别出图片/报错的关键信息（error_type、内容与诊断建议）",
    "argus": "返回与查询词相关的检索结果列表（标题+链接+摘要）",
    "zcode": "代码执行成功：退出码 0，stdout 有预期产出",
    "file": {"read": "读取到目标文件/目录的实际内容", "write": "内容已写入目标文件并可回读",
             "list_dir": "列出目标目录的条目清单"},
}

# 分句：连接词与中英文分隔符（逗号/半角逗号都算子句边界，顿号不算）
# [L7.7] 扩充：首先/随后/紧接着/而后/顺便/顺手
_SPLIT_RE = re.compile(
    r"[；;,，。\n]|首先|随后|紧接着|而后|顺便|顺手|然后|接着|之后|最后|再帮我|同时(?:把|将|对)?")
# 路径/文件名：盘符绝对路径、ASCII 裸文件名+扩展名（禁中文前缀粘连）、或引号包裹串
# [L7.7] 扩展名表追加 bat/sh/ps1/cmd/ini/cfg/toml/xml（常用脚本与配置文件）
_PATH_RE = re.compile(
    r"(?:[A-Za-z]:[\\/][^\s，,;；。'\"|]+)"
    r"|(?:[A-Za-z0-9_.\-]+(?:[\\/][A-Za-z0-9_.\-]+)*\.(?:png|jpg|jpeg|gif|bmp|webp|pdf|txt|md|json|ya?ml|csv|log|py|js|ts|html|bat|sh|ps1|cmd|ini|cfg|toml|xml))")
_QUOTED_RE = re.compile(r"[\"'“”‘’《「『]([^\"'“”‘’》」』]{2,120})[\"'“”‘’》」』]")
# [2026-10-01 U-4] 绝对路径（盘符 D:\... 或 POSIX /...）。仅在 _classify 匹配前替换为
# 占位符，防路径里的工具名子串劫持分类（复现：D:\...\zcode_sandbox\_ui_test 的
# "zcode" 子串命中 zcode 词表 → 整句自然语言被当 code 传给 zcode 工具）。
# 占位只作用于匹配阶段，描述/入参对下游仍用原句，路径信息不丢。
_PATH_PATTERN_RE = re.compile(r"[A-Za-z]:\\[^\s]+|/[^\s]+")


def _classify(clause: str) -> Optional[str]:
    """[L7.7] 无信号时返回 None（交由调用方并入前一步），不再一律兜底 argus。

    [2026-10-01 U-4] 匹配前先把绝对路径占位化：路径中的 "zcode"/"python" 等子串
    会命中词表、劫持整句分类。占位串不含任何工具词/代码特征词且只在匹配阶段存在。
    返回签名不变：Optional[str]，无信号仍返回 None。
    """
    _n = [0]

    def _placeholder(_m) -> str:
        _n[0] += 1
        return "__PATH_PLACEHOLDER_%d__" % (_n[0] - 1)

    low = _PATH_PATTERN_RE.sub(_placeholder, clause).lower()
    for tool, kws in TOOL_KEYWORDS:
        if any(k in low for k in kws):
            return tool
    if re.search(r"报错|异常|错误|traceback|error", low):   # 兜底：纯文本报错 → iris 分析
        return "iris"
    return None


def _extract_path(clause: str) -> str:
    m = _PATH_RE.search(clause)
    if m:
        return m.group(0).strip(" ,.，。'\"")
    m = _QUOTED_RE.search(clause)
    if m and ("/" in m.group(1) or "\\" in m.group(1) or "." in m.group(1)):
        return m.group(1).strip()
    return ""


def _file_op(clause: str) -> str:
    low = clause.lower()
    if any(k in low for k in ("遍历", "列目录", "列出", "list", "目录里有", "有哪些文件",
                              "文件夹", "清单", "文件列表", "打开目录")):
        return "list_dir"
    if any(k in low for k in ("写", "保存", "落盘", "写入", "输出到", "write", "存",
                              "另存", "导出到", "追加写", "存成", "创建文件", "新建文件")):
        return "write"
    return "read"


# [修复 2026-09-30] 写入内容提取：剥掉路径与动作/引导词，剩余文本即用户要写入的内容。
# 分支顺序敏感：组合前缀（用file工具创建文件…）必须排在单前缀之前。
_WRITE_TOOL_PREFIX_RE = re.compile(
    r"^(?:用file工具创建文件|用file工具新建文件|用file工具写入文件|用file工具写文件|"
    r"用file工具|创建文件|新建文件|写入文件|写文件|帮我|请|往文件里)?\s*")
_WRITE_CONJ_RE = re.compile(r"^(?:并|然后|接着|再|同时|顺便|顺手|把|将)?\s*")
_WRITE_LEAD_RE = re.compile(
    r"^(?:写入内容|内容为|内容是|写入|写进|写|追加|追加写|记录|保存为|保存成|保存到|存成|写成|落盘)"
    r"[:：]?\s*")


def _extract_write_content(clause: str, path: str) -> str:
    """从写文件子句中提取用户显式指定的写入内容（path 之外的剩余文本，去掉动作/引导词）。

    仅当剥除后仍有非空文本才返回；纯"写入文件 xxx"（无内容）返回空串 → 保持
    原有"上一步输出注入"语义，两套机制互不冲突。
    """
    rest = clause.replace(path, "", 1) if path else clause
    rest = _WRITE_TOOL_PREFIX_RE.sub("", rest.lstrip(" ，,。；;"))
    rest = _WRITE_CONJ_RE.sub("", rest)
    rest = _WRITE_LEAD_RE.sub("", rest)
    rest = rest.strip(" ，,。；;")
    if not rest or len(rest) > 500:
        return ""
    return rest


# [2026-09-30] zcode 代码提取：剥"调用zcode工具执行代码"等引导词，只留代码本体。
# 背景：zcode 工具已指向 moray /api/engines/zcode/run（纯代码执行，不解释自然语言），
# 原"整句当 code"会导致 "调用zcode工具执行代码 print(1+2) 输出结果" 整句被执行 → SyntaxError。
_ZCODE_LEAD_RE = re.compile(
    r"^(?:调用|使用|用|让|请)?\s*zcode(?:工具|引擎)?\s*(?:来|去|帮我)?\s*(?:执行|运行|跑|调用)\s*"
    r"(?:一下|下)?\s*(?:代码|脚本|程序|python代码|python|py)?\s*[:：]?\s*")
_ZCODE_TRAIL_RE = re.compile(
    r"(?:输出|打印)(?:一下|下)?(?:其|它的)?(?:结果|输出|内容)?\s*[，,。；;\s]*$")
_ZCODE_CODE_HINT_RE = re.compile(
    r"\(|\)|=|import |from |def |print|input\(|\"|'|lambda|return |for |while |if |#")
# [2026-10-01 U-4] 操作类动词：首子句无任何词表信号时的第一级兜底判据（见 _rule_plan）。
# "创建/新建/生成"等动词刻意不进 file 词表（避免"创建 xxx.py"误归 file read），
# 统一在兜底层判定：命中 → zcode（编程/生成类意图），不再 argus 搜索兜底。
_ACTION_HINT_RE = re.compile(r"创建|新建|生成|做一个|做个|写一个|实现|编[写制]|搭[建设]")


def _extract_zcode_code(clause: str) -> str:
    """无代码块时，剥掉 zcode 引导词/尾部要求词，仅保留可执行代码本体。

    若剩余串仍含代码特征（括号调用/赋值/import/def/引号等）→ 作为代码执行；
    否则保留整句原样（自然语言任务，交由上游解释）。
    """
    c = _ZCODE_LEAD_RE.sub("", str(clause or "").strip())
    c = _ZCODE_TRAIL_RE.sub("", c).strip(" ，,。；;")
    if re.search(_ZCODE_CODE_HINT_RE, c):
        return c
    return str(clause or "").strip()


def _build_input(tool: str, clause: str) -> Dict[str, Any]:
    """从子句抽取该工具的入参（宁缺毋滥，缺的字段由 executor 上下文补链）。"""
    low = clause.lower()
    if tool == "iris":
        path = _extract_path(clause)
        if path and re.search(r"\.(png|jpg|jpeg|gif|bmp|webp|pdf)$", path, re.I):
            return {"image_path": path}
        return {"text": clause.strip()}          # 纯文本报错/截图描述 → 文本分析
    if tool == "argus":
        # 去掉触发词本身，留下查询实体；引号内优先
        q = _QUOTED_RE.search(clause)
        query = q.group(1).strip() if q else re.sub(
            r"(帮我|请|查一下|查查|搜索|搜一下|查|调研|了解一下|最新的|资讯|资料|看看)", "", clause).strip(" ，,。")
        return {"query": query or clause.strip()}
    if tool == "zcode":
        code_m = re.search(r"```[\s\S]*?```", clause)
        if code_m:
            return {"code": code_m.group(0).strip("`").strip()}
        return {"code": _extract_zcode_code(clause)}   # [2026-09-30] 剥离引导词只留代码
    # file
    inp: Dict[str, Any] = {"op": _file_op(clause)}
    path = _extract_path(clause)
    if path:
        inp["path"] = path
    if inp["op"] == "write":
        q = _QUOTED_RE.search(clause)
        if q and not path:
            inp["path"] = q.group(1).strip()
        # [修复 2026-09-30] 用户显式给出写入内容时直接提取进 content：
        # 原逻辑一律留空依赖"上一步输出"注入，导致单步直接写固定内容的任务
        # （如：创建 test.txt 并写入内容 权限测试成功）写入空文件（0 字符）。
        content = _extract_write_content(clause, path)
        if content:
            inp["content"] = content
    return inp


def _step(step_id: int, desc: str, tool: str, inp: Dict[str, Any]) -> Dict[str, Any]:
    exp = _EXPECT[tool]
    if tool == "file":
        exp = exp.get(str(inp.get("op")), exp["read"])
    return {"step_id": step_id, "description": desc, "tool": tool,
            "input": inp, "expected_output": exp}


# ===================== [L7.7] 复合任务模式（3 种，整体匹配优先于分句） =====================
# 约定：命中即返回带 {prev_output} 占位串联的多步计划（executor 负责替换与自动注入），
# 未命中返回 None → 走原分句拆解。三模式互斥按声明顺序尝试。

_CONJ_RE = re.compile(r"(然后|接着|之后|再|随后|顺手|顺便|最后|紧接着|把|将|并)")


def _strip_conj(text: str) -> str:
    """[L7.7] 剥掉子句尾部悬挂的连接词（"……，然后" → "……"）。"""
    return re.sub(r"[，,、；;：:\s]*(?:然后|接着|之后|再|随后|顺手|顺便|最后|紧接着|并且|并|把|将|而)[\s，,]*$",
                  "", text.strip(" ，,。；;")).strip(" ，,。；;")

# 模式1：查资料→写文件
_A1_RE = re.compile(r"(查询|查一下|查查|搜索|搜一下|搜|调研|检索|了解一下|了解)"
                    r"[^。；;]{0,40}?(资讯|资料|新闻|行情|动态|信息|报告|热点|进展|趋势|政策|消息|情报)")
_A2_RE = re.compile(r"(写入|写到|写进|写成|保存到|存到|存成|存入|落盘|导出到|记录到|记录成|写成)"
                    r"[^。；;]{0,40}?(文件|txt|md|json|csv|doc|笔记|报告|盘)")


def _chain_research_write(goal: str) -> Optional[List[Dict[str, Any]]]:
    m1 = _A1_RE.search(goal)
    if not m1:
        return None
    tail = goal[m1.end():]
    m2 = _A2_RE.search(tail)
    if not m2 or not _CONJ_RE.search(tail[:m2.start() + 1]):
        return None
    left = _strip_conj(goal[:m1.end() + m2.start()])
    right = goal[m1.end() + m2.start():].strip(" ，,。；;")
    path = _extract_path(right) or "laya_research.md"
    return [
        _step(1, left, "argus", _build_input("argus", left)),
        _step(2, right, "file", {"op": "write", "path": path, "content": "{prev_output}"}),
    ]


# 模式2：读文件→改代码→执行
# [L7.7] 目标词表并入常见扩展名：用户只说"读取 config.yaml"也认
_B1_RE = re.compile(r"(读取|读一遍|读一下|读|打开|看看)[^。；;]{0,30}?"
                    r"(文件|代码|脚本|源码|程序|日志|\.py|\.js|\.ts|\.yaml|\.yml|\.json|\.md|\.txt|\.log|\.bat|\.sh|\.csv|\.ini|\.cfg)")
_B2_RE = re.compile(r"(修改|修复|重构|优化|改一下|改)[^。；;]{0,20}?(代码|脚本|函数|逻辑|bug)")
_B3_RE = re.compile(r"(运行|执行|跑一下|跑一遍|跑|试跑|测试一下|测试)(?![^。；;]{0,6}(代码|文件))")


def _chain_read_fix_run(goal: str) -> Optional[List[Dict[str, Any]]]:
    # [L7.7 修正] 先锚定"改代码"子句（它常含 文件/代码 等词，会被读句的懒惰匹配吞掉），
    # 再在锚点之前找"读文件"、之后找"执行"，三段各归各句。
    m2 = _B2_RE.search(goal)
    if not m2:
        return None
    m1 = _B1_RE.search(goal[:m2.start()])
    if not m1:
        return None
    m3 = _B3_RE.search(goal[m2.end():])
    if not m3:
        return None
    path = _extract_path(goal[:m2.start()]) or _extract_path(goal)
    d2 = goal[m2.start():m2.end()].strip(" ，,。；;")
    d3 = goal[m2.end() + m3.start():].strip(" ，,。；;") or "执行修改后的代码"
    read_desc = _strip_conj(goal[:m2.start()]) or "读取目标文件"
    return [
        _step(1, read_desc, "file", {"op": "read", "path": path}),
        _step(2, d2, "zcode", {"code": "按需求修改代码，输出修改后的完整代码（不要解释）。\n"
                                       "需求：%s\n原代码：\n{prev_output}" % d2}),
        _step(3, d3, "zcode", {"code": "执行以下脚本并输出运行结果：\n{prev_output}"}),
    ]


# 模式3：截图识别→分析→生成修复代码
_C1_RE = re.compile(r"(截图|图片|照片|报错图|屏幕|界面)[^。；;]{0,12}?(识别|读取|读一下|读|看看|看|ocr)"
                    r"|(?:识别|读取)[^。；;]{0,8}?(截图|图片|照片|报错图|扫描件)")
_C2_RE = re.compile(r"(分析|原因|为什么|怎么回事|诊断)")
_C3_RE = re.compile(r"(生成|写|修复|给出|输出)[^。；;]{0,10}?(修复代码|代码|补丁)")


def _chain_shot_analyze_fix(goal: str) -> Optional[List[Dict[str, Any]]]:
    m1 = _C1_RE.search(goal)
    if not m1:
        return None
    m2 = _C2_RE.search(goal, m1.end())
    if not m2:
        return None
    m3 = _C3_RE.search(goal, m2.end())
    if not m3:
        return None
    img = _extract_path(goal[:m2.start()])
    head = _strip_conj(goal[:m2.start()])
    iris_inp = {"image_path": img} if img and re.search(
        r"\.(png|jpg|jpeg|gif|bmp|webp|pdf)$", img, re.I) else {"text": head or goal}
    return [
        _step(1, head or "识别截图内容", "iris", iris_inp),
        _step(2, _strip_conj(goal[m2.start():m3.start()]) or "分析报错原因", "zcode",
              {"code": "根据以下报错识别结果分析根本原因，输出分析结论：\n{prev_output}"}),
        _step(3, goal[m3.start():].strip(" ，,。；;") or "生成修复代码", "zcode",
              {"code": "根据以下分析结论生成修复代码，输出完整可运行代码：\n{prev_output}"}),
    ]


# [修复 2026-09-30] 增量文件修改模式："在 <path> 末尾添加一行注释 <内容>" 类指令。
# 原分句逻辑会把整句指令当 file write 的 content（覆盖式清空原文件）；
# 本模式改为两步：file read（取原文）→ file write（{prev_output} + 新增内容追加写回），
# 复用 executor 的 {prev_output} 串联机制，实现真正的"追加"而非覆盖。
_APPEND_TRIGGER_RE = re.compile(
    r"(?:在|往|到|对|把)?\s*(?:文件|脚本|代码)?\s*(?:末尾|文件末尾|最后|结尾|行尾|尾部)?\s*"
    r"(?:添加|追加|插入|加上|加)"
    r"(?:一行|一个|条)?\s*(?:注释|代码|内容|文字|说明)?\s*[:：]?\s*(?P<content>.*?)\s*$")


def _chain_append_file(goal: str) -> Optional[List[Dict[str, Any]]]:
    """命中"往已有文件追加内容"→ 两步 read+write；未命中返回 None（回落原逻辑）。"""
    m = _APPEND_TRIGGER_RE.search(goal)
    if not m:
        return None
    path = _extract_path(goal)
    if not path:
        return None
    content = (m.group("content") or "").strip()
    # [2026-09-30 加固] 清洗杂散标点/引号残留（顿号、半角引号等），避免把
    # "# 界面验证"、"、 这类垃圾写进文件
    content = content.strip(" ，,。；;、\"'“”‘’·—–")
    content = content.rstrip(" ，,。；;、\"'“”‘’")
    # 无明确内容（用户只说"加一行注释"没给注释文本）→ 放弃追加链，
    # 尤其点名 zcode 时交给 zcode CLI（Agent 形态）自己处理
    if not content or len(content) > 500:
        return None
    if path in content:                      # 内容里残留路径 → 解析异常，放弃本模式
        return None
    # 纯标点/无有效文字 → 视为无内容，放弃链
    if not re.search(r"[\u4e00-\u9fffA-Za-z0-9_+#@]|/\\|\.|\(|\)", content):
        return None
    return [
        _step(1, "读取目标文件以确认内容", "file", {"op": "read", "path": path}),
        _step(2, "在文件末尾添加内容并写回", "file",
              {"op": "write", "path": path, "content": "{prev_output}\n%s" % content}),
    ]


_CHAINS = (_chain_append_file, _chain_research_write, _chain_read_fix_run, _chain_shot_analyze_fix)


def _match_chain(goal: str) -> Optional[List[Dict[str, Any]]]:
    for fn in _CHAINS:
        try:
            steps = fn(goal)
        except Exception:  # noqa: BLE001 - 复合模式误判绝不允许打断拆解，回落分句即可
            steps = None
        if steps:
            return steps
    return None
# ===================== [L7.7 复合模式结束] =====================


def _rule_plan(goal: str) -> List[Dict[str, Any]]:
    # [L7.7] 先试复合模式（整体语义串联），未命中再分句。
    chain = _match_chain(goal)
    if chain:
        return chain
    clauses = [c.strip(" ，,、") for c in _SPLIT_RE.split(goal) if c and c.strip(" ，,、")]
    if not clauses:
        clauses = [goal.strip()]
    steps: List[Dict[str, Any]] = []
    for desc in clauses:
        tool = _classify(desc)
        # [L7.7] 无信号子句（"写得好看点"这类虚词续句）并入前一步描述，不再误拆成检索步骤
        if tool is None and steps:
            steps[-1]["description"] = (steps[-1]["description"] + "，" + desc)[:200]
            continue
        # [2026-10-27 寒暄闸] 首子句为寒暄/极短非任务文本（"你好，帮我查xx"的"你好"）
        # → 直接跳过，绝不落三级兜底（那是"搜索[你好]"事故的源头）。若全部子句都是
        # 寒暄则 steps 为空——由 plan() 入口寒暄闸兜住，此处只可能出现在混合句。
        if tool is None and _is_casual_text(desc):
            continue
        if tool is None:      # 首子句且完全无信号 → 三级兜底（从具体到宽泛）
            # [2026-10-01 U-4] 三级兜底（原：有代码特征→zcode，否则→argus）
            # 问题：无信号的"创建 xxx"类任务被兜底给 argus 搜索，用户看到百度结果
            # 新策略：优先判"操作类动词"，再判代码特征，最后才用搜索
            if _ACTION_HINT_RE.search(desc):
                tool = "zcode"        # 一级：操作类动词 → 默认走 zcode（编程/生成）
            elif re.search(_ZCODE_CODE_HINT_RE, desc):
                tool = "zcode"        # 二级：代码特征（print(...)/import/def/= 等）→ zcode
            else:
                tool = "argus"        # 三级：以上都无 → 搜索（保底，优先级最低）
        steps.append(_step(len(steps) + 1, desc, tool, _build_input(tool, desc)))
    return steps


# ===================== [2026-10-24 规划层内部知识剥夺·纯加性] =====================
# 背景（现场实锤）：Agent 自动路由到 Laya 时完全绕过 moray llm_proxy 的 tools 物理剥夺，
# 任务原句被 _build_input 塞进 argus 直搜百度，搜回"我"的百科。正确拦截面在规划层：
# 命中内部知识库的 argus 步骤**原位改写为 decision 步**（不生成 argus 外呼），把查到的
# 内部知识文本作为上下文拼进 decision 的生成 Prompt（executor 借 self_check._llm_json
# 本地执行，该步输出即最终答案/供 {prev_output} 串联）。绝不伪造搜索结果：查不到或
# 失败时 argus 步骤原样保留，外呼与否交回原链路判定。fail-open 铁律：8000 不可达/
# 超时/任何异常 → 原计划零改动。
# 查库端点 = moray POST /api/twin/recall（corpus 专属白名单，与 _memory_recall_sync
# 所用的 /api/memory/recall 结构互斥——记忆召回查不到分身语料，故只镜像其封装形态）。
# hybrid recall 实测 ~3.3s（embedding 走 Ollama 占大头）→ 超时 6s（env 可调）。
_TWIN_GATE_TIMEOUT = float(os.environ.get("LAYA_TWIN_GATE_TIMEOUT", "6"))
_TWIN_GATE_LOG = logging.getLogger("laya.twin_gate")


def _twin_recall_sync(query: str) -> Optional[List[Dict[str, Any]]]:
    """同步查 moray 内部知识库（/api/twin/recall）。命中返回非空 items，否则 None。

    形态镜像 laya_std_server._memory_recall_sync（同步 httpx + MORAY_BASE + fail-open），
    端点换成 twin/recall：corpus 白名单专属，记忆召回接口覆盖不到。
    ⚠limit 必须取端点上限 50 而非 1：hybrid 的向量通道 KNN k=max(limit*3,15) 是
    **全库混搜**（corpus 仅占 memory_bank 的 4/162），limit=1 时 k=15，corpus 块
    极易被大量 task_result 等非语料行挤出榜（实测"我的项目红队拦截率" limit=1/8/16
    全空、50 才命中 2 条）——闸门判"非空即剥夺"，limit 拍死会让 vec 通道结构性失明。
    """
    try:
        if not str(query or "").strip():
            return None
        base = str(getattr(_tool_registry, "MORAY_BASE",
                           "http://127.0.0.1:8000")).rstrip("/")
        with httpx.Client(timeout=_TWIN_GATE_TIMEOUT) as client:
            resp = client.post(base + "/api/twin/recall",
                               json={"query": str(query).strip(),
                                     "limit": 50, "mode": "hybrid"})
        if resp.status_code != 200:
            _TWIN_GATE_LOG.warning("[Laya-twin-gate] /api/twin/recall 非 200：status=%s body=%s",
                                   resp.status_code, resp.text[:200])
            return None
        body = resp.json() or {}
        data = body.get("data")
        items = data.get("items") if isinstance(data, dict) else None
        return items or None
    except Exception as e:  # noqa: BLE001 - fail-open：查库失败绝不阻断规划
        _TWIN_GATE_LOG.warning("[Laya-twin-gate] 查库失败（fail-open）：%s: %s",
                               type(e).__name__, str(e)[:200])
        return None


def _substitute_argus_with_knowledge(steps: List[Dict[str, Any]],
                                     goal: str) -> List[Dict[str, Any]]:
    """计划后处理：argus 步骤逐个查内部知识库，命中则原位改写为 decision 步。

    - 不删除步骤：多步链的 {prev_output} 串联不断，decision 的输出像 argus 结果
      一样流入后续步骤——数据来源从"外呼搜索"换成"内部知识"。
    - decision input：system 钉死 JSON 输出契约；user = 目标 + 内部知识 + 硬约束
      （只依据内部知识、不编造、知识未覆盖就如实说明）。
    - description 追加"已命中内部知识，跳过外部搜索"，SSE 消费方/用户可见。"""
    for step in steps:
        if not isinstance(step, dict) or str(step.get("tool") or "").lower() != "argus":
            continue
        inp = step.get("input") if isinstance(step.get("input"), dict) else {}
        query = str(inp.get("query") or inp.get("keyword") or goal or "").strip()
        items = _twin_recall_sync(query)
        if not items:
            continue
        # 防炸：知识块最多取前 8 条、单条截 800 字符（corpus 块本身 ≤1000 字符，
        # 常态命中 1~4 条；decision 的 user prompt 有总量约束）
        knowledge = "\n".join(
            "[%d] %s（来源：%s）" % (i, str((it or {}).get("content") or "").strip()[:800],
                                    "/".join(str(t) for t in ((it or {}).get("tags") or [])))
            for i, it in enumerate(items[:8], 1))
        step["description"] = (str(step.get("description") or "")[:180]
                               + "（已命中内部知识，跳过外部搜索）")
        step["tool"] = "decision"
        step["input"] = {
            "system": "你是任务执行引擎。只依据用户提供的【内部知识】完成任务目标，"
                      "输出纯JSON对象 {\"answer\": \"...\"}，不要解释、不要代码块标记。"
                      "内部知识不足以回答时，answer 如实说明知识库未覆盖，绝不编造、绝不外呼。",
            "user": "任务目标：%s\n\n【内部知识】（来自本地知识库，已跳过外部搜索）：\n%s\n\n"
                    "请只依据上述内部知识完成任务目标，输出 JSON。"
                    % (str(goal or step.get("description") or "").strip(), knowledge),
        }
        step["expected_output"] = "依据内部知识直接给出答案（未外呼搜索）"
        _TWIN_GATE_LOG.warning(
            "[Laya-twin-gate] 命中内部知识，argus 步骤已改写为 decision，query=%s",
            query[:120])
    return steps


async def _gate_steps(steps: List[Dict[str, Any]], goal: str) -> List[Dict[str, Any]]:
    """三路计划（pattern/llm/rule）统一过内部知识闸。查库含网络 IO，扔线程池执行，
    不卡 /execute 的 SSE 事件循环；无 argus 步骤时零开销直通。"""
    if not steps or not any(isinstance(s, dict) and str(s.get("tool") or "").lower() == "argus"
                            for s in steps):
        return steps
    return await asyncio.to_thread(_substitute_argus_with_knowledge, steps, goal)
# ===================== [2026-10-24 规划层内部知识剥夺结束] =====================


def _validate_steps(steps: Any) -> List[Dict[str, Any]] | None:
    """形状严校（供 LLM 拆解结果把关）：不符返回 None → 回落规则拆解。"""
    if not isinstance(steps, list) or not steps:
        return None
    out = []
    for i, s in enumerate(steps, 1):
        if not isinstance(s, dict):
            return None
        tool = str(s.get("tool") or "").lower()
        if tool not in _TOOLS:
            return None
        inp = s.get("input")
        default_exp = _EXPECT["file"].get(str((inp or {}).get("op") if isinstance(inp, dict) else ""),
                                          _EXPECT["file"]["read"]) if tool == "file" else _EXPECT[tool]
        out.append({"step_id": int(s.get("step_id") or i), "description": str(s.get("description") or ""),
                    "tool": tool, "input": inp if isinstance(inp, dict) else {},
                    "expected_output": str(s.get("expected_output") or default_exp)})
    return out


# ===================== [2026-10-27 寒暄闸·纯加性] =====================
# 背景（现场实锤事故）："你好"被三级兜底打包成 argus 搜索步骤、真去外呼百度搜回
# 无意义结果。寒暄/极短非任务文本在规划层就地拦截：不生成任何工具步骤（尤其 argus），
# 由 /execute 短路成 success 帧把 reply 当普通对话回复——体验上等同纯聊天。
# 与前端 92 寒暄闸（decideMode 前置白名单）构成双保险：前端拦交互路径，本闸拦
# /execute 的一切直调方（自驱卡片、Laya 面板、API 调用）。
_GREETING_TOKEN_RE = re.compile(
    r"你好|您好|嗨|哈喽|哈罗|hello|hi+|hey|yo|早安|早上好|中午好|下午好|晚上好|晚安"
    r"|在吗|在不在|在么|谢谢|多谢|感谢|再见|拜拜|辛苦了|okay?|嗯+|哦+|好的|收到|行|可以",
    re.IGNORECASE)
_RESIDUE_STRIP_RE = re.compile(r"[\s!！~～。．,.，;；:：?？!！()（）\[\]【】\"'“”]+")
# 任务信号（动词/技术词）：命中即说明"极短文本"仍可能是真任务（"改bug""查价格"），
# 绝不能被寒暄闸吞掉。
_TASK_HINT_RE = re.compile(
    r"写|做|搞|弄|生成|创建|新建|构建|开发|修改|改|修复|修|优化|重构|调整|完善"
    r"|分析|查|搜|找|调研|检索|搜索|统计|汇总|整理|计算|对比|评估|测试|调试|排查"
    r"|运行|执行|跑|启动|部署|发布|编译|安装|下载|导出|导入|读取|写入|删除|保存"
    r"|代码|脚本|程序|函数|模块|文件|目录|文件夹|数据|数据库|接口|api|日志|报错"
    r"|错误|bug|报告|方案|文档|页面|组件|配置|环境|服务器|命令|任务|流程|架构"
    r"|python|javascript|java|sql|价格|多少钱|资讯|新闻|政策|行情|趋势",
    re.IGNORECASE)

_GREETING_REPLY = "你好呀！我在呢，有什么需要帮忙的吗？"
_NEUTRAL_CASUAL_REPLY = ("这条消息看起来是闲聊，没有可执行的任务步骤；"
                         "如果你想让我查询资料或执行操作，直接描述目标即可。")

# ---- [2026-10-27 寒暄闸 v2·前缀判据] 寒暄开头网（与前端 92 同款） ----
# 中文寒暄词后不设限（允许"moray"/英文/语气词后缀）；英文寒暄词须以非字母收尾，
# 防 hi→history 这类词前缀误伤。点名式寒暄先剥 wake 词（"moray晚上好呀"）。
_GREETING_CN_PREFIX_RE = re.compile(
    r"^(?:你好|您好|嗨|哈喽|哈罗|早安|早上好|中午好|下午好|晚上好|晚安"
    r"|在吗|在不在|在么|谢谢|多谢|感谢|再见|拜拜|辛苦了|嗯+|哦+|好的|收到|行|可以)")
_GREETING_EN_PREFIX_RE = re.compile(r"^(?:hello|hi+|hey|yo)(?![a-z])", re.IGNORECASE)
_WAKE_PREFIX_RE = re.compile(r"^(?:moray)\s*[，,!！~～。．,.:：、\s]*", re.IGNORECASE)


def _is_greeting_opening(text: str) -> bool:
    """以寒暄词开头 **且全文零任务动作词** → 寒暄文本（真任务永不命中）。

    与前端 92 的 hasGreetingPrefix + TASK_HINT 判据保持同构，前后端双保险。
    """
    t = str(text or "").strip()
    if not t:
        return False
    if _TASK_HINT_RE.search(t) is not None:
        return False                      # "晚上好，帮我查价格" → 真任务，放行

    def _hit(s: str) -> bool:
        return bool(_GREETING_CN_PREFIX_RE.match(s) or _GREETING_EN_PREFIX_RE.match(s))

    if _hit(t):
        return True
    stripped = _WAKE_PREFIX_RE.sub("", t, count=1)
    return stripped != t and _hit(stripped)


def _is_casual_text(text: str) -> bool:
    """寒暄/极短非任务文本判定（真值=可作纯对话处理，绝不外呼工具）。

    1) 剥掉全部寒暄词与语气/标点后无剩余 → 纯寒暄（"你好""hi!! 在吗""嗯。"）；
    2) **寒暄开头网（前缀判据，2026-10-27 二次扩网）**：以寒暄词开头且全文零任务
       动作词 → 纯聊天（"晚上好呀moray""hi moray""moray晚上好"——原 1) 是整句剥词
       判据，后缀挂英文/语气词即漏网，实锤事故"晚上好呀moray"被拆去 argus 搜百度）；
    3) 极短且零任务信号（<5 字、无任何动词/技术词）→ 非任务文本。
    "写个脚本""改bug""查价格""晚上好，帮我查价格"等含任务信号的句子永不命中——
    保真任务零误伤。
    """
    t = str(text or "").strip()
    if not t:
        return False
    residue = _GREETING_TOKEN_RE.sub("", t)
    if not _RESIDUE_STRIP_RE.sub("", residue):
        return True
    if _is_greeting_opening(t):
        return True
    return len(t) < 5 and _TASK_HINT_RE.search(t) is None


async def plan(goal: str) -> Dict[str, Any]:
    """入口：返回 {"steps": [...], "mode": "rule"|"llm"}。规则拆解恒作保底。"""
    goal = str(goal or "").strip()
    if not goal:
        return {"steps": [], "mode": "rule", "note": "goal 为空"}
    # ---- [2026-10-27 寒暄闸 v2·最前置] 寒暄开头 + 全文零任务词 → 直接 casual 帧 ----
    # 实锤事故："晚上好呀moray" 经整句剥词闸漏网 → 走到三级兜底 argus → 百度搜"晚上好"。
    # 本分支放在 pattern/LLM/rule 一切拆解之前，杜绝"无信号 → 搜索"的弱智兜底。
    if _is_greeting_opening(goal):
        return {"steps": [], "mode": "casual", "casual": True,
                "reply": _GREETING_REPLY,
                "note": "寒暄开头且零任务词（v2 前缀闸），最前置拦截，严禁外呼 argus 等工具"}
    # ---- [2026-10-27 寒暄闸] 整句寒暄/极短非任务文本 → 零工具步骤 + 对话式 reply ----
    if _is_casual_text(goal):
        return {"steps": [], "mode": "casual", "casual": True,
                "reply": _GREETING_REPLY if _GREETING_TOKEN_RE.search(goal)
                         else _NEUTRAL_CASUAL_REPLY,
                "note": "寒暄/极短非任务文本，规划层拦截，严禁外呼 argus 等工具"}
    # ---- [L7.8 入口加判断·纯加性] 工程范式优先：命中即返回多步计划，未命中原逻辑一行不动 ----
    _pattern_steps = _match_task_pattern(goal)
    if _pattern_steps is not None:
        return {"steps": await _gate_steps(_pattern_steps, goal), "mode": "pattern"}
    # ---- [L7.8 分支结束；未命中完全走下方原有 LLM/规则拆解] ----
    if os.environ.get("LAYA_PLANNER_LLM", "0") != "0":
        user = json.dumps({"目标": goal, "可用工具": {
            "iris": "看图/读截图/识别报错", "argus": "查资料/搜信息/行业调研",
            "zcode": "写代码/跑脚本/代码执行", "file": "读文件/写文件/遍历目录(read/write/list_dir)"}},
            ensure_ascii=False)
        llm = await _sc._llm_json(
            "你是任务规划器。把目标拆解为有序步骤，只输出纯JSON数组，每元素含 "
            "step_id(int)/description/tool(iris|argus|zcode|file)/input(对象)/expected_output。"
            "不解释、不用代码块。"
            # [2026-10-27 寒暄死命令] 严禁小模型把寒暄拆成外呼工具步骤（实锤事故：
            # "你好"被拆出 argus 搜索步）。空数组交由规则层，规划层寒暄闸另行拦截。
            "死命令：如果任务目标仅为寒暄、打招呼或极短的非任务文本，直接输出空数组 []，"
            "严禁生成任何外呼工具（尤其 argus 搜索）步骤。", user)
        steps = _validate_steps(llm) if isinstance(llm, list) else None
        if steps:
            return {"steps": await _gate_steps(steps, goal), "mode": "llm"}
    rule_steps = _rule_plan(goal)
    if not rule_steps:
        # [2026-10-27 寒暄闸] 混合句所有子句均被寒暄闸跳过 → 不落 executor 空步骤
        # 红卡（"计划为空"failed 帧），改走对话式 reply（/execute 短路消费）。
        return {"steps": [], "mode": "casual", "casual": True,
                "reply": _NEUTRAL_CASUAL_REPLY, "note": "全部子句为寒暄，规划层拦截"}
    return {"steps": await _gate_steps(rule_steps, goal), "mode": "rule"}


# ===================== [新增代码·开始] L7.8 三种标准工程任务范式（2026-09-29，纯加性） =====================
# 本节只向下追加，上方原有分句/四工具识别/L7.7 复合链/LLM 增强逻辑一行未改未删；
# 对原函数的唯一触达 = plan() 入口加的"工程范式优先"if 分支（已就地标注 [L7.8 入口加判断]）。
# 零新增依赖：复用本文件既有 re/json/os 导入；目标工具对接 tool_registry 已注册的 zcode.* 原子工具。
# 接口兼容：命中范式产出的步骤仍是标准五字段 step_id/description/tool/input/expected_output；
#   /planner 返回键集合不变，mode 仅多一个取值 "pattern"（取值扩展，非新增必填字段）。
# 步骤工具命名：file_list/file_read/file_write/file_edit/cmd_exec/port_check/process_kill
#   （executor 多步链路负责映射到注册表 zcode.* 全名）；decision 为"模型决策步"，
#   由 executor 借 self_check._llm_json 本地执行（任何异常降级为失败信息，不打断流程）。
# 占位符约定：
#   {target_path}/{target_dir}/{run_cmd}/{verify_cmd}/{goal} —— 已知参数由 _match_task_pattern
#     在规划期渲染填充；未知的保留原样，由 executor 多步链路做上下文注入兜底；
#   {prev_output}/{step_N_output}/{step_N_output.属性} —— 规划期一律保留，executor 运行期串联。

# ---- 参数抽取小工具（全部新函数，不触碰原有 _extract_path/_file_op 等） ----

_DIR_PRE_RE = re.compile(r"([A-Za-z0-9_.:\\/\-]{2,60})\s*(?:目录|文件夹)")
_DIR_POST_RE = re.compile(r"(?:目录|文件夹)\s*[是为：:]?\s*([A-Za-z0-9_.:\\/\-]{2,60})")
_CMD_MENTION_RE = re.compile(r"命令[是为：:]?\s*[\"'`]?([^\"'`；;。\n]{2,80})")
_RUN_MENTION_RE = re.compile(
    r"(?:运行|执行|跑)(?:一下|下|命令|脚本)?\s*[\"'`]?([A-Za-z0-9_.:/\\\\+\- ]{2,60}?)"
    r"(?=\s*(?:看|试试|验证|报错|出错|异常|检查|结果|[，,。；;]|$))")
_EXT_RE = re.compile(r"\.([A-Za-z0-9]{1,7})$")


def _extract_dir(text: str) -> str:
    """提取目标目录：'xxx目录'/'目录xxx' 邻近词 → 盘符路径（无扩展名） → 兜底 '.'（=工程根）。"""
    m = _DIR_PRE_RE.search(text)
    if m and ("\\" in m.group(1) or "/" in m.group(1) or ":" in m.group(1)
              or not _EXT_RE.search(m.group(1))):
        return m.group(1).strip("\\/:. ") or "."
    m = _DIR_POST_RE.search(text)
    if m:
        return m.group(1).strip("\\/:. ") or "."
    p = _extract_path(text)
    if p and not _EXT_RE.search(p):
        return p
    return "."


def _derive_run_cmd(text: str, path: str) -> str:
    """推导可执行运行命令：显式"命令：xxx" → "运行/执行 xxx" 邻近串 → 按目标文件扩展名合成。"""
    m = _CMD_MENTION_RE.search(text)
    if m and m.group(1).strip():
        return m.group(1).strip()
    m = _RUN_MENTION_RE.search(text)
    if m:
        cand = m.group(1).strip(" \"'`")
        low = cand.lower()
        if cand and not cand.startswith("{"):
            if low.startswith(("python", "py ", "node", "npm", "npx", "pytest", "bash",
                               "powershell", "go ", "cargo", "make", "java", "cmd ", "dir")):
                return cand
            if low.endswith((".py", ".pyw")):
                return "python %s" % cand
            if low.endswith((".js", ".mjs")):
                return "node %s" % cand
            return cand
    if path:
        low = path.lower()
        if low.endswith((".py", ".pyw")):
            return 'python "%s"' % path
        if low.endswith(".js"):
            return 'node "%s"' % path
        if low.endswith((".bat", ".cmd", ".ps1", ".sh")):
            return '"%s"' % path
    return ""


def _derive_verify_cmd(path: str) -> str:
    """按目标文件扩展名合成语法/完整性校验命令（未知类型退回可读性检查，保证第4步恒在）。"""
    m = _EXT_RE.search(path or "")
    ext = (m.group(1).lower() if m else "")
    if ext in ("py", "pyw"):
        return 'python -m py_compile "%s"' % path
    if ext in ("js", "mjs", "cjs"):
        return 'node --check "%s"' % path
    if ext == "json":
        return 'python -m json.tool "%s"' % path
    if ext in ("yaml", "yml"):
        return ('python -c "import yaml,sys;yaml.safe_load(open(sys.argv[1],'
                "encoding='utf-8'))\" \"%s\"" % path)
    return 'type "%s" >nul' % path


# ---- 范式参数装配：返回 known 字典；None = 参数不足以安全成链 → 不命中、回落原有逻辑 ----

def _params_batch(text: str) -> Optional[Dict[str, str]]:
    target_dir = _extract_dir(text)
    return {"target_dir": target_dir, "goal": text,
            "verify_cmd": 'dir /b "%s"' % target_dir}


def _params_debug(text: str) -> Optional[Dict[str, str]]:
    path = _extract_path(text)
    run_cmd = _derive_run_cmd(text, path)
    if not run_cmd:      # 无可运行目标（既无路径也无命令）→ 六步链无法闭环，交回原有拆解
        return None
    return {"target_path": path, "target_dir": _extract_dir(text),
            "run_cmd": run_cmd, "goal": text}


def _params_code_fix(text: str) -> Optional[Dict[str, str]]:
    path = _extract_path(text)
    if not path:         # 无明确目标文件 → 不命中（宁可不接管，不误伤单工具任务）
        return None
    return {"target_path": path, "goal": text, "verify_cmd": _derive_verify_cmd(path)}


# ---- 范式模板：steps 为模板（描述含占位符）；step_id 由渲染器按序补齐 ----
# 决策步 system/user 均按 executor 约定组装：user 内嵌需求与串联占位符，
# 并要求模型只回纯 JSON（字段名与后续步骤引用的 {step_N_output.属性} 对齐）。

TASK_PATTERNS: Dict[str, Dict[str, Any]] = {
    "batch": {
        "name": "文件批量处理型",
        "priority": 1,   # 最特异，最先尝试
        "trigger": re.compile(
            r"批量(?:修改|替换|重命名|转换|处理|整理|添加|生成)"
            r"|遍历(?:目录|文件夹|所有|全部)"
            r"|逐个(?:修改|替换|转换|处理|重命名)"),
        "params": _params_batch,
        "steps": [
            {"description": "遍历{target_dir}列出目标文件", "tool": "file_list",
             "input": {"path": "{target_dir}"},
             "expected_output": "拿到目标目录下的文件清单"},
            {"description": "逐个读取待处理文件", "tool": "file_read",
             "input": {"path": "{step_1_output.files.0.path}"},
             "expected_output": "读取到首个待处理文件的实际内容"},
            {"description": "生成批量处理方案", "tool": "decision",
             "input": {
                 "system": "你是文件批量处理决策引擎。基于清单与样例内容给出批量处理方案。"
                           "只输出纯JSON对象，不要解释、不要代码块。"
                           "fixed_content 的值必须是目标文件的完整裸文本内容本身："
                           "禁止用 ``` 围栏或任何 markdown 包裹，禁止解释、注释、前后缀。",
                 "user": "目标：{goal}\n文件清单：{step_1_output}\n样例文件内容：{prev_output}\n"
                         '输出JSON：{"file_path": "待写入文件路径", '
                         '"fixed_content": "处理后的完整文件内容（裸文本）", '
                         '"verify_cmd": "批量校验命令（裸命令行，无 markdown 围栏）", '
                         '"expected_exit_code": "校验命令的期望退出码（整数，通常为 0）"}'},
             "expected_output": "给出批量处理方案（目标文件+写入内容+校验命令）"},
            {"description": "批量写入处理结果", "tool": "file_edit",
             "input": {"path": "{step_3_output.file_path}",
                       "content": "{step_3_output.fixed_content}"},
             "expected_output": "批量处理结果已写回目标文件"},
            {"description": "批量处理结果验证", "tool": "cmd_exec",
             "input": {"command": "{step_3_output.verify_cmd}"},
             "expected_output": "校验命令成功执行，处理结果可复核",
             "expected_exit_code": 0},
        ],
    },
    "debug": {
        # [promptfix 2026-10-03] 步序调整：file_read 提前到 decision 之前（与 batch/code_fix
        # 两范式的"读先行"对齐）——decision 要产出"完整文件重写"就必须看到原文件全文；
        # 原步序 decision 排在 read 之前只能凭报错盲猜，>20 行文件必然写丢骨架
        # （2026-10-03 e2e 实证：首轮盲写 13 行片段覆盖 38 行原文）。
        "name": "问题排查型",
        "priority": 2,
        "trigger": re.compile(
            r"排查|调试|定位问题|找\s*bug|查\s*bug"
            r"|(?:运行|执行|跑)[^。；;]{0,30}?(?:报错|出错|异常|失败|不起来)"
            r"|(?:修复|解决)[^。；;]{0,30}?(?:问题|报错|异常|错误|bug)"),
        "params": _params_debug,
        "steps": [
            {"description": "遍历{target_dir}锁定相关文件", "tool": "file_list",
             "input": {"path": "{target_dir}"},
             "expected_output": "拿到工程目录结构，圈定嫌疑文件"},
            {"description": "读取问题文件内容", "tool": "file_read",
             "input": {"path": "{target_path}"},
             "expected_output": "读取问题文件当前完整内容，供决策步全文重写"},
            {"description": "运行并捕获报错", "tool": "cmd_exec",
             "input": {"command": "{run_cmd}"},
             "expected_output": "复现运行并捕获 stdout/stderr 报错信息"},
            {"description": "分析报错根因", "tool": "decision",
             "input": {
                 "system": "你是问题排查决策引擎。基于运行报错与目录清单定位根因并给出修复内容。"
                           "只输出纯JSON对象，不要解释、不要代码块。"
                           "fixed_content 的值必须是目标文件的完整裸文本内容本身："
                           "禁止用 ``` 围栏或任何 markdown 包裹，禁止解释、注释、前后缀。"
                           "analysis 才是说明文字。修复必须让程序恢复正常运行（重跑退出码 0），"
                           "不要用抛异常代替修复。",
                 "user": "目标：{goal}\n运行输出：{prev_output}\n目录清单：{step_1_output}\n"
                         '输出JSON：{"problem_path": "问题文件路径", '
                         '"analysis": "根因一句话", '
                         '"fixed_content": "修复后的完整文件内容（裸文本）", '
                         '"expected_exit_code": "修复后重新运行的期望退出码（整数，通常为 0）"}'},
             "expected_output": "给出根因分析、问题文件路径与修复后完整内容"},
            {"description": "写入修复内容", "tool": "file_edit",
             "input": {"path": "{target_path}", "content": "{step_4_output.fixed_content}"},
             "expected_output": "修复内容已写回问题文件"},
            {"description": "重新运行验证结果", "tool": "cmd_exec",
             "input": {"command": "{run_cmd}"},
             "expected_output": "重新运行：退出码 0、无报错输出",
             "expected_exit_code": 0},
        ],
    },
    "code_fix": {
        "name": "代码修改型",
        "priority": 3,   # 触发词最泛（修改/改/新增/添加/优化/修复…），放最后兜住前两者未收的
        "trigger": re.compile(
            r"(?:修改|改动|优化|新增|添加|增加|修复|调整|改|给)[^。；;！？]{0,40}?"
            r"(?:文件|代码|脚本|源码|函数|方法|接口|逻辑|配置|模块|页面|功能"
            r"|\.(?:py|pyw|js|mjs|ts|tsx|java|go|css|html|bat|cmd|sh|ps1|json|ya?ml|md|txt|csv|ini|cfg|toml|xml))"),
        "params": _params_code_fix,
        "steps": [
            {"description": "读取目标文件", "tool": "file_read",
             "input": {"path": "{target_path}"},
             "expected_output": "读取到目标文件完整内容"},
            {"description": "按需求生成修改方案", "tool": "decision",
             "input": {
                 "system": "你是代码修改决策引擎。基于原文件内容与需求产出修改后的完整文件内容。"
                           "只输出纯JSON对象，不要解释、不要代码块。"
                           "fixed_content 的值必须是目标文件的完整裸文本内容本身："
                           "禁止用 ``` 围栏或任何 markdown 包裹，禁止解释、注释、前后缀。"
                           "summary 才是说明文字。",
                 "user": "需求：{goal}\n当前文件内容：{prev_output}\n"
                         '输出JSON：{"fixed_content": "修改后的完整文件内容（裸文本）", '
                         '"summary": "改动一句话说明", '
                         '"expected_exit_code": "语法校验命令的期望退出码（整数，通常为 0）"}'},
             "expected_output": "产出修改后的完整文件内容与改动说明"},
            {"description": "增量修改写回文件", "tool": "file_edit",
             "input": {"path": "{target_path}", "content": "{step_2_output.fixed_content}"},
             "expected_output": "修改已写回目标文件"},
            {"description": "语法校验", "tool": "cmd_exec",
             "input": {"command": "{verify_cmd}"},
             "expected_output": "语法校验通过，退出码 0",
             "expected_exit_code": 0},
        ],
    },
}


def _render_str(text: Any, known: Dict[str, str]) -> Any:
    """只填 known 里的 {token}；未知 token 原样保留，交给 executor 上下文注入兜底。"""
    if not isinstance(text, str) or "{" not in text:
        return text
    for key, val in known.items():
        if val:
            text = text.replace("{%s}" % key, str(val))
    return text


def _render_pat_steps(template_steps: List[Dict[str, Any]], known: Dict[str, str]) -> List[Dict[str, Any]]:
    """模板 → 标准五字段步骤列表（字段形状与原有 _step() 完全一致）。"""
    out: List[Dict[str, Any]] = []
    for i, st in enumerate(template_steps, 1):
        out.append({"step_id": i,
                    "description": str(_render_str(st["description"], known))[:200],
                    "tool": st["tool"],
                    "input": {k: _render_str(v, known) for k, v in st["input"].items()},
                    "expected_output": str(_render_str(st["expected_output"], known))})
        # [exit_check 2026-10-03] 模板静态兜底字段透传：末步 cmd_exec 自带期望退出码，
        # decision 输出可覆盖（优先级见 executor 多步链校验处）
        if isinstance(st.get("expected_exit_code"), int) \
                and not isinstance(st["expected_exit_code"], bool):
            out[-1]["expected_exit_code"] = st["expected_exit_code"]
    return out


def _match_task_pattern(task: str) -> Optional[List[Dict[str, Any]]]:
    """三种工程范式识别入口：命中 → 多步步骤列表；未命中 → None（原逻辑照跑）。

    尝试顺序按 priority：批量处理型 → 问题排查型 → 代码修改型（越特异越先）。
    任何异常一律视为未命中——范式误判绝不允许打断原有拆解。
    """
    try:
        text = str(task or "").strip()
        if not text:
            return None
        for key in sorted(TASK_PATTERNS, key=lambda k: TASK_PATTERNS[k]["priority"]):
            pat = TASK_PATTERNS[key]
            if not pat["trigger"].search(text):
                continue
            known = pat["params"](text)
            if not known:
                continue
            # [L7.8 让位护栏] 原有 L7.7 复合链已精确命中（读→改→跑 等三句式）→ 不接管，
            # 完全交回原链路，行为与升级前逐字节一致——范式只收原链路接不住的任务。
            if _match_chain(text) is not None:
                return None
            return _render_pat_steps(pat["steps"], known)
    except Exception:  # noqa: BLE001 - 兜底回落原有单工具/复合链拆解
        return None
    return None


# ---- zcode 原子工具触发词内存扩充（任务书二.2）----
# 只改本模块内存里的 TOOL_KEYWORDS 词表（给 zcode 追加原子动作触发词），
# 严禁触碰 tool_registry.py 注册表文件；幂等：扩展一次后由标志位短路。
_ZCODE_ATOMIC_EXTRA_TRIGGERS = [
    # 命令执行 / 进程 / 端口（沙箱原子工具语义）
    "命令行", "终端命令", "执行终端", "杀进程", "终止进程", "kill进程", "进程",
    "端口", "检测端口", "查端口", "端口占用", "监听端口",
    # 文件级精细操作（不与前位工具词表冲突；'文件/写入'等归 file，不重复收）
    "增量修改", "按行修改", "覆盖写回",
]
_ZCODE_TRIGGERS_EXTENDED = False


def _extend_zcode_triggers() -> None:
    """内存内扩充 zcode 触发词库（覆盖文件读写/命令执行/端口检测等原子动作）。"""
    global _ZCODE_TRIGGERS_EXTENDED
    if _ZCODE_TRIGGERS_EXTENDED:
        return
    for entry in TOOL_KEYWORDS:
        if entry[0] == "zcode":
            existing = set(entry[1])
            entry[1].extend(w for w in _ZCODE_ATOMIC_EXTRA_TRIGGERS if w not in existing)
            break
    _ZCODE_TRIGGERS_EXTENDED = True


_extend_zcode_triggers()  # 模块导入即生效（纯内存操作，无副作用外溢）
# ===================== [新增代码·结束] L7.8 三种标准工程任务范式 =====================
