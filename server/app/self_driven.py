# -*- coding: utf-8 -*-
"""自驱引擎 v2（多引擎路由 + 执行闭环）self_driven（2026-10-23）：

自驱引擎第一步——读最近一次自我叙事落库的心智状态（memory_bank 中
kind='self_narrative' 行 content JSON 里的 mental_state 字段），让本地 LLM
据此生成一个简短改进目标**并选择执行引擎**，封装成结构化目标卡进待审批队列
（pending_approval）。[v2] 产出改 JSON 契约 {"goal", "target_engine"}：
json_repair 容错解析（self_narrative._parse_narrative_json 同款），target_engine
白名单 ["zcode", "laya"] 外一律回落 zcode（解析失败整段退化纯文本当目标）。

级别边界（与 self_narrative 同口径，勿扩）：
- 对 memory_bank：本模块只 SELECT 读心智状态，绝不写、绝不改（v2 闭环自省
  回写 kind='self_observation' 由 synapse_bus._handler_self_driven_close_loop
  落库，不经本模块——本模块与 memory_bank 的关系仍是单向只读）；
- 产物不落 memory_bank：目标卡自带 status 流转语义（pending_approval →
  dispatched → done/failed），memory_bank 只追加无 UPDATE，装不下审批状态机
  → 落本模块私有状态文件（config.DATA_DIR/self_driven_state.json，与
  memory.sqlite3/synapse.sqlite3 同目录惯例），每日配额与目标队列同文件；
- fail-open：读不到心智状态 / LLM 失败 / 产出为空一律返回 None（"没生成"），
  事实原因只进日志，绝不把异常抛给调用方；唯熔断落盘失败 fail-close（见下）；
- 零自动**执行**（硬约束不变）：目标只生成到 pending_approval，绝不自动派发
  到执行引擎；派发仍要人点 approve；生成入口两条：
  ① [v1] POST /api/self_driven/generate 手动触发；
  ② [v3 2026-10-31] self_check_loop 每轮尾调 maybe_generate_goal()（3h 冷却 +
     日配额双闸）——本条是 v3 唯一放开的自动面：**自动生目标，不自动执行**。
  v3 之前的 docstring 写的是"绝不接入自省循环"，那导致目标队列 8 天零新增
  （最后一条生成日 2026-10-23，状态文件 daily.date 即证据）；放开生成面、
  守住执行面，是本轮修的根因。
  [v2] 派发仍由前端手动发起：POST /api/self_driven/approve 只做状态流转
  （pending_approval→dispatched 并登记引擎 task_id），绝不触发执行；执行结果
  由引擎收尾事件经 synapse handler 回写（close_dispatched_goal 置 done/failed）；
  POST /api/self_driven/reject = 删卡出队（忽略）；
- LLM 调用零新增配置面：复用 reflection 同组 config（REFLECTION_API/BASE_URL/
  MODEL/TIMEOUT/NUM_CTX），与 self_narrative._llm_narrate 完全同款 httpx 双协议。

安全熔断（防跑飞，两道）：
- 每日限额 _DAILY_GOAL_LIMIT = 3（挂钟日历日）：配额计数落状态文件持久化，
  跨进程重启依然成立——进程级计数器重启即清零，等于没有熔断；
- 在飞闸 _goal_inflight（self_narrative._narrative_inflight 同款）：生成中不
  重复触发。check/set 同步无 await，事件循环单线程内天然原子；也因此
  **状态文件互斥锁绝不跨 await**——async 里持锁等 await 会把整个事件循环
  冻死，_STATE_LOCK 只包 _record_goal 这种纯同步段。
配额完整性 fail-close：_record_goal 落盘失败/超限 → generate_goal 返回 None
不发卡——配额没记上却发卡，限额会被绕穿，熔断必须宁可不生成。
"""
import asyncio
import datetime
import json
import logging
import os
import re
import subprocess
import threading
import time
import uuid

import httpx
from fastapi import APIRouter, Request

try:
    import json_repair  # [v2] self_narrative 同款容错；venv 已装但 requirements 未声明，故 try-import
except ImportError:  # noqa: BLE001 - 无 json_repair 时退化裸 json.loads（解析失败整卡按纯文本兜底）
    json_repair = None

from . import config

logger = logging.getLogger("moray.self_driven")

#: 心智状态来源 kind（self_narrative 落库产物：content JSON 的 mental_state 字段）
_NARRATIVE_KIND = "self_narrative"
#: 每日目标生成上限（安全熔断核心：挂钟日历日，落盘计数跨重启成立）
_DAILY_GOAL_LIMIT = 3
#: 心智状态注入 prompt 的字符上限（防跑飞；正常 mental_state 才几百字符）
_MENTAL_STATE_CHARS = 2000
#: 目标文本字符上限（prompt 要求 50 字，这里只当跑马护栏给 4 倍余量）
_GOAL_TEXT_MAX = 200
#: [v3] 可证伪三件套的清洗上限（claim/prediction/verify_cmd）
_CLAIM_TEXT_MAX = 200
_PRED_TEXT_MAX = 300
_VERIFY_TEXT_MAX = 200

#: 状态文件（每日配额 + 待审批队列单文件）。
#: 路径/原子写完全照 log_monitor.STATE_FILE 与 task_routes 落盘范式：
#: 模块常量 Path + with_suffix('.json.tmp') 临时文件 + replace 原子换名。
STATE_DIR = config.DATA_DIR
STATE_FILE = STATE_DIR / "self_driven_state.json"

#: [v2] 执行引擎白名单：target_engine 只认这两个短名（与 local_adapter 注册表
#: NAME 对齐），其余取值（大小写漂移/带后缀/编造名）一律回落默认引擎。
_ENGINE_WHITELIST = ("zcode", "laya")
_DEFAULT_ENGINE = "zcode"

#: 目标生成 prompt。{mental_state} 用 str.replace 注入——模板与 JSON 花括号会跟
#: str.format 打架，replace 最稳（v2 输出契约示例自带花括号，更不能用 format）。
#: 引擎说明写死在模板里（任务书允许）：LLM 只看得到 mental_state，不喂引擎画像
#: 就只能凭名字猜路由。
_SYSTEM_PROMPT = (
    "你是 MoRay。这是你当前的心智状态：{mental_state}。"
    "可用执行引擎：zcode（擅长写代码、执行命令）；laya（擅长任务拆解与编排）。"
    "你近期已生成过的目标在下面，**禁止重复这些主题**，必须换一个新方向："
    "{recent_goals}。"
    "请根据你最近的失败或不足，提出一个**可被证伪**的改进目标。硬要求："
    "claim 必须是一句能被现实判真假的话——禁止写“加强/注意/重视/确保/持续强化”"
    "这类无法证伪的口号（历史上这么写产出的目标全是空话）；"
    "prediction 必须写清“目标达成时会观测到什么具体数值或文件变化”；"
    "verify_cmd 必须是一行**在本机（Windows）现在就能直接执行**的验证命令，硬约束："
    "① 只许引用 Windows 盘符绝对路径（如 D:\\MoRay\\...）或相对路径，"
    "禁止 /tmp、/var、/home 这类 Linux 路径——本机没有这些目录，写了必定失败；"
    "② 不许用 example.com、your_file、path/to、xxx 这类占位符；"
    "③ 引用的文件必须现在已存在，或就是本目标这一轮要产出、且路径已在目标里写明；"
    "④ 命令必须对失败敏感：文件不存在时要非零退出或输出 0，不能永远成功；"
    "⑤ 只许用 Windows 原生命令（python / type / dir / findstr / powershell），"
    "禁止 find、grep、awk、sed、cat、xargs、touch 这些 Linux 命令，"
    "禁止 2>/dev/null、| grep、`$(...)` 这类 sh 语法——本机是 cmd，写了必定跑不通；"
    "⑥ 命令里出现 Windows 路径时，一律写成 raw 字符串（r'D:\\...'）或正斜杠"
    "（D:/MoRay/...），绝不能在普通字符串里写单反斜杠，否则 \\u 会被当转义而语法错。"
    "只输出一个 JSON 对象，不要任何解释文字、不要代码块标记，严格符合这个形状："
    '{"goal": "一句话目标（不超过50字）", '
    '"claim": "可证伪断言", '
    '"prediction": "预期观测（含具体数值或文件）", '
    '"verify_cmd": "验证命令或文件路径", '
    '"target_engine": "zcode"}'
    "。target_engine 只允许取值 zcode 或 laya。"
)
_USER_PROMPT = "生成我的下一步目标（输出 JSON）。"

#: [v4 2026-10-31] verify_cmd 存在性硬约束。v3 第一张自动卡就把验证指向
#: /tmp/zcode_test_script.py —— Windows 上没有 /tmp，这条验证永远输出 0、
#: 永远打脸，"闭环"永远关不上。v4 在落卡前做静态体检：不合格先带反馈重生成
#: 一次；仍不合格照样发卡，但把原因码写进 verify_problem 字段。宁可如实记录
#: "这张卡不可验证"，也绝不偷偷改写模型原话、更不假装它能验。
_VERIFY_POSIX_MARKERS = ("/tmp/", "/var/", "/home/", "/usr/", "/etc/",
                         "/private/", "/opt/", "/dev/")
_VERIFY_PLACEHOLDERS = ("example.com", "your_file", "yourfile", "path/to",
                        "placeholder", "xxx", "<", ">")
#: [v5 2026-10-31] POSIX 专用命令：在 cmd 上必然"不是内部或外部命令"，**命令层面
#: 就跑不通**——此时 exit code 非零跟预测真假毫无关系，属于噪声。只认命令位
#: （行首 / | && ; ( 之后），避免误伤 python -c 代码体里的普通单词（如变量名 ls）。
_VERIFY_POSIX_CMD_RE = re.compile(
    r"(?:^|\||;|&&|\|\||\()\s*"
    r"(?:find|grep|egrep|awk|sed|xargs|touch|chmod|chown|dirname|basename"
    r"|which|df|du|ps|kill|mkdir\s+-p|rm\s+-)\b")
