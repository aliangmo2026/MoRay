# -*- coding: utf-8 -*-
"""ExperienceBase：本地排错经验库（p_mcp 迭代·任务4 → L5.1 经验进化，2026-09-28）。

L5.0 既有契约（逐字保留，tests/mcp_phase/test_04_experience.py 钉死）：
  * record(entry)  追加一条 JSONL：project_id / error_type / root_cause_category /
    error_snippet / solution / duration_sec / success / timestamp（缺 timestamp 自动补）。
    非法字段（缺 project_id/error_type、非 dict）拒绝并返回 None（不抛）。
  * query(project_id, error_type, top_n=3)
    按 root_cause_category 原文聚合：score = 出现次数 × 成功率；同分取最近出现。
  * build_hint / stats 语义不变。

L5.1 增量（全部旁路式，关闸即回 L5.0 行为）：
  * 根因归一化 normalize_cause(entry)：把 LLM 自由文本收敛成稳定"根因类型"键
    （异常类名映射 > 文本抽异常名 > 中文关键词规则 > 原文截断兜底）。
    record() 落盘时顺带写 cause_key / language 两个新键（旧行无此键，读时现算）。
  * 项目级高频根因：每次 record() 后按**字节 offset** 增量重算当前项目聚类，
    维护 sidecar 元数据 experience_stats.json（与 JSONL 同目录，
    MORAY_EXPERIENCE_STATS 可覆盖）——每项目存 top_root_causes(Top5)、
    error_types/languages 计数、耗时统计。build_hint 优先读预计算结果（O(1)，
    不再全扫 JSONL）；元数据缺失/损坏 → 自动全量重建（自愈）。
  * rank_candidates(project_id, candidates)：按历史频次×成功率对 Iris 根因候选
    做纯逻辑重排（高频高成功→最先；历史 0 成功→最后并标注）。消费点
    engine/task_runner.py._exec_mcp（analyze 工具返回后处理，模板/步骤零改动）。
  * 项目画像：work/project_profiles/{sha256(project_id)[:16]}.json
    （MORAY_PROJECT_PROFILES_DIR 可重定向；聚类顺带产出，供后端展示/策略消费）。

L5.3 增量（全部旁路式，关闸即回 L5.1 行为）：
  * 经验分层 scope（project / global）：record() 自动判定——命中语言级异常类名表
    （_EXC_CAUSE）或语言级关键词规则（_KW_RULES）= "global"（跨项目通用根因，如
    缩进错误/导入错误/除零）；自由文本兜底（业务描述、配置错误）= "project"。
  * sidecar 元数据新增全局桶 meta["global"]（按 cause_key 跨项目聚合 + 语言分布），
    META_VERSION 升 "l52-1"：旧 "l51-1" 元数据自动失配 → 走既有自愈重建通道，无需迁移脚本。
  * 冷启动：build_hint() 项目层无可用根因时，自动补同语言 global Top（行首标
    "[通用经验（同语言）]"）；项目层有数据但不足 top_n 时也补位（项目经验永远排前）。
  * 跨项目推荐：rank_candidates() 候选先查本项目桶（行为与 L5.1 逐字一致），
    项目 miss 的候选再查 global 桶 → 只进"参考"档并标 experience_match.source=
    "通用经验（同语言）"（不参与优先/置末判定）。
  * 独立闸 MORAY_EXPERIENCE_GLOBAL=0（需在总闸开的前提下）：关 = 全局桶不聚合、
    冷启动不补位、重排不查全局——行为与 L5.1 完全一致。

总闸：MORAY_EXPERIENCE_EVOLUTION=0 → 不写 cause_key/language/scope/元数据/画像，build_hint 走
旧全量扫描，rank 原样返回——行为与 L5.0 完全一致。
线程/进程安全：单行追加（既定容错）；元数据/画像 tmp→replace 原子写；脏行跳过。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_DEFAULT_PATH = Path(__file__).resolve().parent / "experience_base.jsonl"
MORAY_ROOT = Path(__file__).resolve().parents[1]
REQUIRED_FIELDS = ("project_id", "error_type", "root_cause_category")
META_VERSION = "l52-1"     # l51-1→l52-1：新增 global 桶（旧元数据自动失配→自愈重建）

TOP_STORE = 5      # 每项目在元数据里预存的"高频根因"条数（任务书 2.2 Top5）
FIRST_VERIFY_MIN_TIMES = 5    # 阶段3：出现≥5次且成功率达标 → 第一个验证
FIRST_VERIFY_MIN_RATE = 0.5   # "成功率高"门槛（≥50%）
DEPRIORITIZE_MIN_TIMES = 2    # 阶段3：历史 0 成功且出现≥2次 → 放最后（不物理删，诚实留痕）


def _evolution_on() -> bool:
    return str(os.environ.get("MORAY_EXPERIENCE_EVOLUTION", "1")).strip().lower() \
        not in ("0", "false", "off", "no")


def _rank_on() -> bool:
    return str(os.environ.get("MORAY_CAUSE_RANK", "1")).strip().lower() \
        not in ("0", "false", "off", "no")


def _global_on() -> bool:
    """L5.3 分层独立闸（需在总闸开的前提下才生效）：关 = 无 global 桶（行为回 L5.1）。"""
    return _evolution_on() and str(os.environ.get("MORAY_EXPERIENCE_GLOBAL", "1")).strip().lower() \
        not in ("0", "false", "off", "no")


# ---------------- 根因归一化（纯规则，无新依赖） ----------------

# 常见异常类名 → 规范"根因类型"键（中文可读；聚类/Top5/画像都按这个键聚合）
_EXC_CAUSE: Dict[str, str] = {
    "ZeroDivisionError": "除零错误（分母为0/空集合均值）",
    "ModuleNotFoundError": "依赖模块未安装",
    "ImportError": "导入失败",
    "IndentationError": "缩进错误",
    "SyntaxError": "语法错误",
    "NameError": "名称未定义",
    "TypeError": "类型错误",
    "AttributeError": "属性/成员缺失",
    "KeyError": "映射键缺失",
    "IndexError": "序列索引越界",
    "FileNotFoundError": "文件或路径不存在",
    "PermissionError": "权限不足",
    "ValueError": "取值不合法",
    "RuntimeError": "运行时异常",
    "RecursionError": "递归过深",
    "TimeoutError": "操作超时",
    "ConnectionError": "网络连接失败",
    "ConnectionRefusedError": "连接被拒绝（服务未启动/端口错）",
    "AssertionError": "断言失败",
    "NullPointerException": "空指针/空值访问",
    "ClassNotFoundException": "类路径缺类",
    "NoClassDefFoundError": "运行期缺类",
    "ArithmeticException": "算术异常（除零）",
}
_EXC_TOKEN_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]{2,}(?:Error|Exception))\b")
# 关键词规则（作用于 error_type+类别+片段的合并小写文本；顺序=优先级）
_KW_RULES: List[Tuple[str, str]] = [
    (r"除零|division by zero", "除零错误（分母为0/空集合均值）"),
    (r"no module named|modulenotfound|缺.{0,3}依赖|依赖.{0,4}(缺失|未安装)|未安装.{0,4}依赖|pip install", "依赖模块未安装"),
    (r"循环导入|circular import", "循环导入"),
    (r"缩进|indentation", "缩进错误"),
    (r"不存在|找不到文件|no such file", "文件或路径不存在"),
    (r"超时|timed out|timeout", "操作超时"),
    (r"拒绝连接|connection refused|无法连接|连接失败", "网络连接失败"),
    (r"无权限|权限不足|permission", "权限不足"),
    (r"空值|none|null|undefined|未定义", "空值访问（None/undefined）"),
    (r"越界|out of range", "序列索引越界"),
    (r"键缺失|no such key", "映射键缺失"),
    (r"语法错误", "语法错误"),
    (r"虚拟环境|venv|环境不一致", "运行环境问题"),
]
# 语言推断规则（任务书 4.1"高频语言"；entry 无语言字段，按报错特征词推断）
_LANG_RULES: List[Tuple[str, str]] = [
    (r"traceback|modulenotfound|importerror|indentationerror|zerodivision|nameerror|"
     r"attributeerror|keyerror|indexerror|pytest|\.py\b", "python"),
    (r"nullpointer|classnotfound|noclassdeffound|exception in thread|\.java\b|maven|gradle", "java"),
    (r"cannot read propert|is not a function|err_module|npm err|webpack|vite|\.jsx?\b", "javascript"),
    (r"panic:|goroutine|\.go\b", "go"),
    (r"cargo|rustc|rust-|\.rs\b", "rust"),
    (r"\bts\d{4}\b|\.tsx?\b|\btsc\b", "typescript"),
]


def _norm_text(entry: Dict[str, Any]) -> str:
    # 归一化只看 error_type + 根因类别：error_snippet 里常有 "No module named" 等
    # 通用字样，掺进来会把不同根因过度收敛（并把 test_04 钉死的"hint 含原类别"打挂）。
    return "%s %s" % (str(entry.get("error_type") or ""),
                      str(entry.get("root_cause_category") or ""))


def _joined_text(entry: Dict[str, Any]) -> str:
    return " ".join(str(entry.get(k) or "") for k in
                    ("error_type", "root_cause_category", "error_snippet"))


def normalize_cause(entry: Dict[str, Any]) -> str:
    """LLM 自由文本根因 → 稳定类型键（同一类报错跨次收敛到同一键）。"""
    et = str(entry.get("error_type") or "").strip()
    if et in _EXC_CAUSE:
        return _EXC_CAUSE[et]
    text = _norm_text(entry)
    for token in _EXC_TOKEN_RE.findall(text):
        if token in _EXC_CAUSE:
            return _EXC_CAUSE[token]
        # 未知异常名也归一化到"异常类名本身"（比整段描述稳定）
        return token
    low = text.lower()
    for pat, key in _KW_RULES:
        if re.search(pat, low):
            return key
    # [L6.0 阶段3] 人工规则全 miss → 挖掘器自动发现并（可选）生效的叠加规则。
    # 只读 active 候选（默认只有建议不生效）；关闸/无候选文件/异常 = 原行为逐字节一致。
    try:
        from knowledge import pattern_miner as _pm
        for _rule in _pm.runtime_rules():
            try:
                if re.search(_rule["pattern"], low):
                    return str(_rule["key"])[:60]
            except re.error:
                continue
    except Exception:  # noqa: BLE001
        pass
    fallback = re.sub(r"\s+", " ", str(entry.get("root_cause_category") or "")).strip()
    return (fallback or "未分类")[:60]


def detect_language(entry: Dict[str, Any]) -> str:
    text = _joined_text(entry).lower()
    for pat, lang in _LANG_RULES:
        if re.search(pat, text):
            return lang
    return "unknown"


# 语言级根因键白名单（L5.3 scope 判定核心）：来自异常类名表 + 语言级关键词规则，
# 这类根因跨项目成立（Python 缩进错误在哪个项目都是缩进错误）→ global；
# normalize_cause 走"原文截断兜底"产出的键（业务描述/配置错误）→ project。
_GENERIC_CAUSE_KEYS = frozenset(_EXC_CAUSE.values()) | frozenset(k for _, k in _KW_RULES)


def classify_scope(entry: Dict[str, Any]) -> str:
    """经验条目的适用范围：'global'（语言级/框架级）| 'project'（业务/配置特定）。
    判定复用 normalize_cause 的匹配链结果键是否在白名单；表外异常名（业务自定义异常）
    归 project 更诚实——它只在自己的业务语境成立。"""
    key = normalize_cause(entry)
    if key not in _GENERIC_CAUSE_KEYS:
        return "project"
    et = str(entry.get("error_type") or "").strip()
    if et and et not in _EXC_CAUSE:
        # 键虽 generic，但 error_type 是不在表内的自定义异常 → 业务异常归 project
        for token in _EXC_TOKEN_RE.findall(_norm_text(entry)):
            if token not in _EXC_CAUSE and token == key:
                return "project"
    return "global"


# ---------------- 元数据/画像路径 ----------------

def _default_meta_path(jsonl_path: Path) -> Path:
    return jsonl_path.with_name("experience_stats.json")


def _default_profiles_dir() -> Path:
    return Path(os.environ.get("MORAY_PROJECT_PROFILES_DIR")
                or (MORAY_ROOT / "work" / "project_profiles"))


def project_profile_hash(project_id: str) -> str:
    return hashlib.sha256(str(project_id or "").encode("utf-8")).hexdigest()[:16]


class ExperienceBase:
    def __init__(self, path: Optional[str] = None, meta_path: Optional[str] = None):
        self.path = Path(path or os.environ.get("MORAY_EXPERIENCE_DB", str(_DEFAULT_PATH)))
        self.meta_path = Path(meta_path or os.environ.get("MORAY_EXPERIENCE_STATS")
                              or str(_default_meta_path(self.path)))

    # ---------- 写 ----------

    def record(self, entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not isinstance(entry, dict):
            return None
        rec = dict(entry)
        for k in REQUIRED_FIELDS:
            if not str(rec.get(k) or "").strip():
                return None
        rec.setdefault("timestamp", datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"))
        rec.setdefault("success", False)
        rec["success"] = bool(rec["success"])
        for key in ("error_snippet", "solution"):
            if rec.get(key) is not None:
                rec[key] = str(rec[key])[:2000]
        if rec.get("duration_sec") is not None:
            try:
                rec["duration_sec"] = float(rec["duration_sec"])
            except (TypeError, ValueError):
                rec.pop("duration_sec")
        evolved = _evolution_on()
        if evolved:
            # L5.1：落盘附带归一键与语言（旧行无此两键 → 读时现算，向后兼容）
            rec.setdefault("cause_key", normalize_cause(rec))
            rec.setdefault("language", detect_language(rec))
            if _global_on():
                # L5.3：scope 键（语言级→global，业务/兜底→project；显式传入合法值则尊重）。
                # MORAY_EXPERIENCE_GLOBAL 关 = 不写此键 = 行形状与 L5.1 一致。
                s = str(rec.get("scope") or "").strip().lower()
                rec["scope"] = s if s in ("project", "global") else classify_scope(rec)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(line)
        if evolved:
            try:
                self._bump_meta(rec)
            except Exception:  # noqa: BLE001 元数据坏了绝不影响记库主链路（下次自愈重建）
                pass
            # [L6.0 阶段3] 模式挖掘懒触发（新增记录数/天数达阈值才真跑；内部全 try/except，
            # 总闸 MORAY_EXPERIENCE_EVOLUTION 关 = 完全不碰，L5.0 形状不变）。
            try:
                from knowledge import pattern_miner as _pm
                _pm.maybe_mine()
            except Exception:  # noqa: BLE001
                pass
        return rec

    # ---------- 读 ----------

    def _load(self) -> List[Dict[str, Any]]:
        if not self.path.is_file():
            return []
        out: List[Dict[str, Any]] = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if isinstance(rec, dict):
                        out.append(rec)
                except json.JSONDecodeError:  # 脏行跳过（追加式文件的既定容错）
                    continue
        return out

    def query(self, project_id: Optional[str] = None, error_type: Optional[str] = None,
              top_n: int = 3) -> List[Dict[str, Any]]:
        groups: Dict[str, Dict[str, Any]] = {}
        for rec in self._load():
            if project_id is not None and rec.get("project_id") != project_id:
                continue
            if error_type is not None and rec.get("error_type") != error_type:
                continue
            cat = str(rec.get("root_cause_category") or "未分类")
            g = groups.setdefault(cat, {"root_cause_category": cat, "times": 0,
                                        "successes": 0, "samples": [], "last_ts": ""})
            g["times"] += 1
            if rec.get("success"):
                g["successes"] += 1
                g["samples"].append({
                    "solution": str(rec.get("solution") or "")[:300],
                    "error_snippet": str(rec.get("error_snippet") or "")[:300]})
            g["last_ts"] = max(g["last_ts"], str(rec.get("timestamp") or ""))
        rows: List[Dict[str, Any]] = []
        for g in groups.values():
            rate = (g["successes"] / g["times"]) if g["times"] else 0.0
            rows.append({
                "root_cause_category": g["root_cause_category"],
                "times": g["times"], "successes": g["successes"],
                "success_rate": round(rate, 3),
                "score": round(g["times"] * rate, 4),
                "sample_solution": g["samples"][-1]["solution"] if g["samples"] else "",
                "error_snippet": g["samples"][-1]["error_snippet"] if g["samples"] else "",
                "last_ts": g["last_ts"],
            })
        rows.sort(key=lambda x: (-x["score"], -x["times"], x["last_ts"]), reverse=False)
        return rows[:max(1, int(top_n))]

    # ---------- L5.1：项目级高频根因（元数据，O(1) 读） ----------

    def _empty_meta(self) -> Dict[str, Any]:
        return {"version": META_VERSION, "updated_at": "", "offset": 0,
                "records": 0, "projects": {}, "global": self._new_global_bucket()}

    @staticmethod
    def _new_global_bucket() -> Dict[str, Any]:
        # L5.3 全局层：跨项目共享的语言级根因聚合（结构对齐项目桶的 causes 部分）
        return {"causes": {}, "languages": {}}

    def _load_meta(self) -> Optional[Dict[str, Any]]:
        try:
            if not self.meta_path.is_file():
                return None
            meta = json.loads(self.meta_path.read_text(encoding="utf-8"))
            if (isinstance(meta, dict) and meta.get("version") == META_VERSION
                    and isinstance(meta.get("projects"), dict)
                    and isinstance(meta.get("offset"), int)):
                return meta
            return None
        except (OSError, json.JSONDecodeError):
            return None

    def _save_meta(self, meta: Dict[str, Any]) -> None:
        meta["updated_at"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        self.meta_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.meta_path.with_name(self.meta_path.name + ".tmp")
        tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.meta_path)

    def _new_proj_bucket(self, project_id: str) -> Dict[str, Any]:
        return {"project_id": project_id, "records": 0, "successes": 0,
                "duration_sum": 0.0, "duration_n": 0, "success_duration_sum": 0.0,
                "success_duration_n": 0, "error_types": {}, "languages": {},
                "causes": {}, "top_root_causes": []}

    @staticmethod
    def _row_scope(rec: Dict[str, Any]) -> str:
        """行的适用范围：落盘 scope 优先；旧行（L5.1 前无此键）现算（与 cause_key 同容错策略）。"""
        s = str(rec.get("scope") or "").strip().lower()
        if s in ("project", "global"):
            return s
        return classify_scope(rec)

    def _merge_cause(self, bucket: Dict[str, Any], rec: Dict[str, Any]) -> str:
        """把一行经验合入某个 causes 字典（项目桶与全局桶共用同一聚合形状）。"""
        key = str(rec.get("cause_key") or "") or normalize_cause(rec)
        lang = str(rec.get("language") or "") or detect_language(rec)
        et = str(rec.get("error_type") or "") or "unknown"
        ok = bool(rec.get("success"))
        c = bucket.setdefault(key, {"times": 0, "successes": 0, "error_types": {},
                                    "languages": {}, "sample_solution": "",
                                    "error_snippet": "", "last_ts": ""})
        c["times"] += 1
        if ok:
            c["successes"] += 1
            c["sample_solution"] = str(rec.get("solution") or "")[:300]
            c["error_snippet"] = str(rec.get("error_snippet") or "")[:300]
        c["error_types"][et] = c["error_types"].get(et, 0) + 1
        if lang:
            c["languages"][lang] = c["languages"].get(lang, 0) + 1
        c["last_ts"] = max(c["last_ts"], str(rec.get("timestamp") or ""))
        return key

    def _apply_row(self, meta: Dict[str, Any], rec: Dict[str, Any]) -> None:
        pid = str(rec.get("project_id") or "")
        if not pid:
            return
        proj = meta["projects"].setdefault(pid, self._new_proj_bucket(pid))
        key = self._merge_cause(proj["causes"], rec)
        lang = str(rec.get("language") or "") or detect_language(rec)
        et = str(rec.get("error_type") or "") or "unknown"
        ok = bool(rec.get("success"))
        proj["records"] += 1
        proj["successes"] += 1 if ok else 0
        meta["records"] = meta.get("records", 0) + 1
        if et:
            proj["error_types"][et] = proj["error_types"].get(et, 0) + 1
        if lang:
            proj["languages"][lang] = proj["languages"].get(lang, 0) + 1
        dur = rec.get("duration_sec")
        if isinstance(dur, (int, float)):
            proj["duration_sum"] += float(dur)
            proj["duration_n"] += 1
            if ok:
                proj["success_duration_sum"] += float(dur)
                proj["success_duration_n"] += 1
        proj["top_root_causes"] = self._top_rows(proj, TOP_STORE)
        # [L5.3] 语言级根因跨项目聚合（独立闸关 = 不动全局桶 = L5.1 形状）
        if _global_on() and self._row_scope(rec) == "global":
            g = meta.setdefault("global", self._new_global_bucket())
            self._merge_cause(g["causes"], rec)
            if lang:
                g["languages"][lang] = g["languages"].get(lang, 0) + 1

    @staticmethod
    def _cause_row(key: str, c: Dict[str, Any]) -> Dict[str, Any]:
        rate = (c["successes"] / c["times"]) if c["times"] else 0.0
        return {"root_cause_category": key, "cause_key": key,
                "times": c["times"], "successes": c["successes"],
                "success_rate": round(rate, 3), "score": round(c["times"] * rate, 4),
                "sample_solution": c.get("sample_solution", ""),
                "error_snippet": c.get("error_snippet", ""),
                "last_ts": c.get("last_ts", ""),
                "error_types": sorted(c.get("error_types", {}).keys())}

    @classmethod
    def _top_rows(cls, proj: Dict[str, Any], n: int) -> List[Dict[str, Any]]:
        rows = [cls._cause_row(k, c) for k, c in proj["causes"].items()]
        rows.sort(key=lambda x: (-x["score"], -x["times"], x["last_ts"]))
        return rows[:max(1, int(n))]

    def _rebuild_meta(self) -> Dict[str, Any]:
        """全量扫描 JSONL 重建元数据（缺失/损坏/截断时的自愈通道）。"""
        meta = self._empty_meta()
        for rec in self._load():
            self._apply_row(meta, rec)
        try:
            meta["offset"] = self.path.stat().st_size if self.path.is_file() else 0
        except OSError:
            meta["offset"] = 0
        self._save_meta(meta)
        for pid in meta["projects"]:
            try:
                self._write_profile(meta["projects"][pid])
            except Exception:  # noqa: BLE001
                pass
        return meta

    def _bump_meta(self, rec: Dict[str, Any]) -> None:
        """增量维护：从记录的 offset 读到文件尾（覆盖刚追加的本行），刷新 Top5 与画像。"""
        meta = self._load_meta()
        try:
            size = self.path.stat().st_size
        except OSError:
            size = 0
        if meta is None or size < meta["offset"]:
            self._rebuild_meta()
            return
        if size > meta["offset"]:
            with open(self.path, "rb") as f:
                f.seek(meta["offset"])
                chunk = f.read().decode("utf-8", "replace")
            meta["offset"] = size
            consumed = 0
            for line in chunk.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                    if isinstance(row, dict):
                        self._apply_row(meta, row)
                except json.JSONDecodeError:
                    continue
        pid = str(rec.get("project_id") or "")
        if pid and pid in meta["projects"]:
            self._write_profile(meta["projects"][pid])
        self._save_meta(meta)

    def ensure_meta(self) -> Dict[str, Any]:
        """对外读入口：优先增量，缺/坏/落后 → 自愈重建（build_hint/top_meta 共用）。"""
        meta = self._load_meta()
        try:
            size = self.path.stat().st_size if self.path.is_file() else 0
        except OSError:
            size = 0
        if meta is None or size < meta["offset"] or size > meta["offset"]:
            # 落后（其它入口直写过 JSONL）或有损坏 → 补齐增量部分；差异大则重建
            if meta is not None and size > meta["offset"] and size - meta["offset"] < 262144:
                try:
                    with open(self.path, "rb") as f:
                        f.seek(meta["offset"])
                        chunk = f.read().decode("utf-8", "replace")
                    meta["offset"] = size
                    for line in chunk.splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            row = json.loads(line)
                            if isinstance(row, dict):
                                self._apply_row(meta, row)
                        except json.JSONDecodeError:
                            continue
                    self._save_meta(meta)
                    return meta
                except OSError:
                    pass
            return self._rebuild_meta()
        return meta

    def top_meta(self, project_id: Optional[str] = None, error_type: Optional[str] = None,
                 top_n: int = TOP_STORE) -> List[Dict[str, Any]]:
        """读预计算的项目高频根因（任务书 2.3：新任务 O(1) 读，不全扫）。"""
        if not _evolution_on():
            return self.query(project_id, error_type, top_n)
        meta = self.ensure_meta()
        buckets = ([project_id] if project_id is not None
                   else list(meta["projects"].keys()))
        rows: List[Dict[str, Any]] = []
        for pid in buckets:
            proj = meta["projects"].get(str(pid))
            if not proj:
                continue
            for r in proj.get("top_root_causes") or []:
                if error_type is not None:
                    ets = r.get("error_types") or []
                    if ets and str(error_type) not in ets:
                        continue
                rows.append(dict(r))
        rows.sort(key=lambda x: (-x["score"], -x["times"], x["last_ts"]))
        return rows[:max(1, int(top_n))]

    def top_global(self, language: Optional[str] = None, error_type: Optional[str] = None,
                   top_n: int = TOP_STORE) -> List[Dict[str, Any]]:
        """[L5.3] 跨项目高频语言级根因（scope=global 聚合桶）。
        language 给了 → 只保留该语言出现过的根因；language=unknown/None → 不按语言过滤。
        独立闸关 / 无数据 → []。行形状对齐 top_meta，另带 scope=languages。"""
        if not _global_on():
            return []
        try:
            meta = self.ensure_meta()
        except Exception:  # noqa: BLE001
            return []
        gb = meta.get("global") or {}
        causes = gb.get("causes") or {}
        rows: List[Dict[str, Any]] = []
        for k, c in causes.items():
            if error_type is not None:
                ets = c.get("error_types") or {}
                if ets and str(error_type) not in ets:
                    continue
            langs = c.get("languages") or {}
            if language and language != "unknown" and not langs.get(language):
                continue
            r = dict(self._cause_row(k, c))
            r["scope"] = "global"
            r["languages"] = dict(langs)
            rows.append(r)
        rows.sort(key=lambda x: (-x["score"], -x["times"], x["last_ts"]))
        return rows[:max(1, int(top_n))]

    def build_hint(self, project_id: Optional[str] = None, error_type: Optional[str] = None,
                   top_n: int = 3) -> str:
        rows = self.top_meta(project_id, error_type, top_n)
        if (not rows and error_type is not None and project_id is not None
                and _evolution_on()):
            # 元数据 Top5 没命中该 error_type：可能被 Top5 截断，回退全扫保证正确性
            rows = self.query(project_id, error_type, top_n)
        elif not rows and not _evolution_on():
            rows = self.query(project_id, error_type, top_n)
        rows = [r for r in rows if r["score"] > 0]  # 从未成功过的根因不进优先假设
        rows = rows[:top_n]
        lines = []
        for i, r in enumerate(rows, 1):
            line = (f"{i}. {r['root_cause_category']}"
                    f"（历史 {r['times']} 次，成功率 {int(r['success_rate'] * 100)}%）")
            if r["sample_solution"]:
                line += "；参考方案：" + r["sample_solution"][:120]
            lines.append(line)
        # [L5.3] 冷启动/补位：项目层可用根因不足 top_n → 追同语言 global Top
        # （任务书 3.3/3.4：新项目第一次报错自动带通用经验；来源显式标注）。
        if _global_on() and len(lines) < top_n:
            own = {str(r.get("cause_key") or r.get("root_cause_category") or "") for r in rows}
            lang = detect_language({"error_type": str(error_type or "")})
            want = max(1, top_n - len(lines))
            g_rows = [g for g in self.top_global(language=None if lang == "unknown" else lang,
                                                 error_type=None, top_n=want * 3)
                      if g["cause_key"] not in own and g["score"] > 0]
            for g in g_rows[:want]:
                tag = "通用经验（同语言）" if (lang != "unknown"
                                     and g["languages"].get(lang)) else "通用经验（跨项目）"
                line = (f"{len(lines) + 1}. [{tag}] {g['root_cause_category']}"
                        f"（跨项目 {g['times']} 次，成功率 {int(g['success_rate'] * 100)}%）")
                if g["sample_solution"]:
                    line += "；参考方案：" + g["sample_solution"][:120]
                lines.append(line)
        if not lines:
            return ""
        return "\n".join(lines)

    # ---------- L5.1 阶段3：按经验自动调整排查顺序（纯逻辑，不调 LLM） ----------

    def rank_candidates(self, project_id: str, candidates: List[Dict[str, Any]],
                        error_type: Optional[str] = None) -> Tuple[List[Dict[str, Any]], str]:
        """Iris 根因候选重排：高频高成功→最先验证；历史 0 成功→最后（标注，不物理删）。
        返回 (新列表, 说明文本)；无经验/关闸/异常时原序返回。稳定排序保留 LLM 相对序。
        [L5.3] 双层：候选先查本项目历史（判定与 L5.1 逐字一致），项目 miss 的再查 global 桶
        ——只进"参考"档并标 experience_match.source="通用经验（同语言）"，
        不参与优先/置末判定（通用层的成功率是跨项目平均值，不足以给单个新项目下强结论）。"""
        if not (_evolution_on() and _rank_on()) or not candidates or not project_id:
            return list(candidates or []), ""
        try:
            meta = self.ensure_meta()
        except Exception:  # noqa: BLE001
            return list(candidates), ""
        proj = meta["projects"].get(str(project_id))
        causes = (proj or {}).get("causes") or {}
        gcauses = ((meta.get("global") or {}).get("causes") or {}) if _global_on() else {}
        if not causes and not gcauses:
            return list(candidates), ""
        bucketed: List[Tuple[int, int, float, Dict[str, Any]]] = []
        touched = 0
        global_hits = 0
        for idx, cand in enumerate(candidates):
            c = dict(cand) if isinstance(cand, dict) else {"cause": str(cand)}
            # 归一化只看候选自身文本（cause+evidence）：步级 error_type 是"这一单"的标签，
            # 拿去归一化会让每个候选都命中同一个历史键，排序失去区分度（E2E 抓出）。
            c_text = "%s %s" % (str(c.get("cause") or ""), str(c.get("evidence") or ""))
            key = normalize_cause({"root_cause_category": c_text})
            h = causes.get(key)
            if h:
                rate = (h["successes"] / h["times"]) if h["times"] else 0.0
                if (h["times"] >= FIRST_VERIFY_MIN_TIMES
                        and rate >= FIRST_VERIFY_MIN_RATE):
                    g, act = 0, "优先验证（历史高频且修复成功率高）"
                elif h["successes"] == 0 and h["times"] >= DEPRIORITIZE_MIN_TIMES:
                    g, act = 2, "置末（历史出现但从未修复成功）"
                else:
                    g, act = 1, "参考（历史有记录，未达优先/降权阈值）"
                c["experience_match"] = {"cause_key": key, "times": h["times"],
                                         "successes": h["successes"],
                                         "success_rate": round(rate, 3), "action": act,
                                         "source": "历史经验（本项目）"}
                touched += 1
                bucketed.append((g, idx, -(h["times"] * rate), c))
                continue
            gh = gcauses.get(key)
            if gh and gh.get("successes", 0) > 0:
                # 全局"参考"档同样要求跨项目至少成功过一次（0 成功=无推荐信号，
                # 与 build_hint 的 score>0 过滤一致；优先/置末仍只认本项目历史）。
                grate = (gh["successes"] / gh["times"]) if gh["times"] else 0.0
                c["experience_match"] = {"cause_key": key, "times": gh["times"],
                                         "successes": gh["successes"],
                                         "success_rate": round(grate, 3),
                                         "action": "参考（跨项目通用根因，未验证于本项目）",
                                         "source": "通用经验（同语言）"}
                touched += 1
                global_hits += 1
                bucketed.append((1, idx, 0.0, c))
                continue
            bucketed.append((1, idx, 0.0, c))
        if touched == 0:
            return list(candidates), ""
        bucketed.sort(key=lambda t: (t[0], t[1] if t[0] != 0 else 0, t[2], t[1]))
        out = [t[3] for t in bucketed]
        if global_hits:
            note = ("经验重排：%d/%d 候选命中历史（本项目 %d + 通用经验 %d；"
                    "优先验证/置末只按本项目判定，通用层仅供参考）"
                    % (touched, len(candidates), touched - global_hits, global_hits))
        else:
            note = "经验重排：%d/%d 候选命中项目历史（优先验证/置末规则见经验库元数据）" % (touched, len(candidates))
        return out, note

    # ---------- L5.1 阶段4：项目画像 ----------

    def _write_profile(self, proj: Dict[str, Any]) -> Path:
        pid = str(proj.get("project_id") or "")
        prof_dir = _default_profiles_dir()
        prof_dir.mkdir(parents=True, exist_ok=True)
        target = prof_dir / ("%s.json" % project_profile_hash(pid))
        causes_sorted = sorted(proj["causes"].items(),
                               key=lambda kv: (-(kv[1]["times"] * (kv[1]["successes"] / kv[1]["times"] if kv[1]["times"] else 0)),
                                               -kv[1]["times"]))
        top_causes = [dict(self._cause_row(k, c), rank=i + 1)
                      for i, (k, c) in enumerate(causes_sorted[:10])]
        solutions = []
        for k, c in causes_sorted:
            if c.get("sample_solution"):
                solutions.append({"cause_key": k, "solution": c["sample_solution"],
                                  "times": c["times"], "last_ts": c["last_ts"]})
            if len(solutions) >= 5:
                break
        langs = sorted(proj["languages"].items(), key=lambda x: -x[1])
        ets = sorted(proj["error_types"].items(), key=lambda x: -x[1])
        avg = (proj["duration_sum"] / proj["duration_n"]) if proj["duration_n"] else None
        avg_ok = ((proj["success_duration_sum"] / proj["success_duration_n"])
                  if proj["success_duration_n"] else None)
        profile = {
            "schema": "moray.project_profile.l51-1",
            "project_id": pid,
            "profile_hash": project_profile_hash(pid),
            "records": proj["records"],
            "successes": proj["successes"],
            "success_rate": round(proj["successes"] / proj["records"], 3) if proj["records"] else 0.0,
            "high_freq_language": (langs[0][0] if langs else "unknown"),
            "languages": dict(langs),
            "top_error_types": [{"error_type": k, "times": v} for k, v in ets[:10]],
            "top_root_causes": top_causes,
            "common_solutions": solutions,
            "avg_duration_sec": round(avg, 1) if avg is not None else None,
            "avg_duration_sec_success": round(avg_ok, 1) if avg_ok is not None else None,
            "updated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        }
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(json.dumps(profile, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(target)
        return target

    @staticmethod
    def read_profile(project_id: str) -> Optional[Dict[str, Any]]:
        """读项目画像（无/坏 → None；调用方按"无画像=旧行为"降级）。"""
        try:
            p = _default_profiles_dir() / ("%s.json" % project_profile_hash(project_id))
            if not p.is_file():
                return None
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (OSError, json.JSONDecodeError):
            return None

    # ---------- 运维辅助 ----------

    def stats(self) -> Dict[str, Any]:
        recs = self._load()
        projs = sorted({str(r.get("project_id")) for r in recs if r.get("project_id")})
        out: Dict[str, Any] = {"path": str(self.path), "records": len(recs), "projects": projs}
        if _evolution_on():
            meta = self._load_meta()
            gb = (meta or {}).get("global") or {}
            out["evolution"] = {
                "meta_path": str(self.meta_path),
                "meta_ok": meta is not None,
                "meta_updated_at": (meta or {}).get("updated_at", ""),
                "meta_records": (meta or {}).get("records", 0),
                "profiles_dir": str(_default_profiles_dir()),
                # [L5.3] 分层可视化：闸状态 + 全局桶根因数与总次数
                "global_on": _global_on(),
                "global_causes": len(gb.get("causes") or {}),
                "global_records": sum(int(c.get("times") or 0)
                                      for c in (gb.get("causes") or {}).values()),
            }
        return out


if __name__ == "__main__":  # 离线冒烟（临时文件，不碰正式库/正式画像目录）
    import tempfile
    tmpdir = Path(tempfile.mkdtemp(prefix="mcp_exp_smoke_"))
    os.environ["MORAY_EXPERIENCE_DB"] = str(tmpdir / "exp.jsonl")
    os.environ["MORAY_EXPERIENCE_STATS"] = str(tmpdir / "experience_stats.json")
    os.environ["MORAY_PROJECT_PROFILES_DIR"] = str(tmpdir / "profiles")
    tmp = Path(os.environ["MORAY_EXPERIENCE_DB"])
    eb = ExperienceBase(str(tmp))
    for _ in range(3):
        eb.record({"project_id": "A", "error_type": "ModuleNotFoundError",
                   "root_cause_category": "虚拟环境未安装依赖", "error_snippet": "No module named 'foo'",
                   "solution": "pip install foo", "duration_sec": 12.5, "success": True})
    eb.record({"project_id": "B", "error_type": "ModuleNotFoundError",
               "root_cause_category": "循环导入", "solution": "拆模块", "success": False})
    hint = eb.build_hint("A", "ModuleNotFoundError")
    assert "虚拟环境未安装依赖" in hint or "依赖模块未安装" in hint, hint
    assert "循环导入" not in hint, hint
    q = eb.query("A", "ModuleNotFoundError")
    assert q and q[0]["times"] == 3 and q[0]["score"] == 3, q
    assert eb.record({"project_id": "", "error_type": "x"}) is None
    # L5.1 冒烟：Top5 元数据 O(1)、重排、画像
    top = eb.top_meta("A", "ModuleNotFoundError", top_n=5)
    assert top and top[0]["times"] == 3, top
    cands = [{"cause": "先试试重启", "source": "llm"},
             {"cause": "No module named foo（依赖模块未安装）", "source": "rules"}]
    ranked, note = eb.rank_candidates("A", cands)
    assert note and any(t.get("experience_match") for t in ranked), (ranked, note)
    prof = eb.read_profile("A")
    assert prof and prof["records"] == 3 and prof["high_freq_language"] == "python", prof
    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)
    print("EXPERIENCE SMOKE OK (L5.1)")
