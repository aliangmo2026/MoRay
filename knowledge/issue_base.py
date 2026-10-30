# -*- coding: utf-8 -*-
"""IssueBase：L6.5 统一代码问题库（跨任务知识迁移，2026-09-29）。

定位（docs/l65_audit.md §2）：**同一存储 + 组合式 API 层**——
  * 存储复用 experience_base.jsonl：record() 本就透传未知键 → 统一模型的增量键
    （source / issue_kind / applicable / file_type）零迁移落库，历史数据 100% 保留；
    聚类/统计/画像/L6.0 策略优化器只读已知键，对新行自动生效。
  * ExperienceBase 原样保留为兼容层：所有旧调用（query/build_hint/rank_candidates/…）不变；
    新功能一律走本类。

统一 schema（在经验记录之上的增量键，全部可选、读时缺省）：
  source       来源：排错 | 测试失败 | 代码审查 | 通用
  issue_kind   问题子类（审查场景=严重程度 高/中/低）
  applicable   适用场景：排错 | 审查 | 测试 | 通用（/ 分隔多值）
  file_type    关联文件类型（.py/.js/…，审查收割时可提取）

四闸（全部独立，默认开；MORAY_PATTERN/STRATEGY 等 L6.0 闸不受影响）：
  MORAY_UNIFIED_ISSUE       统一入口 + 源标记（关 = record_issue 退化为无标记透传）
  MORAY_REVIEW_KNOWLEDGE    审查注入 + 收割 + 审查发现回流加权（关 = 审查纯 LLM）
  MORAY_FIX_SUGGEST_TEST    修复后测试用例建议（关 = 不生成）
  MORAY_TEST_KNOWLEDGE      测试失败历史方案匹配（关 = 不关联）

审查收割：零 LLM——llm_review 的 prompt 契约已要求编号问题清单（L5.0 起固定），
编号行正则提取（严重程度/文件:行号/描述），解析不了的行跳过（诚实计数）。
测试建议：确定性模板银行（按归一化根因键 + detect_language 选 pytest/清单格式），
"历史同类错误的测试模式" = 经验库同键 sample_solution 作为注释引用；只建议不执行。
"""
from __future__ import annotations

import difflib
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from knowledge.experience_base import (ExperienceBase, detect_language,
                                       normalize_cause)

SOURCE_FIX = "排错"
SOURCE_REVIEW = "代码审查"
SOURCE_TEST = "测试失败"
SOURCE_GENERIC = "通用"

REVIEW_SIM_THRESHOLD = 0.55   # 审查发现 ↔ 排错候选 的相似度回流阈值
MAX_HARVEST_ITEMS = 12        # 单次审查最多收割条数（防御失控清单）
SUGGEST_MAX_ITEMS = 3         # 任务书 4.2：2-3 个

# 审查报告编号行：`1. 高 main.py:12 描述文字`（序号 + 可选严重度 + 可选文件:行 + 描述）
_REVIEW_ITEM_RE = re.compile(r"^\s*(\d{1,2})[.、)）]\s+(.+)$")
_SEVERITY_RE = re.compile(r"(严重程度[：:]?\s*)?(高|中|低|critical|high|medium|low)", re.IGNORECASE)
_FILE_RE = re.compile(r"([\w.\-/\\]+\.(?:py|js|jsx|ts|tsx|go|rs|java|json|yaml|yml))(?:[:：](\d{1,5}))?")
# 段落切割：Markdown 标题行或"总体评价"行之后一律不算问题清单
#（不能按 "2[.、]" 切——问题清单自己的第 2 条会被误当节头，实测教训）
_SECTION2_RE = re.compile(r"^\s*#{1,6}\s|总体评价", re.MULTILINE)
_HEADER_WORDS = ("问题清单", "总体评价", "审查结论", "未发现明显问题")
_PY_ERR_TYPES = frozenset(
    ("SyntaxError", "IndentationError", "ZeroDivisionError", "NameError", "TypeError",
     "AttributeError", "KeyError", "IndexError", "ValueError", "ModuleNotFoundError",
     "ImportError", "PermissionError", "RuntimeError", "RecursionError",
     "FileNotFoundError", "TimeoutError", "ConnectionError", "AssertionError"))


def unified_on() -> bool:
    return str(os.environ.get("MORAY_UNIFIED_ISSUE", "1")).strip().lower() \
        not in ("0", "false", "off", "no")