#: sh 专有语法：命令替换/变量展开，cmd 不认（/dev/null 已被 posix_path 先抓）
_VERIFY_SH_SYNTAX = ("$(", "${", ">&2", "2>&1 &")
#: 允许引用的根：已存在的路径一律合格；不存在的路径必须落在这些根下，才算
#: "本轮要产出的文件"，否则视为幽灵路径。
_VERIFY_ROOTS = ("D:\\MoRay", "E:\\MoRay OS", "C:\\Users\\")
#: verify_cmd 里抽路径的形状：盘符绝对路径 或 POSIX 绝对路径（后者不是放行，
#: 是抓出来判死——为的是日志能说清"你引用的是哪个 Linux 路径"）。POSIX 分支
#: 强制 ≥2 段（`/a/b`），否则 findstr /C:def 这类命令行斜杠参数会被误判成路径。
_VERIFY_PATH_RE = re.compile(r"[A-Za-z]:\\[^\s\"']+|/(?:[^/\s\"']+/)+[^/\s\"']*")
#: `python -c "..."` 的代码体（非贪婪到引号收尾），用来做转义体检。
_PY_C_RE = re.compile(r"""python(?:\.exe)?\s+-c\s+(?P<q>["'])(?P<src>.*?)(?P=q)""", re.S)
#: 与近期目标的字符二元组重合度 ≥ 此值 → 判为同主题重复。
_REPEAT_SIMILARITY = 0.55
#: 喂给 prompt 的近期目标条数（去重窗口）。
_RECENT_GOALS_MAX = 3

#: 在飞闸（进程内存活，重启清零）：生成中不重复触发（self_narrative 同款）
_goal_inflight = False
#: 状态文件读改写互斥（只包纯同步段，绝不跨 await——见模块 docstring）
_STATE_LOCK = threading.Lock()

#: [v3] 循环挂点冷却：self_check_loop 每 30min 一跳，若无冷却，日配额 3 条会在
#: 开机后 1.5 小时内被打光（突刺），"每日一条目标"的节奏彻底失效。
#: 用 time.monotonic（self_model / _hippo 同款）：进程重启即清零 → 首轮必放行，
#: 重启后立刻能看到自驱产出；日配额仍由状态文件持久化兜底，跨重启不绕穿。
_LOOP_COOLDOWN_SEC = 3 * 3600
_last_generate_monotonic = 0.0


# ---- 状态文件（配额 + 待审批队列，JSON 单文件） ----

def _load_state() -> dict:
    """读状态文件 → {"queue": [...], "daily": {...}, "verdict_log": [...]}。
    缺失/损坏/形状不对一律回默认空状态（fail-open，不抛）。

    [v5] verdict_log 也在这里归一化：只留 dict 行、只留最后 _VERDICT_LOG_MAX 条。
    键名白名单是刻意的——但这意味着**新增状态键必须同时改这里**，否则
    _load_state → _save_state 的回写会把新键静默抹掉（v5 台账第一版就踩了：
    写进去的 250 条读完只剩 1 条）。"""
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8", errors="replace"))
        if isinstance(data, dict):
            queue = data.get("queue")
            daily = data.get("daily")
            vlog = data.get("verdict_log")
            vlog = ([e for e in vlog if isinstance(e, dict)][-_VERDICT_LOG_MAX:]
                    if isinstance(vlog, list) else [])
            return {"queue": queue if isinstance(queue, list) else [],
                    "daily": daily if isinstance(daily, dict) else {},
                    "verdict_log": vlog}
    except (OSError, ValueError):
        pass  # 从未写过（FileNotFoundError）/坏 JSON（JSONDecodeError）= 空状态
    return {"queue": [], "daily": {}, "verdict_log": []}


def _save_state(state: dict) -> bool:
    """原子写状态文件（log_monitor._save_state 同款：临时文件写完再 replace，
    防拦腰截断出坏 JSON）。失败返回 False，不抛。"""
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(STATE_FILE)
        return True
    except Exception as e:  # noqa: BLE001 - 调用方按 fail-close 处理，这里只记账
        logger.warning("[self_driven] 状态文件写入异常：%s: %s",
                       type(e).__name__, str(e)[:120])
        return False


def _today() -> str:
    """挂钟日历日（本地时区，与 memory_bank localtime 口径一致）。"""
    return datetime.datetime.now().strftime("%Y-%m-%d")


def _quota_info(state: dict) -> dict:
    """今日配额水位 {"date", "generated_today", "limit"}。
    换日自动归零（不写盘，写盘只发生在 _record_goal）——状态文件里的旧
    日期就是天然的"上一次生成日"记录。"""
    daily = state.get("daily") if isinstance(state.get("daily"), dict) else {}
    date = _today()
    count = 0
    if daily.get("date") == date:
        try:
            count = int(daily.get("count") or 0)
        except (TypeError, ValueError):
            count = 0
    return {"date": date, "generated_today": count, "limit": _DAILY_GOAL_LIMIT}


def _record_goal(card: dict) -> bool:
    """配额 +1 并把目标卡追加进队列，原子写回状态文件。成功 True / 失败 False。
    熔断双重保险：写盘前再查一次今日限额（generate_goal 的 LLM 前检查拦
    浪费推理开销，这里守最终一致性——配额没记上就发卡 = 限额可被绕穿）。
    纯同步段：_STATE_LOCK 绝不跨 await。"""
    with _STATE_LOCK:
        state = _load_state()
        quota = _quota_info(state)
        if quota["generated_today"] >= quota["limit"]:
            logger.warning("[self_driven] 拒记：今日配额已满 %d/%d",
                           quota["generated_today"], quota["limit"])
            return False
        state["daily"] = {"date": quota["date"],
                          "count": quota["generated_today"] + 1}
        state["queue"] = list(state.get("queue") or []) + [card]
        if not _save_state(state):
            logger.warning("[self_driven] 拒发卡：配额/队列落盘失败（熔断 fail-close）")
            return False
        return True


def _remove_goal(task_id: str) -> int:
    """从队列移除指定 task_id 的目标卡（approve/reject 两路由共用）。
    安全读改写：_STATE_LOCK 包住 load→filter→save 纯同步段，写回走
    _save_state 原子换名（与 _record_goal 同一把锁，互不交叉读半截状态）。
    返回移除条数；task_id 空/落盘失败 = -1（fail-open 不抛）；队列里没有
    该项 = 0（幂等：重复 approve/reject 也回 ok）。"""
    tid = str(task_id or "").strip()
    if not tid:
        return -1
    with _STATE_LOCK:
        state = _load_state()
        before = state.get("queue") or []
        after = [g for g in before
                 if not (isinstance(g, dict) and str(g.get("task_id")) == tid)]
        removed = len(before) - len(after)
        if removed == 0:
            return 0
        state["queue"] = after
        if not _save_state(state):
            logger.warning("[self_driven] 出队落盘失败 task_id=%s", tid)
            return -1
        return removed


def mark_dispatched(task_id: str, engine_task_id: str) -> dict | None:
    """[v2] approve 状态流转：pending_approval → dispatched，登记引擎 task_id
    （闭环匹配键）。不删卡——执行结果要靠 engine_task_id 回写（close_dispatched_goal）。

    终态只进不出：已 done/failed 的卡不回退（事件先到的竞态下终态事实优先），
    原样返回让调用方按成功计；重复 approve 幂等刷新 engine_task_id（后写胜）。
    返回更新后的卡片 dict；task_id 未命中/落盘失败返回 None（fail-open 不抛）。"""
    tid = str(task_id or "").strip()
    etid = str(engine_task_id or "").strip()[:64]
    if not tid or not etid:
        return None
    with _STATE_LOCK:
        state = _load_state()
        card = None
        for g in state.get("queue") or []:
            if isinstance(g, dict) and str(g.get("task_id")) == tid:
                card = g
                break
        if card is None:
            return None
        if str(card.get("status") or "") not in ("done", "failed"):
            card["status"] = "dispatched"
            card["engine_task_id"] = etid
            if not _save_state(state):
                logger.warning("[self_driven] 派发登记落盘失败 task_id=%s", tid)
                return None
        return dict(card)


def close_dispatched_goal(engine_task_id: str, success: bool,
                          note: str = "") -> dict | None:
    """[v2] 引擎终态回写：engine_task_id 匹配的 dispatched 卡 → done/failed。

    幂等：只流转 status=='dispatched' 的卡（重复事件/已终态返回 None 不二写）；
    无匹配卡也返回 None——普通聊天任务/编排工具子任务的引擎事件同样会进来，
    无卡即无事（不是错误，不记 warning）。note 留事件正文痕（截 200）。
    成功返回更新后的卡片 dict（含 goal，供调用方写 self_observation）；
    落盘失败返回 None（fail-open 不抛）。"""
    etid = str(engine_task_id or "").strip()
    if not etid:
        return None
    with _STATE_LOCK:
        state = _load_state()
        card = None
        for g in state.get("queue") or []:
            if (isinstance(g, dict)
                    and str(g.get("engine_task_id") or "") == etid
                    and str(g.get("status") or "") == "dispatched"):
                card = g
                break
        if card is None:
            return None
        card["status"] = "done" if success else "failed"
        card["result_note"] = str(note or "").strip()[:200]
        if not _save_state(state):
            logger.warning("[self_driven] 闭环回写落盘失败 engine_task_id=%s", etid)
            return None
        return dict(card)


# ---- 失败重试/复盘 v3（2026-10-24 深海阶段二） ----
#: 谱系重试上限：retry_count 达到该值的失败卡直接置 abandoned，不再生成新卡。
#: 防跑飞第三道闸（与每日配额、self_state_log 熔断并行）：重试卡必须人工审批
#: 才会执行，自动面每谱系最多 2 跳。
_RETRY_MAX = 2
#: 失败原因字符钳制（进 prompt 与新卡 retry_reason 的统一口径）
_RETRY_REASON_CHARS = 400

