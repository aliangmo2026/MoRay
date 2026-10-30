# -*- coding: utf-8 -*-
"""L5.2 主动日志监控（2026-09-28）：后台 asyncio 循环 + 告警去重 + 本地落盘状态。

红线 7 / 任务书 2.1：**默认关闭**。MONITOR_ENABLED=false 时后端进程里根本不存在
循环协程；只有用户在对话里说"开启监控"→ POST /api/monitor/enable → start() 动态
create_task；disable → cancel。重启回关（诚实，不假装恢复）。

复用（审计 l5253_audit.md §Q1/§Q3/§Q4）：
  * 读尾部 + 堆栈提取 = adapters/local_log_adapter.py 的静态件（_tail_lines/_extract_traces）；
  * 告警级别正则在 _ERROR_LINE_RE 语义上收紧（MONITOR_ALERT_LEVEL=ERROR|FATAL）；
  * 根因分析 = adapters/iris_mcp_client（log-intent 同款 iris_analyze_text）；
  * 不自动触发任务——"立即修复"按钮由前端复用既有 POST /api/tasks/run-error-fix。

去重与留痕：错误指纹（数字/十六进制地址/引号内容归一后 sha1）持久化在
work/monitor_state/alerts.json（tmp→replace 原子写，仓内 sidecar 范式），
**重启后仍不重复告警**；告警队列 pending/acked 两态，前端轮询只拿 pending。

陷阱规避（[[moray-testclient-lifespan-zombie-trap]]）：sleep 不放 finally；
cancel 后 await 收尸；循环内一切异常吞掉记 warning（监控绝拖垮主服务）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config
from adapters.local_log_adapter import LocalLogAdapter, _STACK_HEAD_RE

MORAY_ROOT = Path(__file__).resolve().parents[2]
STATE_DIR = MORAY_ROOT / "work" / "monitor_state"
STATE_FILE = STATE_DIR / "alerts.json"

TAIL_LINES = 200                      # 任务书 2.2：最新日志尾部 200 行
MAX_FILES_PER_DIR = 3                 # 每目录每轮最多看 3 个最新 .log（防洪泛）
MAX_NEW_ALERTS_PER_SCAN = 3           # 单轮新增告警上限
MAX_ALERTS_KEPT = 50                  # 落盘队列上限（超额先丢最旧 acked）
MAX_SEEN = 500                        # 指纹表上限（LRU 丢最旧）

logger = logging.getLogger("moray-monitor")

_LEVEL_RES = {
    "ERROR": re.compile(r"\b(ERROR|FATAL|CRITICAL|SEVERE|PANIC)\b", re.IGNORECASE),
    "FATAL": re.compile(r"\b(FATAL|CRITICAL|PANIC|ABORTED|Segmentation fault)\b",
                        re.IGNORECASE),
}
_HEX_ADDR_RE = re.compile(r"0x[0-9a-fA-F]+")
_DIGIT_RE = re.compile(r"\d+")
_QUOTED_RE = re.compile(r"'[^']*'|\"[^\"]*\"")


def _fingerprint(text: str) -> str:
    """错误行/堆栈块 → 稳定指纹：地址/数字/引号内容归一（同一错误反复出现不重复告警）。"""
    s = _HEX_ADDR_RE.sub("#", str(text or ""))
    s = _QUOTED_RE.sub("''", s)
    s = _DIGIT_RE.sub("#", s)
    s = re.sub(r"\s+", " ", s).strip().lower()[:300]
    return hashlib.sha1(s.encode("utf-8", "replace")).hexdigest()[:16]


def resolve_scan_dirs(extra: Optional[str] = None) -> List[Path]:
    """监控目录 = 配置 MONITOR_LOG_DIRS（相对路径按 MoRay 根解析）+ 本次 enable 传入的项目目录。
    传 project_dir 时优先其 logs/ 子目录（与 LocalLogAdapter 源1 同语义），退回目录本身。"""
    dirs: List[Path] = []
    for d in (config.MONITOR_LOG_DIRS or ()):
        p = Path(d)
        dirs.append(p if p.is_absolute() else MORAY_ROOT / d)
    if extra:
        p = Path(str(extra).strip().strip('"'))
        if p.is_dir():
            sub = p / "logs"
            dirs.append(sub if sub.is_dir() else p)
    out: List[Path] = []
    seen: set = set()
    for d in dirs:
        try:
            key = str(d.resolve()).lower()
        except OSError:
            continue
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out


def _load_state() -> Dict[str, Any]:
    try:
        st = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(st, dict) and isinstance(st.get("seen"), dict) \
                and isinstance(st.get("alerts"), list):
            return st
    except (OSError, json.JSONDecodeError):
        pass
    return {"seen": {}, "alerts": []}


def _save_state(st: Dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(STATE_FILE)


class MonitorManager:
    """单进程单例：状态只在事件循环线程内变更（路由 handler 与 loop 同循环，无需锁）。"""

    def __init__(self) -> None:
        self.enabled = False
        self.interval = int(getattr(config, "MONITOR_INTERVAL", 60) or 60)
        self.level = str(getattr(config, "MONITOR_ALERT_LEVEL", "ERROR"))
        self.extra_dir = ""
        self.state = _load_state()
        self._task: Optional[asyncio.Task] = None
        self.last_scan = ""
        self.last_error = ""
        self.scans = 0

    # ---------------- 开关 ----------------

    async def enable(self, project_dir: str = "", interval: Optional[int] = None) -> None:
        self.extra_dir = str(project_dir or "").strip().strip('"')
        if interval is not None:
            self.interval = max(2, int(interval))
        await self.stop()
        self.enabled = True
        self._task = asyncio.create_task(self._loop())
        logger.warning("[monitor] enabled interval=%ss level=%s extra=%s",
                       self.interval, self.level, self.extra_dir or "-")

    async def disable(self) -> None:
        await self.stop()
        self.enabled = False
        logger.warning("[monitor] disabled")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    # ---------------- 主循环 ----------------

    async def _loop(self) -> None:
        while True:
            try:
                await self.scan_once()
            except Exception as e:  # noqa: BLE001  监控任何异常绝不外溢（红线：不拖垮服务）
                self.last_error = "%s: %s" % (type(e).__name__, e)
                logger.warning("[monitor] scan failed: %s", self.last_error)
            # sleep 必须在 try/finally 之外（僵尸循环教训）：取消要立刻退出而不是睡满周期
            await asyncio.sleep(self.interval)

    async def scan_once(self) -> None:
        """一轮扫描：目录 → 最新 .log 尾部 200 行 → 级别过滤 → 指纹去重 → 新错误入队+Iris 分析。"""
        from knowledge.experience_base import ExperienceBase, _EXC_TOKEN_RE
        alert_re = _LEVEL_RES.get(self.level if self.level in _LEVEL_RES else "ERROR",
                                  _LEVEL_RES["ERROR"])
        new_found: List[Dict[str, Any]] = []
        for d in resolve_scan_dirs(self.extra_dir):
            if not d.is_dir():
                continue
            # 告警归属的项目根：扫 {project}/logs（与 LocalLogAdapter 源1 同语义）时回退父目录，
            # 否则"立即修复"把 project_dir 提交到 logs/ 里找源码会空手而归。
            proj_root = d.parent if d.name.lower() == "logs" else d
            try:
                files = sorted((f for f in d.glob("*.log") if f.is_file()),
                               key=lambda f: f.stat().st_mtime, reverse=True)
            except OSError:
                continue
            for f in files[:MAX_FILES_PER_DIR]:
                lines = LocalLogAdapter._tail_lines(f, TAIL_LINES)
                if not lines:
                    continue
                # 候选 = 堆栈块（_extract_traces 现成规则）+ 独立错误行。
                # 相邻合并（冒烟抓出）：ERROR/FATAL 行若紧跟 Traceback 块头，它是块的引导行，
                # 单独再告一条会把同一个错误拆成两卡（违"重复错误不重复告警"精神）。
                cands = []
                for idx, ln in enumerate(lines):
                    if not alert_re.search(ln):
                        continue
                    following = [l.strip() for l in lines[idx + 1:idx + 3] if l.strip()]
                    if following and _STACK_HEAD_RE.match(following[0]):
                        continue
                    cands.append(ln)
                for blk in LocalLogAdapter._extract_traces(lines):
                    # 级别闸对块同样生效：Python Traceback 行本身不含 "ERROR" 字样，
                    # ERROR 阈值下异常堆栈天然是错误级 → 恒过闸；
                    # FATAL 阈值下必须块内确有 FATAL 级字样（冒烟抓出的绕闸缺陷）。
                    if self.level == "FATAL" and not alert_re.search(blk):
                        continue
                    cands.append(blk)
                for block in cands:
                    fp = _fingerprint(block)
                    if fp in self.state["seen"]:
                        continue
                    first = block.strip().splitlines()[0][:300] if block.strip() else ""
                    tok = _EXC_TOKEN_RE.search(block)
                    rec = {
                        "id": uuid.uuid4().hex[:10],
                        "fp": fp,
                        "ts": datetime.now(timezone.utc).astimezone().isoformat(
                            timespec="seconds"),
                        "file": str(f),
                        "project_dir": str(proj_root),
                        "level_line": first,
                        "stack": block.strip()[:2000],
                        "error_type": tok.group(1) if tok else "",
                        "analysis": None,
                        "analysis_error": "",
                        "status": "pending",
                        "action": "",
                    }
                    new_found.append(rec)
                    self.state["seen"][fp] = {"ts": rec["ts"], "alert_id": rec["id"]}
                    if len(new_found) >= MAX_NEW_ALERTS_PER_SCAN:
                        break
                if len(new_found) >= MAX_NEW_ALERTS_PER_SCAN:
                    break
            if len(new_found) >= MAX_NEW_ALERTS_PER_SCAN:
                break
        self.scans += 1
        self.last_scan = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        for rec in new_found:
            rec["analysis"], rec["analysis_error"] = await analyze_error(
                rec["stack"] or rec["level_line"], Path(rec["project_dir"]).name)
            self.state["alerts"].append(rec)
            logger.warning("[monitor] NEW ALERT %s %s (%s)",
                           rec["id"], rec["error_type"] or rec["level_line"][:60], rec["file"])
        # 队列修剪：超额先丢最旧已 ack 的
        while len(self.state["alerts"]) > MAX_ALERTS_KEPT:
            drop_i = next((i for i, a in enumerate(self.state["alerts"])
                           if a.get("status") != "pending"), 0)
            self.state["alerts"].pop(drop_i)
        while len(self.state["seen"]) > MAX_SEEN:
            oldest = min(self.state["seen"].items(), key=lambda kv: str(kv[1].get("ts") or ""))
            self.state["seen"].pop(oldest[0], None)
        if new_found:
            self.save()

    def save(self) -> None:
        try:
            _save_state(self.state)
        except Exception as e:  # noqa: BLE001
            logger.warning("[monitor] state save failed: %s", e)

    # ---------------- 读侧 ----------------

    def pending_alerts(self) -> List[Dict[str, Any]]:
        return [dict(a) for a in self.state["alerts"] if a.get("status") == "pending"]

    def ack(self, alert_id: str, action: str) -> Optional[Dict[str, Any]]:
        for a in self.state["alerts"]:
            if a.get("id") == alert_id and a.get("status") == "pending":
                a["status"] = "acked"
                a["action"] = str(action or "ignored")[:20]
                a["acked_at"] = datetime.now(timezone.utc).astimezone().isoformat(
                    timespec="seconds")
                self.save()
                return dict(a)
        return None

    def status_view(self) -> Dict[str, Any]:
        return {
            "enabled": bool(self.enabled),
            "interval_sec": self.interval,
            "alert_level": self.level,
            "config_defaults": {
                "enabled": bool(getattr(config, "MONITOR_ENABLED", False)),
                "interval": int(getattr(config, "MONITOR_INTERVAL", 60)),
                "log_dirs": list(getattr(config, "MONITOR_LOG_DIRS", ())),
                "alert_level": str(getattr(config, "MONITOR_ALERT_LEVEL", "ERROR")),
            },
            "scan_dirs": [str(d) for d in resolve_scan_dirs(self.extra_dir)],
            "extra_dir": self.extra_dir,
            "state_file": str(STATE_FILE),
            "last_scan": self.last_scan,
            "last_error": self.last_error,
            "scans": self.scans,
            "pending": len(self.pending_alerts()),
            "seen_count": len(self.state["seen"]),
        }


async def analyze_error(text: str, project_name: str) -> tuple:
    """Iris 根因分析（log-intent 同款工具），并附经验库优先假设（L5.3 冷启动自动带通用层）。
    失败不抛：返回 (None, 原因)——告警本身照发，分析栏如实留空。"""
    if not text or not text.strip():
        return None, "无可分析文本"
    try:
        from adapters.iris_mcp_client import IrisMCPClient
        cli = IrisMCPClient()
        if not cli.cfg.get("enabled"):
            return None, "Iris MCP 已禁用（MORAY_IRIS_MCP_ENABLED=0）"
        env = await cli.call_tool("iris_analyze_text", {"text": text[:4000]})
        if env.get("code") != "OK":
            return None, str((env.get("data") or {}).get("message")
                             or env.get("code") or "Iris 分析失败")
        data = env.get("data") or {}
        cands = data.get("root_cause_candidates") or []
        diag = data.get("diagnosis") or {}
        out = {
            "root_cause": str((cands[0].get("cause") if cands and isinstance(cands[0], dict)
                               else diag.get("root_cause")) or ""),
            "suggestion": str(diag.get("plan") or
                              (cands[0].get("fix") if cands and isinstance(cands[0], dict)
                               else "") or ""),
            "severity": data.get("severity") or "",
            "candidates": cands[:3],
            "source": "iris",
        }
        try:
            from knowledge.experience_base import ExperienceBase as _EB
            hint = _EB().build_hint(project_name, None, 3)
            if hint:
                out["experience_hint"] = hint
        except Exception:  # noqa: BLE001
            pass
        return out, ""
    except Exception as e:  # noqa: BLE001
        return None, "%s: %s" % (type(e).__name__, str(e)[:160])


#: 模块单例（monitor_routes 与未来 lifespan 自启共用）
manager = MonitorManager()