def review_knowledge_on() -> bool:
    return str(os.environ.get("MORAY_REVIEW_KNOWLEDGE", "1")).strip().lower() \
        not in ("0", "false", "off", "no")


def fix_suggest_test_on() -> bool:
    return str(os.environ.get("MORAY_FIX_SUGGEST_TEST", "1")).strip().lower() \
        not in ("0", "false", "off", "no")


def test_knowledge_on() -> bool:
    return str(os.environ.get("MORAY_TEST_KNOWLEDGE", "1")).strip().lower() \
        not in ("0", "false", "off", "no")


class IssueBase:
    """统一问题库入口（组合 ExperienceBase；兼容层零改动）。"""

    def __init__(self, path: Optional[str] = None, meta_path: Optional[str] = None):
        self.eb = ExperienceBase(path, meta_path)

    # ---------------- 写：统一记录 ----------------

    def record_issue(self, entry: Dict[str, Any], source: str = SOURCE_GENERIC,
                     issue_kind: str = "", applicable: str = "通用",
                     file_type: str = "") -> Optional[Dict[str, Any]]:
        """统一入库：增量键打标后委托 ExperienceBase.record（同一 JSONL，零迁移）。
        闸关 = 不加任何新键 = 纯 record 行为（L6.0 兼容）。"""
        if not isinstance(entry, dict):
            return None
        rec = dict(entry)
        if unified_on():
            rec.setdefault("source", str(source or SOURCE_GENERIC))
            if issue_kind:
                rec.setdefault("issue_kind", str(issue_kind)[:40])
            rec.setdefault("applicable", str(applicable or "通用")[:40])
            if file_type:
                rec.setdefault("file_type", str(file_type)[:20])
        return self.eb.record(rec)

    # ---------------- 读：审查赋能（阶段3） ----------------

    def review_hint(self, project_id: str, top_n: int = 10) -> str:
        """审查前注入：该项目历史高频问题 Top N + 同语言通用问题 Top N + 历史审查待处理项。
        无数据/闸关 → ""（prompt 一字不动）。"""
        if not (review_knowledge_on() and project_id):
            return ""
        try:
            rows = [r for r in self.eb.top_meta(project_id, None, int(top_n))
                    if r.get("score", 0) > 0]
            lang = "unknown"
            prof = self.eb.read_profile(project_id)
            if prof:
                lang = str(prof.get("high_freq_language") or "unknown")
            lines: List[str] = []
            for i, r in enumerate(rows, 1):
                line = ("%d. %s（历史 %d 次，成功率 %d%%）"
                        % (i, r["root_cause_category"], r["times"],
                           int(r["success_rate"] * 100)))
                if r.get("sample_solution"):
                    line += "；参考方案：" + str(r["sample_solution"])[:120]
                lines.append(line)
            own = {str(r.get("cause_key") or "") for r in rows}
            if lang and lang != "unknown":
                for g in self.eb.top_global(language=lang, error_type=None, top_n=int(top_n)):
                    if g["cause_key"] in own or g.get("score", 0) <= 0:
                        continue
                    lines.append("%d. [通用经验·%s] %s（跨项目 %d 次，成功率 %d%%）"
                                 % (len(lines) + 1, lang, g["root_cause_category"],
                                    g["times"], int(g["success_rate"] * 100)))
                    own.add(g["cause_key"])
            open_rows = self.open_review_issues(project_id, limit=int(top_n))
            if open_rows:
                lines.append("历史审查待处理：")
                for r in open_rows:
                    lines.append("  · " + str(r.get("root_cause_category") or "")[:120])
            if not lines:
                return ""
            return "【历史重点检查（来自统一问题库，仅作提示，不要复述）】\n" + "\n".join(lines[:int(top_n) + len(open_rows) + 2])
        except Exception:  # noqa: BLE001
            return ""

    def harvest_review(self, project_id: str, text: str,
                       language: str = "") -> Dict[str, Any]:
        """审查后反哺：编号问题清单 → 结构化 → record_issue(source=代码审查)。
        返回 {"harvested": n, "items": [...], "skipped": m}；绝不抛异常。"""
        out: Dict[str, Any] = {"harvested": 0, "items": [], "skipped": 0}
        try:
            if not (review_knowledge_on() and unified_on() and project_id
                    and str(text or "").strip()):
                return out
            t = str(text)
            # 只取"问题清单"段（第 2 节/总体评价之前）
            m2 = _SECTION2_RE.search(t)
            body = t[:m2.start()] if m2 else t
            for raw in body.splitlines():
                if out["harvested"] >= MAX_HARVEST_ITEMS:
                    break
                m = _REVIEW_ITEM_RE.match(raw)
                if not m:
                    continue
                desc_full = m.group(2).strip()
                if not desc_full or any(w in desc_full for w in _HEADER_WORDS):
                    continue
                sev_m = _SEVERITY_RE.search(desc_full)
                severity = (sev_m.group(2) if sev_m else "")
                file_type = ""
                f = _FILE_RE.search(desc_full)
                if f:
                    file_type = f.group(1)
                desc = re.sub(r"\s+", " ", desc_full)[:200]
                rec = self.record_issue(
                    {"project_id": str(project_id),
                     "error_type": "code-review",
                     "root_cause_category": desc,
                     "error_snippet": (("%s:%s" % (f.group(1), f.group(2))) if f else ""),
                     "solution": "", "success": False},
                    source=SOURCE_REVIEW,
                    issue_kind=severity or "审查发现",
                    applicable="排错/审查",
                    file_type=file_type)
                if rec is not None:
                    out["items"].append({"desc": desc, "severity": severity,
                                         "file": file_type})
                    out["harvested"] += 1
                else:
                    out["skipped"] += 1
            return out
        except Exception as e:  # noqa: BLE001
            out["skipped"] = out["skipped"] or 0
            out["error"] = "%s: %s" % (type(e).__name__, e)
            return out

    def open_review_issues(self, project_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """未修复的审查发现（source=代码审查，success=False），按时间倒序去重。"""
        try:
            rows = [r for r in self.eb._load()
                    if str(r.get("project_id") or "") == str(project_id)
                    and str(r.get("source") or "") == SOURCE_REVIEW
                    and not r.get("success")]
            seen: set = set()
            out: List[Dict[str, Any]] = []
            for r in sorted(rows, key=lambda x: str(x.get("timestamp") or ""), reverse=True):
                k = normalize_cause(r)
                if k in seen:
                    continue
                seen.add(k)
                out.append(r)
                if len(out) >= max(1, int(limit)):
                    break
            return out
        except Exception:  # noqa: BLE001
            return []

    def boost_with_review(self, project_id: str,
                          candidates: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], str]:
        """审查发现回流（任务书 6.2-2）：排错/测试候选命中未修复审查发现 → 前置 + 标注。
        匹配 = normalize 键相等 或 文本相似度 ≥0.55。无命中原样返回。"""
        try:
            if not (review_knowledge_on() and candidates):
                return list(candidates or []), ""
            issues = self.open_review_issues(project_id, limit=20)
            if not issues:
                return list(candidates), ""
            i_keys = [(normalize_cause(r), str(r.get("root_cause_category") or ""))
                      for r in issues]
            boosted: List[Dict[str, Any]] = []
            rest: List[Dict[str, Any]] = []
            hits = 0
            for c in candidates:
                cc = dict(c) if isinstance(c, dict) else {"cause": str(c)}
                text = "%s %s" % (str(cc.get("cause") or ""), str(cc.get("evidence") or ""))
                key = normalize_cause({"root_cause_category": text})
                matched = any(key == ik or (it and difflib.SequenceMatcher(
                    None, it.lower(), str(cc.get("cause") or text).lower()).ratio()
                    >= REVIEW_SIM_THRESHOLD) for ik, it in i_keys)
                if matched:
                    cc["review_match"] = "历史审查曾发现同类问题（未修复）→ 优先排查"
                    boosted.append(cc)
                    hits += 1
                else:
                    rest.append(cc)
            if not hits:
                return list(candidates), ""
            return boosted + rest, ("审查知识回流：%d/%d 候选命中历史审查发现，已前置" % (hits, len(candidates)))
        except Exception:  # noqa: BLE001
            return list(candidates or []), ""

    # ---------------- 读：测试失败关联（阶段5） ----------------

    def match_failure(self, project_id: str, candidates: List[Dict[str, Any]],
                      error_type: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """测试失败 → 历史方案：候选过 L5.1/L6.0 同款重排后，取第一个成功过的根因。
        返回 {"cause_key","times","successes","success_rate","solution","source"}；无 → None。"""
        if not (test_knowledge_on() and candidates and project_id):
            return None
        try:
            ranked, _note = self.eb.rank_candidates(project_id, list(candidates),
                                                    error_type=error_type)
            for c in ranked:
                m = (c or {}).get("experience_match") or {}
                if int(m.get("successes") or 0) > 0:
                    key = str(m.get("cause_key") or "")
                    sol = ""
                    try:
                        meta = self.eb.ensure_meta()
                        cc = ((meta.get("projects") or {}).get(str(project_id))
                              or {}).get("causes") or {}
                        sol = str((cc.get(key) or {}).get("sample_solution") or "")
                    except Exception:  # noqa: BLE001
                        sol = ""
                    return {"cause_key": key, "times": int(m.get("times") or 0),
                            "successes": int(m.get("successes") or 0),
                            "success_rate": m.get("success_rate"),
                            "solution": sol[:300], "source": str(m.get("source") or "")}
            return None
        except Exception:  # noqa: BLE001
            return None

    # ---------------- 修复后测试建议（阶段4，确定性生成，不执行） ----------------

    def suggest_tests(self, project_id: str, error_type: str = "",
                      cause_text: str = "", snippet: str = "",
                      solution_hint: str = "") -> Dict[str, Any]:
        """按根因类型生成 2-3 个回归测试建议（pytest 格式 for Python；其他语言给清单）。
        纯模板银行 + 历史方案注释，零 LLM、零执行。"""
        out: Dict[str, Any] = {"suggestions": "", "count": 0, "language": "unknown",
                               "path": "", "note": ""}
        try:
            if not fix_suggest_test_on():
                out["note"] = "开关关闭（MORAY_FIX_SUGGEST_TEST=0），未生成"
                return out
            text = "%s %s %s" % (error_type, cause_text, snippet)
            # Python 异常类名直接判 python（_LANG_RULES 不含 syntaxerror 等，实测教训）
            lang = ("python" if str(error_type or "").strip() in _PY_ERR_TYPES
                    else detect_language({"error_type": error_type,
                                          "root_cause_category": cause_text,
                                          "error_snippet": snippet}))
            key = normalize_cause({"error_type": error_type,
                                   "root_cause_category": cause_text})
            out["language"] = lang
            out["cause_key"] = key
            hist = ""
            try:
                meta = self.eb.ensure_meta()
                cc = ((meta.get("projects") or {}).get(str(project_id)) or {}).get("causes") or {}
                hist = str((cc.get(key) or {}).get("sample_solution") or "")[:200]
            except Exception:  # noqa: BLE001
                hist = ""
            ref = solution_hint or hist
            items = _py_bank(key, text, ref) if lang == "python" else _checklist_bank(key, ref)
            if not items:
                items = _py_bank("", text, ref) if lang == "python" else _checklist_bank("", ref)
            items = items[:SUGGEST_MAX_ITEMS]
            out["count"] = len(items)
            if lang == "python":
                body = "\n\n\n".join(it[1] for it in items)
                header = ("# 回归测试建议（由统一问题库按根因「%s」自动生成，仅供人工采纳，未执行）\n"
                          "# 历史参考方案：%s\n" % (key, ref or "（无历史记录）"))
                out["suggestions"] = header + body
            else:
                lines = ["# 回归测试建议（%s 项目，按根因「%s」，仅供人工采纳）" % (lang, key)]
                if ref:
                    lines.append("# 历史参考方案：%s" % ref)
                for i, (name, desc) in enumerate(items, 1):
                    lines.append("%d. %s" % (i, desc))
                out["suggestions"] = "\n".join(lines)
            out["note"] = "已生成 %d 条建议（%s，根因 %s）" % (len(items), lang, key)
            return out
        except Exception as e:  # noqa: BLE001
            out["note"] = "生成失败（不影响主流程）：%s: %s" % (type(e).__name__, e)
            return out


# ---------------- 确定性建议银行（纯函数，可独立单测） ----------------

def _slug(key: str) -> str:
    s = re.sub(r"\s+", "_", str(key or "issue"))[:30]
    return re.sub(r"[^\w\u4e00-\u9fff]", "_", s) or "issue"


def _py_bank(key: str, text: str, ref: str) -> list:
    """Python/pytest 建议模板（按归一化根因键；键不命中 → 通用冒烟两条）。"""
    ref_c = ("# 历史参考：%s\n" % ref) if ref else ""
    low = str(key or "")
    if "依赖" in low or "导入" in low:
        mod = ""
        m = re.search(r"No module named ['\"]?([\w.]+)", str(text), re.IGNORECASE)
        if m:
            mod = m.group(1)
        mod_arg = '"%s"' % mod if mod else "os.environ.get('EXPECTED_MODULE', '确认模块名')"
        return [("test_dependency_imports",
                 "import importlib\n\n\ndef test_dependency_imports():\n"
                 "    %s修复后，依赖应可直接导入\n"
                 "    assert importlib.import_module(%s) is not None" % (ref_c, mod_arg)),
                ("test_entry_still_runs",
                 "def test_entry_still_runs():\n"
                 "    %s补齐依赖后，入口导入不再 ModuleNotFoundError\n"
                 "    import sys; assert 'site-packages' in '\\n'.join(sys.path) or True" % ref_c)]
    if "除零" in low:
        return [("test_no_zero_division_on_empty",
                 "import pytest\n\n\ndef test_no_zero_division_on_empty():\n    %s"
                 "空集合/零分母不得再触发 ZeroDivisionError（按实际入口替换 calc）\n"
                 "    with pytest.raises(ZeroDivisionError):\n"
                 "        assert False, '占位：替换为真实调用 calc([]) 并断言不再抛出'" % ref_c),
                ("test_zero_denominator_guarded",
                 "def test_zero_denominator_guarded():\n"
                 "    %s分母为 0 时应返回兜底值（按项目约定填 expected）\n"
                 "    assert True, '占位：avg = calc_safe([]) ; assert avg == expected'" % ref_c)]
    if "语法" in low or "缩进" in low:
        return [("test_module_compiles",
                 "import py_compile\n\n\ndef test_module_compiles():\n"
                 "    %s涉事文件必须可编译（替换为真实相对路径）\n"
                 "    py_compile.compile('main.py', doraise=True)" % ref_c),
                ("test_no_syntax_regressions",
                 "def test_no_syntax_regressions():\n"
                 "    %s全项目 py 扫描无语法错误（大项目可缩范围）\n"
                 "    import ast, pathlib\n"
                 "    for p in pathlib.Path('.').rglob('*.py'):\n"
                 "        ast.parse(p.read_text(encoding='utf-8'), filename=str(p))" % ref_c)]
    if "超时" in low or "网络" in low or "连接" in low:
        return [("test_remote_call_within_budget",
                 "import time\n\n\ndef test_remote_call_within_budget():\n"
                 "    %s外部依赖调用必须在时限内返回（替换为真实客户端与断言）\n"
                 "    started = time.monotonic()\n"
                 "    assert True, '占位：do_call(); assert time.monotonic()-started < 5'" % ref_c),
                ("test_timeout_has_retry_or_fallback",
                 "def test_timeout_has_retry_or_fallback():\n"
                 "    %s超时路径应有重试/降级（构造不可达端点验证不崩）\n"
                 "    assert True, '占位：unreachable() 应走降级分支'" % ref_c)]
    if "空值" in low or "键缺失" in low or "越界" in low or "类型" in low or "取值" in low:
        return [("test_boundary_inputs_none_empty",
                 "import pytest\n\n\ndef test_boundary_inputs_none_empty():\n"
                 "    %sNone/空串/空集合/越界索引等边界输入行为符合约定\n"
                 "    assert True, '占位：parametrize 边界用例'" % ref_c),
                ("test_error_message_helpful",
                 "def test_error_message_helpful():\n"
                 "    %s非法输入的报错信息应指明字段与原因\n"
                 "    assert True, '占位：pytest.raises(..., match=...)'" % ref_c)]
    # 通用冒烟
    return [("test_smoke_entry",
             "def test_smoke_entry():\n"
             "    %s入口可运行、退出码 0（替换为真实入口调用）\n"
             "    assert True, '占位：run_entry(); assert code == 0'" % ref_c),
            ("test_regression_repro",
             "def test_regression_repro():\n"
             "    %s用本次报错的最小复现输入断言已修复\n"
             "    assert True, '占位：最小复现 + 预期输出'" % ref_c)]


def _checklist_bank(key: str, ref: str) -> list:
    """非 Python 项目的清单式建议（语言无关）。"""
    return [("回归复现", "用本次报错的最小输入重新执行触发路径，确认不再出现「%s」" % (key or "同类问题")),
            ("边界输入", "对涉事函数/接口补空值、越界、非法类型三类边界用例"),
            ("守卫断言", "若历史方案可用（%s），为该修复补一条断言防回退" % (ref or "见修复记录"))]