_RETRY_SYSTEM_PROMPT = (
    "你是 MoRay 自驱引擎的失败复盘器。刚才一次自驱目标执行失败，请基于失败原因"
    "给出一个修复式的下一步目标（换一个可行做法，而不是原样重试）。只输出一个 "
    "JSON 对象，不要任何解释或代码块标记，严格符合这个形状："
    '{"goal": "新的改进目标（不超过50字）", "target_engine": "zcode 或 laya"}'
    "。target_engine 只允许取值 zcode 或 laya。"
)


def _build_retry_messages(goal: str, engine: str, reason: str) -> list:
    """失败卡三要素（原目标/引擎/失败原因）→ messages。纯函数零 IO（单测惯例）。"""
    user = ("失败的原目标：%s\n执行引擎：%s\n失败原因（引擎输出摘录）：\n%s\n"
            "请给出修复式新目标的 JSON。"
            % (str(goal or "")[:200], str(engine or "zcode"),
               str(reason or "")[:_RETRY_REASON_CHARS]))
    return [{"role": "system", "content": _RETRY_SYSTEM_PROMPT},
            {"role": "user", "content": user}]


async def retry_handler(engine_task_id: str, failure_reason: str) -> dict | None:
    """[v3 失败重试闭环] 引擎终态失败事件 → 找到对应目标卡 → 谱系内重规划。

    流程：按 engine_task_id 找卡（close_loop 与本 handler 同事件并行、先后不限，
    故不要求卡已置 failed，只跳过 done/abandoned 终态）→ retry_count >=
    _RETRY_MAX 置 abandoned（终态只进不出，mark_dispatched 同语义）→ 否则喂
    LLM（复用 _llm_goal/_parse_goal_payload，JSON 契约同 v2）生成修复式新卡：
    status=pending_approval 人工审批，retry_of=父卡 task_id、retry_count+1、
    retry_reason 留痕。重试卡不计每日配额（防跑飞由谱系上限承担，审批人是
    最后一道闸）。

    fail-open：卡不存在（普通聊天任务的失败事件）/LLM 失败/产出为空/落盘失败
    一律返回 None 只记日志，绝不抛给 synapse 派发器。"""
    tid = str(engine_task_id or "").strip()
    reason = str(failure_reason or "").strip()[:_RETRY_REASON_CHARS]
    if not tid:
        return None
    with _STATE_LOCK:
        state = _load_state()
        card = None
        for g in state.get("queue") or []:
            if isinstance(g, dict) and str(g.get("engine_task_id") or "") == tid:
                card = g
                break
        if card is None:
            return None  # 非 self-driven 引擎任务的失败：静默跳过（close_loop 同口径）
        if str(card.get("status") or "") in ("done", "abandoned"):
            return None
        retry_count = int(card.get("retry_count") or 0)
        if retry_count >= _RETRY_MAX:
            card["status"] = "abandoned"
            card["abandoned_reason"] = reason[:200]
            if not _save_state(state):
                logger.warning("[self_driven] abandoned 落盘失败 engine_task_id=%s", tid)
                return None
            logger.warning("[self_driven] 重试超限置 abandoned task_id=%s retry_count=%d",
                           card.get("task_id"), retry_count)
            return dict(card)

    # LLM 重规划（_STATE_LOCK 绝不跨 await：此处锁已释放，await 安全）
    messages = _build_retry_messages(str(card.get("goal") or ""),
                                     str(card.get("target_engine") or _DEFAULT_ENGINE),
                                     reason)
    raw, err = await _llm_goal(messages)
    if raw is None:
        logger.warning("[self_driven] 重试规划 LLM 失败（engine_task_id=%s）：%s",
                       tid, err)
        return None
    goal, engine = _parse_goal_payload(raw)
    if not goal:
        logger.warning("[self_driven] 重试规划产出为空（engine_task_id=%s）：%r",
                       tid, str(raw)[:60])
        return None
    new_card = {
        "task_id": str(uuid.uuid4()),
        "goal": goal,
        "target_engine": engine,
        "engine_task_id": "",
        "status": "pending_approval",
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "retry_of": str(card.get("task_id") or ""),
        "retry_count": retry_count + 1,
        "retry_reason": reason[:200],
    }
    with _STATE_LOCK:
        state = _load_state()
        state["queue"] = list(state.get("queue") or []) + [new_card]
        if not _save_state(state):
            logger.warning("[self_driven] 重试卡落盘失败 retry_of=%s",
                           new_card["retry_of"])
            return None
    logger.warning("[self_driven] 失败重试卡已生成 task_id=%s retry_of=%s "
                   "retry_count=%d goal=%s",
                   new_card["task_id"], new_card["retry_of"],
                   new_card["retry_count"], goal[:40])
    return new_card


# ---- 心智状态读取（memory_bank 只读） ----

def _fetch_mental_state():
    """读 memory_bank 最新一条 kind='self_narrative'，解析 content JSON 取
    mental_state（dict）。无记录/非 JSON/mental_state 空/读库失败一律返回
    None（fail-open，不抛）。只 SELECT，绝不写库。"""
    try:
        from . import memory_db  # 惰性 import（self_narrative 同款惯例）
    except Exception as e:  # noqa: BLE001 - fail-open：import 失败当没有
        logger.warning("[self_driven] import memory_db 异常：%s: %s",
                       type(e).__name__, str(e)[:120])
        return None
    try:
        conn = memory_db.connect()
        try:
            row = conn.execute(
                "SELECT content FROM memory_bank WHERE kind = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (_NARRATIVE_KIND,)).fetchone()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 - fail-open：读库失败当没有
        logger.warning("[self_driven] 读心智状态异常：%s: %s",
                       type(e).__name__, str(e)[:120])
        return None
    if row is None or not row[0]:
        return None
    try:
        doc = json.loads(row[0])
    except (ValueError, TypeError):
        return None  # stage1 旧纯文本行/坏 JSON：当没有心智状态
    ms = doc.get("mental_state") if isinstance(doc, dict) else None
    return ms if isinstance(ms, dict) and ms else None


# ---- LLM 目标生成（self_narrative._llm_narrate 同款 httpx 双协议） ----

def build_goal_messages(mental_state: dict, recent: list = None):
    """心智状态 dict → messages。纯函数零 IO（build_narrative_messages 同款
    可单测惯例）：mental_state 序列化紧凑 JSON 后替换进 system 模板，
    整体钳 _MENTAL_STATE_CHARS 防跑飞。

    [v4] recent = 近期待办卡（新→旧），拼成"禁止重复"清单注入模板。为什么必须
    喂：v3 实测两张自动卡都咬住"绝对路径 / stderr"同一主题——因为心智状态里
    只记着那一条历史失败，模型没有"我上次已经做过这个"的记忆，就会一直复读。"""
    ms_text = json.dumps(mental_state, ensure_ascii=False,
                         separators=(",", ":"))[:_MENTAL_STATE_CHARS]
    lines = []
    for i, card in enumerate((recent or [])[:_RECENT_GOALS_MAX], 1):
        if not isinstance(card, dict):
            continue
        g = " ".join(str(card.get("goal") or "").split())[:60]
        if g:
            lines.append("%d. %s" % (i, g))
    recent_text = "；".join(lines) if lines else "（无）"
    system = (_SYSTEM_PROMPT.replace("{mental_state}", ms_text)
              .replace("{recent_goals}", recent_text))
    return [{"role": "system", "content": system},
            {"role": "user", "content": _USER_PROMPT}]


def _clean_goal_text(raw: str) -> str:
    """模型输出 → 目标单行文本：剥代码围栏/成对引号、空白归一、钳长。
    纯函数零 IO。清理后为空 = 模型空转，调用方按"没生成"处理。"""
    t = (raw or "").strip()
    if t.startswith("```"):
        nl = t.find("\n")
        t = t[nl + 1:] if nl >= 0 else ""
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    t = t.strip()
    pairs = {'"': '"', "'": "'", "“": "”", "「": "」"}
    if len(t) >= 2 and t[0] in pairs and t[-1] == pairs[t[0]]:
        t = t[1:-1].strip()
    t = " ".join(t.split())  # 目标是单行：换行/多空白归一成空格
    return t[:_GOAL_TEXT_MAX]


def _sanitize_engine(value) -> str:
    """target_engine 归一：strip+lower 后必须落在 _ENGINE_WHITELIST，否则回落
    _DEFAULT_ENGINE（任务书：不在白名单内默认 zcode）。纯函数零 IO。"""
    v = str(value or "").strip().lower()
    return v if v in _ENGINE_WHITELIST else _DEFAULT_ENGINE


def _try_parse_json_object(text: str):
    """文本 → dict | None。剥代码围栏 + 截最外层花括号段 + json_repair 优先修复
    （self_narrative._parse_narrative_json 同款容错链），裸 json.loads 兜底。
    修不出 dict 一律 None（调用方按纯文本退化），不抛。纯函数零 IO。"""
    t = (text or "").strip()
    if t.startswith("```"):
        nl = t.find("\n")
        t = t[nl + 1:] if nl >= 0 else ""
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    t = t.strip()
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end <= start:
        return None
    cand = t[start:end + 1]
    if json_repair is not None:
        try:
            fixed = json_repair.repair_json(cand)
            data = json.loads(fixed) if isinstance(fixed, str) else fixed
            if isinstance(data, dict):
                return data
        except (ValueError, TypeError):
            pass  # 修不动再走裸 loads
    try:
        data = json.loads(cand)
        return data if isinstance(data, dict) else None
    except (ValueError, TypeError):
        return None


def _parse_goal_payload(raw: str):
    """模型输出 → (goal, target_engine)。纯函数零 IO。

    [v2] JSON 契约：{"goal": "...", "target_engine": "..."}。goal 经
    _clean_goal_text 清洗（空产出由调用方拒发卡）；target_engine 经 _sanitize_engine
    白名单校验（漂移/编造名回落 zcode）。模型没照 JSON 说话时退化整段文本当目标、
    引擎回落默认（self_narrative 解析失败降级纯叙事同款 fail-open）。"""
    text = (raw or "").strip()
    obj = _try_parse_json_object(text)
    if isinstance(obj, dict):
        return (_clean_goal_text(str(obj.get("goal") or "")),
                _sanitize_engine(obj.get("target_engine")))
    return _clean_goal_text(text), _DEFAULT_ENGINE


def _clean_field(raw, limit: int) -> str:
    """[v3] 单字段清洗：空白归一 + 剥成对引号 + 钳长。纯函数零 IO。"""
    t = " ".join(str(raw or "").split())
    pairs = {'"': '"', "'": "'", "“": "”", "「": "」"}
    if len(t) >= 2 and t[0] in pairs and t[-1] == pairs[t[0]]:
        t = t[1:-1].strip()
    return t[:limit]


def _extract_verify_paths(cmd: str) -> list:
    """从 verify_cmd 抽出候选路径 token（纯函数零 IO，绝不抛）。"""
    return _VERIFY_PATH_RE.findall(str(cmd or ""))


def _verify_python_syntax_problem(cmd: str) -> str:
    """`python -c` 代码体的转义体检（纯函数，绝不抛）。命中的是"命令根本编译不过"。

    动机是一条真实坏卡（task_id 4004b7bf，2026-10-31 18:22:26 生成）：
        python -c "exit(0 if open('D:\\MoRay\\utf_test.txt','r',...).read()==...)"
    Windows 路径塞进**非 raw** 字符串，`\\u` 被当 unicode 转义而 `tf_` 不是四位
    十六进制 → SyntaxError: unicodeescape。这条命令连编译都过不去，是**永远为假
    的预言机**：预测"exit code 0"永远拿不到，验证环节彻底空转。只查路径的 v4
    关卡放行了它（路径本身合法，问题在转义）。

    只对明确的转义错判死（unicodeescape / invalid \\x / truncated），其它
    SyntaxError 可能是我切引号造成的假阳性，宁可放行也不误杀。
    """
    m = _PY_C_RE.search(str(cmd or ""))
    if not m:
        return ""
    src = (m.group("src") or "").replace('\\"', '"')
    try:
        compile(src, "<verify_cmd>", "exec")
    except SyntaxError as e:
        msg = str(e).lower()
        if "unicodeescape" in msg or "invalid \\" in msg or "truncated" in msg:
            return "syntax"
        return ""
    except Exception:  # noqa: BLE001 - 体检自身出错当没问题，绝不阻断生成
        return ""
    return ""


def verify_cmd_problem(cmd: str) -> str:
    """[v4] verify_cmd 静态体检：合格返回 ""，不合格返回原因码。纯函数，零推理，
    只在 Windows 主机语义下判，绝不抛。

    检查顺序（先命中先返回）：
      empty       没填
      posix_path  引用了 /tmp 这类本机不存在的 Linux 路径
      posix_tool  [v5] 用了 find/grep/awk/sed 这类 POSIX 命令，或 sh 专有语法
      placeholder 用了 example.com / path/to 这类占位符
      syntax      `python -c` 代码体自身的转义错，命令编译不过（永远为假的预言机）
      no_path     命令里没有任何路径 token（无法定位验证对象）
      ghost_path  引用了不存在的盘符路径，且不在 _VERIFY_ROOTS 下
    已存在的路径一律合格（哪怕是 C:\\Windows\\System32\\cmd.exe）。"""
    text = str(cmd or "").strip()
    if not text:
        return "empty"
    low = text.lower()
    if any(m in low for m in _VERIFY_POSIX_MARKERS):
        return "posix_path"
    # [v5] POSIX 命令在 cmd 上根本不存在：exit≠0 是"命令找不到"，不是"预测被证伪"。
    # 不拦这一层，闭环会被自己的噪声污染（10-31 三张卡里两张栽在这里）。
    if _VERIFY_POSIX_CMD_RE.search(text) or any(s in text for s in _VERIFY_SH_SYNTAX):
        return "posix_tool"
    if any(p in low for p in _VERIFY_PLACEHOLDERS):
        return "placeholder"
    syntax_bad = _verify_python_syntax_problem(text)
    if syntax_bad:
        return syntax_bad
    paths = _extract_verify_paths(text)
    if not paths:
        return "no_path"
    ok = False
    for p in paths:
        if p.startswith("/"):
            return "posix_path"  # markers 没覆盖到的其它 Linux 路径
        low_p = p.lower()
        # 双向前缀：路径含空格时（E:\MoRay OS\...）抽取会被空格切断成
        # E:\MoRay，此时它只是允许根的前缀——按合格处理，不冤枉真路径。
        if os.path.exists(p) or any(low_p.startswith(r.lower()) or r.lower().startswith(low_p)
                                    for r in _VERIFY_ROOTS):
            ok = True
    return "" if ok else "ghost_path"


# ---- v5 验证执行器（2026-10-31 深海阶段二）：让"预测"真的被判真假 ----
#: 为什么必须有这一段：v4 之前 verify_cmd 全程只被当**文本**体检
#: （verify_cmd_problem），整个后端搜不到任何一处执行它。于是目标卡结构上没有
#: 判真伪的能力——result_note 里存的是引擎自己说的话（"ZCode：持续强化步骤
#: 验证"），不是 exit code。闭环的"观测/误差"两环因此是空的：预测写完即不朽，
#: 对错都不影响后面的任何判断。这一段把 verify_cmd 真的跑一次，把 exit code /
#: 输出摘要 / 判定写回卡（verified_at, verify_exit, verify_ok, verify_out,
#: verify_problem, verdict），第 1 天的"误差"从此有据可查。
_VERIFY_TIMEOUT_SEC = 20
_VERIFY_OUT_CHARS = 400
#: 派发后多久还没等到引擎终态事件 → 抢救性验证（事件总线丢事件/引擎崩溃时
#: 卡会永远停在 dispatched，闭环挂死）。
_VERIFY_STALE_DISPATCH_SEC = 600
#: 在飞闸 + 互斥（进程内存活）：同一张卡不并发/不重复跑验证命令
_VERIFY_INFLIGHT = set()
_VERIFY_LOCK = threading.Lock()
#: 队列探测节流：GET /queue 是高频轮询，补跑扫描 60s 一次就够
_VERIFY_SWEEP_SEC = 60.0
_last_verify_sweep_monotonic = 0.0
#: 命令静态体检里属于"命令本身坏"的原因码：这些情况下 exit≠0 不能记成"预测被
#: 证伪"（那会把生成器缺陷伪装成世界的否定），统一记 unverifiable。
#: placeholder / no_path 不算——它们不防碍命令执行，只说明定位不严谨。
_VERIFY_HARD_PROBLEMS = ("empty", "posix_path", "posix_tool", "syntax", "ghost_path")
#: 验证子进程的工作目录：固定工程根，相对路径有确定语义，不依赖服务启动目录。
_VERIFY_CWD = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_VERDICT_LABELS = {"confirmed": "预测成立", "falsified": "预测被证伪",
                   "unverifiable": "命令不可判"}


def _decode_output(raw) -> str:
    """子进程输出解码：先 utf-8，坏字节再按 gbk（中文 Windows 代码页）重试。
    纯工具函数，绝不抛。"""
    if not raw:
        return ""
    if isinstance(raw, str):
        return raw
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except Exception:  # noqa: BLE001 - 换编码重试
            continue
    return raw.decode("utf-8", errors="replace")


def _kill_process_tree(pid: int) -> None:
    """超时强杀整棵进程树（绝不抛）。

    为什么不能只 proc.kill()：Windows 上 shell=True 意味着真正跑命令的是 cmd.exe
    的**孙进程**，杀掉 cmd 之后 python 仍在跑、管道仍被攥着，随后的 communicate()
    会一直等到孙进程自然结束——实测 sleep(10)+timeout=1 会卡满 10 秒才返回，
    "超时"形同虚设。taskkill /T 才是把树砍干净。
    """
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True, timeout=5)
        else:
            os.killpg(os.getpgid(pid), 9)
    except Exception:  # noqa: BLE001 - 杀进程失败不影响判定（timed_out 已记录）
        pass


def run_verify_command(cmd: str, timeout: int = _VERIFY_TIMEOUT_SEC) -> dict:
    """**真**执行一次验证命令 → 观测三元组（exit_code + 输出摘要 + 是否超时）。

    这是整个自驱闭环里唯一让"预测"变成可判真伪的动作。诚实边界写在代码里：
    - shell=True 的理由：verify_cmd 的契约就是"一行能直接粘进 cmd 跑的命令"，
      带引号 / 重定向 / python -c，拆 argv 必拆错；命令来自本地 LLM、本机执行，
      与 _build_dispatch_prompt 把同一段文字交给引擎执行是同一信任级别。
    - 超时**整树强杀**（见 _kill_process_tree）：新进程组 + taskkill /T，否则
      "超时"只是不再等父进程，孙进程照样跑完。
    - **绝不抛**：任何异常都收敛成 {ok: False, error}，执行器自己不能变成新故障点。
    """
    text = str(cmd or "").strip()
    out = {"ok": False, "exit": None, "stdout": "", "stderr": "",
           "timed_out": False, "error": "", "problem": verify_cmd_problem(text)}
    if not text:
        out["error"] = "empty"
        return out
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    cwd = _VERIFY_CWD if os.path.isdir(_VERIFY_CWD) else None
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
    try:
        proc = subprocess.Popen(text, shell=True, cwd=cwd, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                creationflags=flags)
    except Exception as e:  # noqa: BLE001 - 起不来如实记，不上抛
        out["error"] = "%s: %s" % (type(e).__name__, str(e)[:160])
        return out
    try:
        stdout_b, stderr_b = proc.communicate(timeout=max(1, int(timeout)))
        out["exit"] = int(proc.returncode)
        out["ok"] = proc.returncode == 0
    except subprocess.TimeoutExpired:
        out["timed_out"] = True
        out["error"] = "timeout"
        _kill_process_tree(proc.pid)
        try:  # 树被砍后管道应立刻关；万一还挂着，最多再等 3 秒
            stdout_b, stderr_b = proc.communicate(timeout=3)
        except Exception:  # noqa: BLE001
            stdout_b, stderr_b = b"", b""
    except Exception as e:  # noqa: BLE001 - 执行器 fail-open，异常转字段
        out["error"] = "%s: %s" % (type(e).__name__, str(e)[:160])
        stdout_b, stderr_b = b"", b""
    out["stdout"] = _decode_output(stdout_b)[:_VERIFY_OUT_CHARS]
    out["stderr"] = _decode_output(stderr_b)[:_VERIFY_OUT_CHARS]
    return out


# ---- v5 判据台账（"连续 7 天活着"的度量衡）----
#: 为什么要台账：判定写完就散落在 7 张卡上，无法回答"最近精度在收敛吗、连着几天
#: 有真判据"。台账是 append-only 的一维流水（ts/task_id/verdict），stats 在读时
#: 现算——不做任何预聚合，避免"统计口径"与"原始判据"分叉。
_VERDICT_LOG_MAX = 200
_VERDICT_KINDS = ("confirmed", "falsified", "unverifiable")


def _verdict_log_append(state: dict, task_id: str, verdict: str, now: str) -> None:
    """往台账追加一条（在写回卡的同一把锁内调用，避免两次落盘中间被打断）。"""
    log = state.get("verdict_log")
    if not isinstance(log, list):
        log = []
    log.append({"ts": now, "task_id": str(task_id), "verdict": str(verdict)})
    state["verdict_log"] = log[-_VERDICT_LOG_MAX:]


def verdict_stats(days: int = 7) -> dict:
    """近 days 天台账 → 命中率 / 有判据天数 / 连续天数（纯读，绝不抛）。

    口径（写死，防以后自己糊弄自己）：
      - judged = confirmed + falsified，**unverifiable 不计入分母**（命令坏不是
        预测错的证据）；
      - active_day = 当天至少有一条 judged（只有 unverifiable 的卡不算"活过一天"）；
      - streak = 从今天往回数的连续 active_day 数；今天还没判据时给一天宽限
        （streak_end_today=False 如实标出，不假装连续）。
    """
    try:
        log = _load_state().get("verdict_log") or []
    except Exception:  # noqa: BLE001 - 台账坏了就是空台账
        log = []
    today = datetime.date.today()
    span = max(1, int(days or 7))
    horizon = today - datetime.timedelta(days=span - 1)
    per_day = {}
    for e in log:
        if not isinstance(e, dict):
            continue
        v = str(e.get("verdict") or "")
        if v not in _VERDICT_KINDS:
            continue
        try:
            d = datetime.date.fromisoformat(str(e.get("ts") or "")[:10])
        except Exception:  # noqa: BLE001 - 时间戳坏的行跳过，不猜日期
            continue
        if d < horizon or d > today:
            continue
        b = per_day.setdefault(d, {"confirmed": 0, "falsified": 0, "unverifiable": 0})
        b[v] += 1
    confirmed = sum(b["confirmed"] for b in per_day.values())
    falsified = sum(b["falsified"] for b in per_day.values())
    unverifiable = sum(b["unverifiable"] for b in per_day.values())
    judged = confirmed + falsified
    active = [d for d, b in per_day.items() if (b["confirmed"] + b["falsified"]) > 0]
    streak = 0
    cursor = today
    if cursor not in per_day or (per_day[cursor]["confirmed"] + per_day[cursor]["falsified"]) == 0:
        cursor = today - datetime.timedelta(days=1)  # 今天还没判据：宽限一天
    while (cursor in per_day
           and (per_day[cursor]["confirmed"] + per_day[cursor]["falsified"]) > 0):
        streak += 1
        cursor -= datetime.timedelta(days=1)
    day_list = []
    for d in sorted(per_day, reverse=True):
        b = per_day[d]
        day_list.append({"date": d.isoformat(), "confirmed": b["confirmed"],
                         "falsified": b["falsified"],
                         "unverifiable": b["unverifiable"],
                         "judged": b["confirmed"] + b["falsified"]})
    return {"days": span, "judged": judged, "confirmed": confirmed,
            "falsified": falsified, "unverifiable": unverifiable,
            "accuracy": round(confirmed / judged, 3) if judged else None,
            "active_days": len(active), "streak": streak,
            "streak_end_today": today in active,
            "goal_days": 7, "day_list": day_list}


def _verdict_of(obs: dict) -> str:
    """观测 → 判定（纯函数）。命令静态不合格时一律 unverifiable：那种失败是世界
    对**命令**的否定，不是对预测的否定，记成 falsified 等于骗自己。"""
    if obs.get("timed_out"):
        return "unverifiable"
    if str(obs.get("problem") or "") in _VERIFY_HARD_PROBLEMS:
        return "unverifiable"
    if obs.get("error") and obs.get("exit") is None:
        return "unverifiable"
    return "confirmed" if obs.get("ok") else "falsified"


def _summarize_obs(obs: dict) -> str:
    """观测 → 一行摘要（stdout 优先，stderr 追加，截断）。纯函数。"""
    parts = []
    so = str(obs.get("stdout") or "").strip()
    se = str(obs.get("stderr") or "").strip()
    if so:
        parts.append(so)
    if se:
        parts.append("ERR: " + se)
    if not parts and obs.get("error"):
        parts.append(str(obs["error"]))
    return " | ".join(parts)[:_VERIFY_OUT_CHARS]


def verify_goal_card(task_id: str, force: bool = False,
                     timeout: int = _VERIFY_TIMEOUT_SEC) -> dict:
    """对一张目标卡跑验证并写回判据（闭环的"观测 → 误差"两环）。

    约束（都是踩过的坑）：
      - pending_approval 不验（还没执行，验了也没意义）；
      - 已验证过且 force=False 直接跳过（幂等，轮询不会重复烧命令）；
      - **子进程在 _STATE_LOCK 之外跑**（20s 的子进程绝不能持状态锁，否则整个
        状态文件读改写被冻住）；
      - 写回是"读-改-写"一个原子段落盘；落盘失败如实返回 reason，不假装成功。
    返回契约见各 return；永不抛。
    """
    tid = str(task_id or "").strip()
    if not tid:
        return {"ok": False, "reason": "task_id_required"}
    with _VERIFY_LOCK:
        if tid in _VERIFY_INFLIGHT:
            return {"ok": False, "reason": "inflight", "task_id": tid}
        _VERIFY_INFLIGHT.add(tid)
    try:
        card = _card_by_id(tid)
        if card is None:
            return {"ok": False, "reason": "not_found", "task_id": tid}
        status = str(card.get("status") or "")
        if status == "pending_approval":
            return {"ok": False, "reason": "not_dispatched", "status": status,
                    "task_id": tid}
        if card.get("verified_at") and not force:
            return {"ok": True, "skipped": "already_verified", "task_id": tid,
                    "verdict": card.get("verdict"), "verified_at": card.get("verified_at")}
        cmd = str(card.get("verify_cmd") or "").strip()
        if not cmd:
            return {"ok": False, "reason": "no_verify_cmd", "task_id": tid}
        obs = run_verify_command(cmd, timeout=timeout)
        verdict = _verdict_of(obs)
        summary = _summarize_obs(obs)
        now = datetime.datetime.now().isoformat(timespec="seconds")
        note = "验证 exit=%s %s" % (obs.get("exit"), _VERDICT_LABELS.get(verdict, verdict))
        with _STATE_LOCK:
            state = _load_state()
            target = None
            for g in state.get("queue") or []:
                if isinstance(g, dict) and str(g.get("task_id")) == tid:
                    target = g
                    break
            if target is None:
                return {"ok": False, "reason": "vanished", "task_id": tid}
            target["verified_at"] = now
            target["verify_exit"] = obs.get("exit")
            target["verify_ok"] = bool(obs.get("ok"))
            target["verify_out"] = summary
            target["verify_problem"] = obs.get("problem") or ""
            target["verdict"] = verdict
            if obs.get("timed_out"):
                target["verify_timeout"] = True
            # result_note 是面板上唯一能立刻看见的字段：把判据放前面，引擎自述
            # 压后面（不再让"引擎说自己做得不错"冒充验证结论）。
            engine_note = str(target.get("result_note") or "").strip()
            target["result_note"] = (note + (" · " + engine_note[:80]
                                             if engine_note else ""))[:200]
            _verdict_log_append(state, tid, verdict, now)
            if not _save_state(state):
                logger.warning("[self_driven] 验证判据落盘失败 task_id=%s", tid)
                return {"ok": False, "reason": "state_save_failed", "task_id": tid}
        logger.warning("[self_driven] 验证完成 task_id=%s exit=%s verdict=%s "
                       "problem=%s cmd=%s", tid, obs.get("exit"), verdict,
                       obs.get("problem") or "-", cmd[:120])
        return {"ok": True, "task_id": tid, "verdict": verdict,
                "exit": obs.get("exit"), "stdout": summary[:200],
                "problem": obs.get("problem") or "",
                "timed_out": bool(obs.get("timed_out")), "verified_at": now}
    except Exception as e:  # noqa: BLE001 - 验证链路 fail-open，绝不拖垮调用方
        logger.warning("[self_driven] 验证异常 task_id=%s：%s: %s",
                       tid, type(e).__name__, str(e)[:200])
        return {"ok": False, "reason": "exception",
                "detail": "%s: %s" % (type(e).__name__, str(e)[:160])}
    finally:
        with _VERIFY_LOCK:
            _VERIFY_INFLIGHT.discard(tid)


async def verify_goal_async(card_or_tid, force: bool = False) -> dict:
    """asyncio 侧入口：把 20s 的子进程丢进线程池，绝不阻塞事件循环
    （synapse handler 在事件循环里调——直接跑会把整条事件总线冻住 20s）。"""
    tid = card_or_tid.get("task_id") if isinstance(card_or_tid, dict) else card_or_tid
    tid = str(tid or "").strip()
    if not tid:
        return {"ok": False, "reason": "task_id_required"}
    try:
        return await asyncio.to_thread(verify_goal_card, tid, force)
    except Exception as e:  # noqa: BLE001
        logger.warning("[self_driven] 异步验证异常 task_id=%s：%s: %s",
                       tid, type(e).__name__, str(e)[:200])
        return {"ok": False, "reason": "exception"}


def _verify_candidates() -> list:
    """挑出"该被验证但还没验"的卡（纯读状态文件，供后台补跑）：
    - done/failed 且没有 verified_at → 引擎终态事件来过但执行器当时不在/没跑；
    - dispatched 超过 _VERIFY_STALE_DISPATCH_SEC 还没 verified_at → 抢救（事件丢了）。
    返回 task_id 列表（新→旧顺序）。绝不抛。"""
    try:
        cards = [g for g in (_load_state().get("queue") or []) if isinstance(g, dict)]
    except Exception:  # noqa: BLE001
        return []
    now = time.time()
    out = []
    for g in cards:
        if g.get("verified_at"):
            continue
        status = str(g.get("status") or "")
        tid = str(g.get("task_id") or "").strip()
        if not tid or not str(g.get("verify_cmd") or "").strip():
            continue
        if status in ("done", "failed"):
            out.append(tid)
            continue
        if status == "dispatched":
            created = str(g.get("created_at") or "")
            try:
                ts = datetime.datetime.fromisoformat(created).timestamp()
            except Exception:  # noqa: BLE001 - 时间戳坏就不抢救，不猜
                continue
            if now - ts >= _VERIFY_STALE_DISPATCH_SEC:
                out.append(tid)
    return out


def _spawn_verify_sweep() -> int:
    """后台补跑（由 GET /queue 触发，60s 节流）：每张卡一个 daemon 线程，
    _VERIFY_INFLIGHT 保证同一张卡不并发。返回本次起的线程数。绝不抛。"""
    global _last_verify_sweep_monotonic
    try:
        now = time.monotonic()
        if now - _last_verify_sweep_monotonic < _VERIFY_SWEEP_SEC:
            return 0
        _last_verify_sweep_monotonic = now
        cands = _verify_candidates()
        for tid in cands:
            threading.Thread(target=verify_goal_card, args=(tid,), daemon=True,
                             name="verify-%s" % tid[:8]).start()
        return len(cands)
    except Exception as e:  # noqa: BLE001 - 补跑失败不影响队列读取
        logger.warning("[self_driven] 验证补跑调度失败：%s: %s",
                       type(e).__name__, str(e)[:160])
        return 0


def _goal_grams(text: str) -> set:
    """目标文本 → 字符二元组集合（去空白）。纯函数零 IO。"""
    t = "".join(str(text or "").split())
    if len(t) <= 1:
        return {t} if t else set()
    return {t[i:i + 2] for i in range(len(t) - 1)}


def goal_similarity(a: str, b: str) -> float:
    """两条目标的字符二元组 Jaccard 重合度（0.0~1.0）。纯函数零 IO。"""
    ga, gb = _goal_grams(a), _goal_grams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def _find_repeat(goal: str, recent: list):
    """近期目标里与 goal 同主题（重合度 ≥ _REPEAT_SIMILARITY）的一条 → 返回该
    dict；没有 → None。纯函数，只读入参。"""
    for item in recent or []:
        if not isinstance(item, dict):
            continue
        if goal_similarity(goal, str(item.get("goal") or "")) >= _REPEAT_SIMILARITY:
            return item
    return None


def _fetch_recent_goals(limit: int = _RECENT_GOALS_MAX) -> list:
    """取队列里最近的 N 张卡（新→旧），用于 prompt 去重清单与重复检测。
    只读状态文件，失败当空（fail-open，不抛）。"""
    try:
        queue = _load_state().get("queue") or []
    except Exception as e:  # noqa: BLE001 - fail-open
        logger.warning("[self_driven] 读近期目标异常：%s: %s",
                       type(e).__name__, str(e)[:120])
        return []
    out = []
    for card in reversed(queue):
        if isinstance(card, dict) and str(card.get("goal") or "").strip():
            out.append(card)
        if len(out) >= limit:
            break
    return out


def _build_fix_message(parsed: dict, problem: str, repeat) -> dict:
    """[v4] 重生成反馈消息：把体检不合格的原因原样写回给模型。纯函数零 IO。"""
    reasons = []
    if problem == "empty":
        reasons.append("verify_cmd 是空的")
    elif problem == "posix_path":
        reasons.append("verify_cmd 引用了 /tmp 这类 Linux 路径，本机是 Windows，"
                       "这些目录不存在，命令必定失败")
    elif problem == "placeholder":
        reasons.append("verify_cmd 用了 example.com / path/to 这类占位符，不是真路径")
    elif problem == "no_path":
        reasons.append("verify_cmd 里没有任何文件路径，无法定位验证对象")
    elif problem == "ghost_path":
        reasons.append("verify_cmd 引用的路径既不存在，也不在 D:\\MoRay 或 "
                       "E:\\MoRay OS 下")
    if isinstance(repeat, dict):
        reasons.append("目标和近期已生成的「%s」主题重复，必须换一个新方向"
                       % str(repeat.get("goal") or "")[:40])
    if not reasons:
        reasons.append("上一版不合格")
    return {"role": "user",
            "content": "上一版为什么不合格：" + "；".join(reasons)
                       + "。请修正后重新输出同一个 JSON 对象。"}


def _parse_goal_payload_v3(raw: str) -> dict:
    """[v3 2026-10-31] 模型输出 → 目标卡字段 dict（goal + 可证伪三件套 + 引擎）。

    为什么要三件套：v1/v2 的 prompt 只产出"持续强化步骤验证"这类不可证伪口号，
    8 天的历史 4 张卡里没有一条能被现实判真假 —— 目标生成等于没生成。v3 强制
    claim（断言）/ prediction（预期观测）/ verify_cmd（验证手段）三字段，缺任
    一项就退化并在卡上留空串，由 UI 显示"未填"，绝不替模型编造。

    容错口径与 v2 一致（纯函数零 IO，绝不抛）：
    - 模型没照 JSON 说话 → 整段文本当 goal，三件套留空，引擎回落默认；
    - goal 空但 claim 有 → 用 claim 当 goal（可读性优先，前端只认 goal）；
    - claim 空但 goal 有 → claim 退化成 goal（仍是断言，不改写语义）；
    - target_engine 经 _sanitize_engine 白名单校验，漂移名回落 zcode。"""
    text = (raw or "").strip()
    obj = _try_parse_json_object(text)
    if not isinstance(obj, dict):
        plain = _clean_goal_text(text)
        return {"goal": plain, "claim": plain, "prediction": "",
                "verify_cmd": "", "target_engine": _DEFAULT_ENGINE}
    goal = _clean_goal_text(str(obj.get("goal") or ""))
    claim = _clean_field(obj.get("claim"), _CLAIM_TEXT_MAX)
    if not claim:
        claim = goal
    return {"goal": goal or claim,
            "claim": claim,
            "prediction": _clean_field(obj.get("prediction"), _PRED_TEXT_MAX),
            "verify_cmd": _clean_field(obj.get("verify_cmd"), _VERIFY_TEXT_MAX),
            "target_engine": _sanitize_engine(obj.get("target_engine"))}


async def _llm_goal(messages: list):
    """喂 messages 要目标 JSON 原文（解析归 _parse_goal_payload）；任何异常/非 200/
    空应答返回 (None, 原因)。与 self_narrative._llm_narrate 同一套 httpx 配置
    （REFLECTION_API/BASE_URL/MODEL/TIMEOUT/NUM_CTX）与双协议口径。"""
    if config.REFLECTION_API == "openai":
        url = config.REFLECTION_BASE_URL.rstrip("/") + "/v1/chat/completions"
        payload = {"model": config.REFLECTION_MODEL, "stream": False,
                   "temperature": 0.0, "messages": messages}
    else:
        url = config.REFLECTION_BASE_URL.rstrip("/") + "/api/chat"
        payload = {"model": config.REFLECTION_MODEL, "stream": False, "temperature": 0.0,
                   "options": {"num_ctx": config.REFLECTION_NUM_CTX},
                   "messages": messages}
    try:
        async with httpx.AsyncClient(timeout=config.REFLECTION_TIMEOUT) as c:
            r = await c.post(url, json=payload)
        if r.status_code != 200:
            return None, "%s HTTP %s（model=%s url=%s）" % (
                config.REFLECTION_API, r.status_code, config.REFLECTION_MODEL, url)
        body = r.json()
        if config.REFLECTION_API == "openai":
            raw = ((body.get("choices") or [{}])[0].get("message") or {}).get("content", "")
        else:
            raw = (body.get("message") or {}).get("content", "")
        if not (raw or "").strip():
            return None, "推理服务应答为空"
        return raw, ""
    except Exception as e:  # noqa: BLE001 - fail-open：异常转错误原因返回，不上抛
        logger.warning("[self_driven] 推理调用异常：%s: %s",
                       type(e).__name__, str(e)[:200])
        return None, "%s: %s" % (type(e).__name__, str(e)[:200])


# ---- 目标生成主流程 ----

async def generate_goal():
    """生成一个待办目标卡 → {"task_id", "goal", "status", "created_at"}。
    无心智状态/今日配额耗尽/LLM 失败/产出为空/落盘失败一律返回 None
    （fail-close 到"没生成"，原因进日志），绝不抛、绝不自动执行目标。

    防跑飞闸（LLM 调用前全判完，被拒调用零推理开销）：
      ① 在飞闸 _goal_inflight：生成中不重复触发；
      ② 心智状态非空（_fetch_mental_state）；
      ③ 每日配额 < _DAILY_GOAL_LIMIT（安全熔断）。
    产出侧：JSON 解析（_parse_goal_payload：goal 清洗、target_engine 白名单，
    空产出拒发卡）→ 发卡前 _record_goal 再守一次限额并原子落盘，落盘失败
    不发卡（熔断 fail-close）。"""
    global _goal_inflight
    if _goal_inflight:
        logger.warning("[self_driven] 拒绝：上一次生成还在飞")
        return None
    mental_state = _fetch_mental_state()
    if not mental_state:
        logger.warning("[self_driven] 拒绝：无可用心智状态（memory_bank 无 "
                       "kind='%s' 或 mental_state 为空）", _NARRATIVE_KIND)
        return None
    quota = _quota_info(_load_state())
    if quota["generated_today"] >= quota["limit"]:
        logger.warning("[self_driven] 拒绝：今日配额已满 %d/%d（安全熔断）",
                       quota["generated_today"], quota["limit"])
        return None
    recent = _fetch_recent_goals()
    messages = build_goal_messages(mental_state, recent)
    _goal_inflight = True
    try:
        raw, err = await _llm_goal(messages)
        if raw is None:
            logger.warning("[self_driven] LLM 阶段失败：%s", err)
            return None
        parsed = _parse_goal_payload_v3(raw)
        goal = parsed["goal"]
        if not goal:
            logger.warning("[self_driven] 拒发卡（产出为空）：%r", str(raw)[:60])
            return None
        # [v4] 体检 + 一次带反馈重生成。只做一次：多轮会拖垮后台循环节奏，
        # 且模型在明确反馈下基本一轮就改对；改不对就照发卡但如实打标。
        problem = verify_cmd_problem(parsed["verify_cmd"])
        repeat = _find_repeat(goal, recent)
        if problem or isinstance(repeat, dict):
            logger.warning("[self_driven] v4 体检不合格（verify=%s repeat=%s）"
                           "→ 带反馈重生成一次", problem or "ok",
                           str(repeat.get("task_id") or "")[:8]
                           if isinstance(repeat, dict) else "-")
            fix_msgs = list(messages) + [
                {"role": "assistant", "content": str(raw)[:800]},
                _build_fix_message(parsed, problem, repeat),
            ]
            raw2, err2 = await _llm_goal(fix_msgs)
            if raw2:
                parsed2 = _parse_goal_payload_v3(raw2)
                if parsed2["goal"]:
                    parsed, goal = parsed2, parsed2["goal"]
            else:
                logger.warning("[self_driven] 重生成失败，沿用初版：%s", err2)
            problem = verify_cmd_problem(parsed["verify_cmd"])
            repeat = _find_repeat(goal, recent)
            if problem or isinstance(repeat, dict):
                logger.warning("[self_driven] 重生成后仍不合格（verify=%s "
                               "repeat=%s）",
                               problem or "ok",
                               str(repeat.get("task_id") or "")[:8]
                               if isinstance(repeat, dict) else "-")
        # [v5] 最后一道闸：硬缺陷（空 / POSIX 路径 / POSIX 工具 / 语法错 / 幽灵路径）
        # 的命令在 Windows 上**跑不起来**，发出去只能换来 verdict=unverifiable——
        # 那既拿不到真实误差，又白烧一份日配额。宁可不发卡，也不发一张注定
        # 判不动的卡：这是"活"与"看起来活"的分界线。
        # 软缺陷（placeholder / no_path）照发：`python -c "print(1)"` 确实没有
        # 文件路径，但它能跑，exit code 依然是真判据。
        if problem in _VERIFY_HARD_PROBLEMS:
            logger.warning("[self_driven] 拒发卡：verify_cmd 硬缺陷不可执行 %s"
                           "（%s）→ 本轮不产出，不烧配额",
                           problem, parsed["verify_cmd"][:80])
            return None
        card = {"task_id": str(uuid.uuid4()),
                "goal": goal,
                "claim": parsed["claim"],
                "prediction": parsed["prediction"],
                "verify_cmd": parsed["verify_cmd"],
                "target_engine": parsed["target_engine"],
                "verify_problem": problem,
                "repeat_of": str(repeat.get("task_id") or "")
                             if isinstance(repeat, dict) else "",
                "engine_task_id": "",
                "status": "pending_approval",
                "created_at": datetime.datetime.now().isoformat(timespec="seconds")}
        if not _record_goal(card):  # 超限/落盘失败：不发卡（见模块 docstring）
            return None
        logger.warning("[self_driven] 目标已进待审批队列 task_id=%s goal=%s",
                       card["task_id"], goal[:40])
        return card
    finally:
        _goal_inflight = False


async def maybe_generate_goal() -> dict:
    """[v3 2026-10-31] 循环挂点：self_check_loop 每轮尾调用（吞异常，绝不中断循环）。

    与手动入口 generate_goal 的分工：本函数只多一道**循环冷却**
    （_LOOP_COOLDOWN_SEC=3h），其余闸门全部复用 generate_goal
    （在飞闸 / 心智状态 / 日配额 / 落盘 fail-close）。三道闸都在 LLM 调用之前，
    被拒时零推理开销。

    返回 {"ran": bool, "reason": str, "card": dict|None}，绝不抛
    （CancelledError 继承 BaseException，不被 except Exception 捕获，照常上抛）。
    注意：本函数只**生成**目标卡到 pending_approval，派发仍要人在 approve
    路由点头 —— "零自动执行"硬约束不变。"""
    global _last_generate_monotonic
    now = time.monotonic()
    if _last_generate_monotonic and now - _last_generate_monotonic < _LOOP_COOLDOWN_SEC:
        return {"ran": False, "reason": "cooldown", "card": None}
    try:
        card = await generate_goal()
    except Exception as e:  # noqa: BLE001 - 挂进循环绝不中断
        logger.warning("[self_driven] maybe_generate_goal 未捕获异常：%s: %s",
                       type(e).__name__, str(e)[:120])
        return {"ran": False,
                "reason": "unhandled: %s: %s" % (type(e).__name__, str(e)[:120]),
                "card": None}
    if card is None:
        return {"ran": False, "reason": "not_generated", "card": None}
    _last_generate_monotonic = time.monotonic()
    logger.warning("[self_driven] 循环挂点已生成目标 task_id=%s", card["task_id"])
    return {"ran": True, "reason": "generated", "card": card}


# ---- API（generate/approve/reject 三 POST + GET queue） ----

router = APIRouter()


@router.post("/self_driven/generate")
async def self_driven_generate() -> dict:
    """[自驱引擎 v1] 手动触发一次目标生成（测试入口）：POST /api/self_driven/generate。
    前置廉价检查（心智状态/今日配额）给可诊断 reason、零推理开销；generate_goal
    内部同款强制再查（熔断在生成点执行，不信调用方）。
    成功 {"ok": True, **目标卡}；被拦/失败 {"ok": False, "reason": ...}。"""
    if _fetch_mental_state() is None:
        return {"ok": False, "reason": "no_mental_state",
                "detail": "memory_bank 无可用心智状态，先生成自我叙事（"
                          "POST /api/self_narrative/generate）再来"}
    quota = _quota_info(_load_state())
    if quota["generated_today"] >= quota["limit"]:
        return {"ok": False, "reason": "daily_limit_reached", **quota}
    if _goal_inflight:
        # 2026-10-31 踩过的坑：后台桥正在生成时手点 generate，会一路走到
        # generation_failed 并显示"LLM 失败"——把"排队中"谎报成"失败"。
        return {"ok": False, "reason": "inflight",
                "detail": "已有一次目标生成在跑（模型可能正在推理），稍后再试"}
    card = await generate_goal()
    if card is None:
        return {"ok": False, "reason": "generation_failed",
                "detail": "LLM 失败/产出为空/配额落盘失败，详见服务端日志"}
    return {"ok": True, **card}


async def _remove_goal_route(request: Request, action: str) -> dict:
    """reject 专用出队体：body {"task_id": "..."} → 删卡。
    契约：成功 {"ok": true, "removed": n}；task_id 缺失/坏 JSON/落盘失败
    {"ok": false, "reason": ...}（fail-open 不抛 500）。"""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - 坏 JSON 转契约错误，不上抛
        return {"ok": False, "reason": "bad_json"}
    task_id = str((body or {}).get("task_id") or "").strip()
    if not task_id:
        return {"ok": False, "reason": "task_id_required"}
    removed = _remove_goal(task_id)
    if removed < 0:
        return {"ok": False, "reason": "state_save_failed"}
    logger.warning("[self_driven] %s 出队 task_id=%s removed=%d",
                   action, task_id, removed)
    return {"ok": True, "removed": removed}


#: [v4] 派发在飞闸：同一张卡并发点两次批准，不许双 spawn（进程内存，重启清零）
_DISPATCH_INFLIGHT = set()


def _card_by_id(task_id: str):
    """从队列取指定 task_id 的卡（只读状态文件，失败当没有，不抛）。"""
    for card in _load_state().get("queue") or []:
        if isinstance(card, dict) and str(card.get("task_id")) == task_id:
            return card
    return None


def _build_dispatch_prompt(card: dict) -> str:
    """目标卡 → 派发指令（纯函数零 IO）。三件套一起交给引擎，否则引擎只知道
    "做什么"，不知道"怎么算做成"。"""
    parts = [str(card.get("goal") or "").strip()]
    for label, key in (("断言（必须为真）", "claim"),
                       ("预期观测", "prediction"),
                       ("验证命令", "verify_cmd")):
        v = str(card.get(key) or "").strip()
        if v:
            parts.append("%s：%s" % (label, v))
    return "\n".join(p for p in parts if p)


@router.post("/self_driven/dispatch")
async def self_driven_dispatch(request: Request) -> dict:
    """[v4 2026-10-31] 人工批准 → 派发（后端权威版，一跳原子）：
    POST /api/self_driven/dispatch  body {"task_id": "..."}

    为什么新增：v2 的 approve 只是"登记"，真正的派发是前端直调
    /api/engines/{name}/tasks —— 闸门挂在前端，后端任何调用方都能绕；且前端两跳
    若第一跳成功、第二跳失败，会留下"引擎已经在跑、卡却还是待审批"的脏状态，
    反复点批准就是重复 spawn。本端点把「校验 pending_approval → 派发 → 登记」
    收进同一个函数，并用 _DISPATCH_INFLIGHT 挡住并发双击。

    诚实边界（写在代码里，免得日后被误当成安全边界）：本机单用户环境下，任何
    进程都能直接 POST /api/engines/*/tasks，所以本端点挡住的是**误调、重放、
    并发双击**，挡不住有意绕过。真正的门只能是"不给 AI 派发能力"。

    契约：成功 {"ok": true, "task_id", "engine_task_id", "engine", "status"}；
    失败 {"ok": false, "reason": ...}，reason ∈ bad_json / task_id_required /
    not_found / not_pending / engine_not_found / empty_prompt / inflight /
    dispatch_failed / state_save_failed。"""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - 坏 JSON 转契约错误，不上抛
        return {"ok": False, "reason": "bad_json"}
    task_id = str((body or {}).get("task_id") or "").strip()
    if not task_id:
        return {"ok": False, "reason": "task_id_required"}
    card = _card_by_id(task_id)
    if card is None:
        return {"ok": False, "reason": "not_found", "task_id": task_id}
    status = str(card.get("status") or "")
    if status != "pending_approval":
        logger.warning("[self_driven] 拒绝派发：卡状态是 %s 不是 pending_approval "
                       "task_id=%s", status, task_id)
        return {"ok": False, "reason": "not_pending", "status": status}
    if task_id in _DISPATCH_INFLIGHT:
        return {"ok": False, "reason": "inflight"}
    engine_name = _sanitize_engine(card.get("target_engine"))
    _DISPATCH_INFLIGHT.add(task_id)
    try:
        try:
            from .engines.local_adapter import get_engine_by_name
        except Exception as e:  # noqa: BLE001 - 引擎注册表导入失败当没有引擎
            logger.warning("[self_driven] import 引擎注册表异常：%s: %s",
                           type(e).__name__, str(e)[:160])
            return {"ok": False, "reason": "engine_not_found", "engine": engine_name}
        engine = get_engine_by_name(engine_name)
        if engine is None or not callable(getattr(engine, "start_stream", None)):
            return {"ok": False, "reason": "engine_not_found", "engine": engine_name}
        prompt = _build_dispatch_prompt(card)
        if not prompt:
            return {"ok": False, "reason": "empty_prompt"}
        try:
            res = engine.start_stream({"prompt": prompt, "caller": "moray-self"})
        except Exception as e:  # noqa: BLE001 - 派发失败转契约错误，不上抛
            logger.warning("[self_driven] 派发异常 task_id=%s：%s: %s",
                           task_id, type(e).__name__, str(e)[:200])
            return {"ok": False, "reason": "dispatch_failed",
                    "detail": "%s: %s" % (type(e).__name__, str(e)[:200])}
        if not isinstance(res, dict) or not res.get("ok"):
            return {"ok": False, "reason": "dispatch_failed",
                    "detail": str(res)[:200]}
        engine_task_id = str(res.get("task_id") or res.get("id") or "").strip()
        if not engine_task_id:
            return {"ok": False, "reason": "dispatch_failed",
                    "detail": "引擎未返回 task_id：%s" % str(res)[:160]}
        updated = mark_dispatched(task_id, engine_task_id)
        if updated is None:
            # 引擎已经在跑、登记却失败：如实报，绝不假装成功（这正是 v2 的脏状态）
            logger.warning("[self_driven] 派发成功但登记失败 task_id=%s "
                           "engine_task_id=%s", task_id, engine_task_id)
            return {"ok": False, "reason": "state_save_failed",
                    "engine_task_id": engine_task_id,
                    "detail": "引擎已启动但卡状态未落盘，请核对队列"}
        logger.warning("[self_driven] 人工批准已派发 task_id=%s → %s:%s",
                       task_id, engine_name, engine_task_id)
        return {"ok": True, "task_id": task_id, "engine_task_id": engine_task_id,
                "engine": engine_name, "status": updated.get("status")}
    finally:
        _DISPATCH_INFLIGHT.discard(task_id)


@router.post("/self_driven/approve")
async def self_driven_approve(request: Request) -> dict:
    """[自驱引擎 v2] 派发登记（状态流转，不删卡）：POST /api/self_driven/approve。
    前端把目标派发给引擎、拿到引擎 task_id 后调用（body {"task_id": 目标卡
    task_id, "engine_task_id": 引擎任务 id}）：卡不删，状态流转
    pending_approval→dispatched 并登记 engine_task_id；执行结果由引擎收尾事件
    经 synapse handler 回写 done/failed。纯登记动作，不触发任何执行。
    契约：成功 {"ok": true, "updated": 1, "status": ...}；task_id/engine_task_id
    缺失/坏 JSON {"ok": false, "reason": ...}；卡不存在 {"reason": "not_found"}。"""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - 坏 JSON 转契约错误，不上抛
        return {"ok": False, "reason": "bad_json"}
    task_id = str((body or {}).get("task_id") or "").strip()
    engine_task_id = str((body or {}).get("engine_task_id") or "").strip()
    if not task_id:
        return {"ok": False, "reason": "task_id_required"}
    if not engine_task_id:
        return {"ok": False, "reason": "engine_task_id_required"}
    card = mark_dispatched(task_id, engine_task_id)
    if card is None:
        return {"ok": False, "reason": "not_found",
                "detail": "目标不存在或落盘失败，详见服务端日志"}
    logger.warning("[self_driven] approve 派发登记 task_id=%s engine_task_id=%s",
                   task_id, engine_task_id)
    return {"ok": True, "updated": 1, "status": card.get("status")}


@router.post("/self_driven/reject")
async def self_driven_reject(request: Request) -> dict:
    """[自驱引擎 v1] 忽略出队：POST /api/self_driven/reject。
    前端点「忽略（X）」调用（body 同 approve），同样移除该项。"""
    return await _remove_goal_route(request, "reject")


@router.post("/self_driven/verify")
async def self_driven_verify(request: Request) -> dict:
    """[v5 2026-10-31] 补跑验证：POST /api/self_driven/verify
    body {"task_id"?: "...", "force"?: false}

    省略 task_id → 对队列里所有"该验未验"的卡逐个补跑（done/failed 无判据、
    或 dispatched 超过 600s 卡死）。带 task_id → 只验这一张；force=true 覆盖
    旧判据重跑。这个端点也是"人可以让闭环当场对一次账"的唯一入口。
    契约：{"ok": true, "count": n, "results": [...]}。"""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - 坏 JSON 当空体（= 全量补跑）
        body = {}
    if not isinstance(body, dict):
        body = {}
    task_id = str(body.get("task_id") or "").strip()
    force = bool(body.get("force"))
    targets = [task_id] if task_id else _verify_candidates()
    if not targets:
        return {"ok": True, "count": 0, "results": [],
                "detail": "没有需要补跑验证的目标卡"}
    results = []
    for tid in targets:
        results.append(await verify_goal_async(tid, force=force))
    confirmed = sum(1 for r in results if r.get("verdict") == "confirmed")
    falsified = sum(1 for r in results if r.get("verdict") == "falsified")
    logger.warning("[self_driven] 手动验证 %d 张：成立 %d / 证伪 %d",
                   len(results), confirmed, falsified)
    return {"ok": True, "count": len(results), "results": results,
            "confirmed": confirmed, "falsified": falsified}


@router.get("/self_driven/verdicts")
def self_driven_verdicts(days: int = 7) -> dict:
    """[v5 2026-10-31] 判据台账：GET /api/self_driven/verdicts?days=7

    "自驱是不是活的"这个问题的唯一硬指标出口：近 N 天有几天拿到过真判据、
    预测命中率多少、连续天数多少。纯读状态文件，不调 LLM、不写库、零副作用。
    口径见 verdict_stats 的 docstring（unverifiable 不进分母）。"""
    stats = verdict_stats(days)
    stats["ok"] = True
    return stats


@router.get("/self_driven/queue")
def self_driven_queue() -> dict:
    """[自驱引擎 v2] 只读目标队列（含历史终态）：GET /api/self_driven/queue。
    [v2] 返回全部状态的目标卡（pending_approval/dispatched/done/failed，新→旧），
    前端按 status 分别展示待审批/执行中/已完成/失败（刷新即见历史）；
    + 今日配额水位。只读状态文件，不调 LLM、不写任何库。
    [v5] 附带一次验证补跑调度（60s 节流、daemon 线程、不阻塞本请求）：
    引擎终态事件丢了/执行器上线前的老卡，靠这条路径也能被验证到。"""
    state = _load_state()
    items = [g for g in (state.get("queue") or []) if isinstance(g, dict)]
    try:
        _spawn_verify_sweep()
    except Exception:  # noqa: BLE001 - 补跑调度失败绝不影响队列读取
        pass
    return {"ok": True, "count": len(items), "items": list(reversed(items)),
            "verdicts": verdict_stats(7), **_quota_info(state)}
