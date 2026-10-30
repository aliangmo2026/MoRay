"""intent_router — Laya 意图识别模块（任务一交付物，2026-09-30）。

三级判定（短路）：
  L1 显式覆盖  explicit_mode 为 "chat"/"agent" 时直接生效
               （decided_by="explicit"，confidence=1.0）
  L2 规则层    纯正则/关键词，零模型调用（decided_by="rule"，confidence 0.85~0.95）
  L3 分类器    本机 Ollama qwen3.5:9b（decided_by="classifier"）；
               超时/连接失败/非法 JSON/低于 AGENT_MIN_CONFIDENCE
               → mode="chat"（decided_by="fallback"，confidence=0.0），绝不返回 "agent"

红线遵循（任务书 2026-09-30）：
  * 唯一出站目标是 OLLAMA_BASE_URL（默认 http://127.0.0.1:11434），无任何云端 API 调用；
  * 不依赖 requests/httpx：HTTP 用标准库 urllib.request + 硬超时实现；
  * 降级路径全部显式落 WARNING 日志（携带异常类型与原因），不吞异常；
  * Laya 侧没有本地事件表；event_reporter.EventReporter 是"上行回调接口"
    （POST 127.0.0.1:8000/api/laya/events + 失败落 spool 磁盘，有网络与磁盘副作用），
    不属于"事件表"——按任务书"若接口不存在，只打日志"处理：
    本模块仅输出结构化日志，不写事件、不建表。

兼容性：纯标准库，Python 3.8+（from __future__ import annotations 使 list[dict]/
Literal 等注解在 3.8 下以字符串形式存在，不参与运行时求值）。
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Literal, Optional

logger = logging.getLogger("laya.intent_router")

__all__ = ["IntentInput", "IntentResult", "decide"]


# ---------------------------------------------------------------------------
# 配置（环境变量，带默认值）
# ---------------------------------------------------------------------------

def _env_bool(name: str) -> bool:
    return os.environ.get(name, "true").strip().lower() in ("1", "true", "yes", "on")


AGENT_MIN_CONFIDENCE = float(os.environ.get("AGENT_MIN_CONFIDENCE", "0.7"))
# [2026-10-01 任务A v2/v4] 超时从 800ms 提到 3000ms：给 L3 模型冷加载留余量。
# v4 起模型为 9b（6.6GB，冷加载比 final 更慢），3000ms 必须保留不可再降。
INTENT_CLASSIFIER_TIMEOUT_MS = int(os.environ.get("INTENT_CLASSIFIER_TIMEOUT_MS", "3000"))
# [2026-10-01 任务A v4] 默认模型改回 qwen3.5:9b：
# v3 实测 final（qwen2.5:1.5b 底座）做 chat/agent 二分类仅 12/20，
# 单向偏 agent（chat 类仅 3/10），根因是其 SFT 数据全是 DecisionResult
# 生成任务，零二分类标注，属分布外任务。
# 9b 与主对话共用 → Ollama 只加载一份，零额外显存；泛化能力强。
# final 保留给 self_check 与 /api/decide（它的 SFT 主场）。
# 若需换回 final，设 env INTENT_CLASSIFIER_MODEL=moray-laya-final。
INTENT_CLASSIFIER_MODEL = os.environ.get("INTENT_CLASSIFIER_MODEL", "qwen3.5:9b")
INTENT_CLASSIFIER_ENABLED = _env_bool("INTENT_CLASSIFIER_ENABLED")
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").strip().rstrip("/")


# ---------------------------------------------------------------------------
# 数据结构（字段名严格按任务书，不得改名）
# ---------------------------------------------------------------------------

@dataclass
class IntentInput:
    text: str
    history: list[dict] = field(default_factory=list)
    attachments: list[dict] = field(default_factory=list)
    explicit_mode: Optional[Literal["chat", "agent"]] = None


@dataclass
class IntentResult:
    mode: Literal["chat", "agent"]
    intent_type: Literal["chat", "code", "search", "file_op", "tool_call", "mixed"]
    confidence: float
    tools_hint: list[str] = field(default_factory=list)
    reason: str = ""
    decided_by: Literal["explicit", "rule", "classifier", "fallback"] = "fallback"


# ---------------------------------------------------------------------------
# Level 2 规则层（纯正则/关键词，零模型调用）
# ---------------------------------------------------------------------------

_CMD_VERBS = ("创建", "删除", "重构", "运行", "执行", "写测试",
              "部署", "搜索", "查资料", "安装", "编译", "调试")

# [2026-10-01 任务A v4] 动词+名词组合升 Agent：v3 侦察发现 6/10 agent 请求被
# "<30 字判 chat"短路根本到不了 L3（如"写个脚本""分析日志"），本组合规则补位。
# 判定：同时命中任一动作动词 + 任一可操作名词 → agent（confidence 0.90）。
# 名词表只收"可操作对象"（代码/文件/数据库等工程实体），领域主题词（如"政策"）
# 有意不收：资料检索请求可由对话侧大模型直接承接，误判 agent（拉起执行链）代价更高。
_AGENT_ACTION_VERBS = [
    "写", "做", "搞", "弄", "生成", "创建", "新建", "构建", "开发",
    "修改", "改", "修复", "修", "优化", "重构", "调整", "完善",
    "分析", "查", "搜", "找", "搜一下", "查一下", "看看", "看下",
    "运行", "执行", "跑", "启动", "停", "部署", "发布", "上线",
    "设计", "画", "画个", "画一个", "整理", "汇总", "统计", "计算",
    "推荐", "评估", "对比", "测试", "调试", "排查",
]
# [2026-10-01 任务A v5] "需求"移除：太抽象，不是具体可操作对象。
# 移除后"帮我看看这个需求怎么实现"不再命中"看看+需求"组合，由 L2 短文本规则
# 判 chat（该句 12 字<30，零模型调用）；"看看我桌面上那个文件"仍命中"看看+文件"，
# 不受影响。
_AGENT_OBJECT_NOUNS = [
    "代码", "函数", "方法", "类", "模块", "脚本", "程序", "项目",
    "文件", "目录", "文件夹", "数据", "表", "数据库", "接口", "API",
    "文档", "报告", "方案", "计划", "页面", "组件", "配置", "环境",
    "日志", "错误", "bug", "测试", "用例", "功能", "系统",
    "服务器", "命令", "任务", "流程", "架构", "设计", "模型",
]


def _verb_noun_hit(text: str) -> Optional[tuple[str, str]]:
    """同时命中动作动词与可操作名词时返回 (动词, 名词)，否则 None。"""
    verbs = [v for v in _AGENT_ACTION_VERBS if v in text]
    nouns = [n for n in _AGENT_OBJECT_NOUNS if n in text]
    if verbs and nouns:
        return verbs[0], nouns[0]
    return None

# [2026-10-01 U-6-3] 搜索动词强信号表：文本含任一 → 直接判 agent（confidence 0.85），
# 无需配合名词表。修复"查一下 2026 年最新的新能源补贴政策"误判：U-4 将"政策"挡在
# 名词表外后，该句不命中动名组合、<30 字被短文本规则短路成 chat，落到本地聊天后被
# 判定专用模型 moray-laya-final 回了裸工具调用 JSON。本档置于"命令动词单词"档之前
# （"查一下"比"查"更具体）；intent_type 记为 search，前端 decideMode 据此走 Laya
# Agent 浮层（搜索链路），不会误入 ZCode 代码沙箱。
_SEARCH_VERBS = ["查一下", "查最新", "查查", "搜一下", "搜索", "查一查", "找一下",
                 "帮我查", "帮我搜", "调研", "检索"]


def _search_verb_hit(text: str) -> Optional[str]:
    """命中搜索动词强信号时返回该动词，否则 None（U-6-3，单命中即 agent，无需名词配合）。"""
    for v in _SEARCH_VERBS:
        if v in text:
            return v
    return None
_INDENTED_LINE_RE = re.compile(r"(?m)^ {4}(?=\S)")
_ABS_PATH_RE = re.compile(r"D:\\|/home/|/Users/|/workspace/")
_TOOL_MARK_RE = re.compile(r"@tool/[\w.\-]+|\[tool[^\]]*\]")
_FILE_EXT_RE = re.compile(r"\.(?:py|js|ts|md|json|yaml|sql)\b")
_GREETING_RE = re.compile(r"你好|您好|哈喽|嗨|在吗|早上好|中午好|下午好|晚上好|晚安|hello|hi\b",
                          re.IGNORECASE)
_EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F]")


def _emoji_ratio(text: str) -> float:
    if not text:
        return 0.0
    return len(_EMOJI_RE.findall(text)) / len(text)


def _agent_intent_type(has_fence: bool, has_path: bool, exts: list[str],
                       verbs: list[str], has_tool_mark: bool,
                       search_verb: Optional[str] = None) -> str:
    if has_tool_mark:
        return "tool_call"
    # [2026-10-01 U-6-3] 搜索动词强信号命中 → search：前端 decideMode 对 search 走
    # Laya Agent 浮层；若落默认 tool_call 会被误分流进 ZCode 代码沙箱。
    if search_verb or any(v in verbs for v in ("搜索", "查资料")):
        return "search"
    if has_fence or any(v in verbs for v in ("重构", "编译", "调试", "写测试", "运行", "执行")):
        return "code"
    if has_path or exts or any(v in verbs for v in ("创建", "删除", "部署", "安装")):
        return "file_op"
    return "tool_call"


def _rule_decide(text: str) -> Optional[IntentResult]:
    """Level 2：命中强信号直接判定；全部不命中返回 None（进入 Level 3）。"""
    verbs = [v for v in _CMD_VERBS if v in text]
    has_fence = "```" in text
    has_path = _ABS_PATH_RE.search(text) is not None
    tool_marks = list(_TOOL_MARK_RE.finditer(text))
    exts = sorted(set(_FILE_EXT_RE.findall(text)))

    agent_signals: list[tuple[str, float]] = []
    if has_fence:
        agent_signals.append(("代码围栏```", 0.95))
    if len(_INDENTED_LINE_RE.findall(text)) >= 3:
        agent_signals.append(("4空格缩进≥3行", 0.90))
    if has_path:
        agent_signals.append(("绝对路径", 0.90))
    if tool_marks:
        agent_signals.append((f"工具标记{tool_marks[0].group(0)}", 0.95))
    verb_noun = _verb_noun_hit(text)
    if verb_noun:
        # [2026-10-01 任务A v4] 动词+名词组合：0.90 高于单词命令动词 0.85，
        # 低于围栏/工具标记 0.95（优先级见任务书 2d）
        agent_signals.append(("动词+名词组合:%s+%s" % verb_noun, 0.90))
    # [2026-10-01 U-6-3] 搜索动词强信号档：单命中即 agent（0.85），无需名词配合；
    # 置于"命令动词单词"档之前（"查一下"比"查"具体），修"查一下新能源补贴政策"误判
    search_verb = _search_verb_hit(text)
    if search_verb:
        agent_signals.append(("搜索动词强信号:" + search_verb, 0.85))
    if verbs:
        agent_signals.append(("命令动词:" + "/".join(verbs), 0.85))
    if exts and verbs:
        agent_signals.append(("文件后缀." + "/".join(exts) + "+操作动词", 0.90))

    if agent_signals:
        confidence = max(c for _, c in agent_signals)
        return IntentResult(
            mode="agent",
            intent_type=_agent_intent_type(has_fence, has_path, exts, verbs, bool(tool_marks),
                                           search_verb=search_verb),
            confidence=confidence,
            tools_hint=[m.group(0) for m in tool_marks],
            reason="rule: 命中 " + "; ".join(name for name, _ in agent_signals),
            decided_by="rule")

    # 保持 Chat 强信号（v4 放宽：短文本还须无动词+名词组合才判 chat）
    if len(text) < 30 and not has_path and not has_fence and not verbs and verb_noun is None:
        return IntentResult(mode="chat", intent_type="chat", confidence=0.90,
                            tools_hint=[],
                            reason="rule: 输入<30字且无路径/代码块/命令动词",
                            decided_by="rule")

    if _GREETING_RE.search(text) and _emoji_ratio(text) > 0.5:
        return IntentResult(mode="chat", intent_type="chat", confidence=0.85,
                            tools_hint=[],
                            reason="rule: 纯问候闲聊且 emoji 占比>50%",
                            decided_by="rule")

    return None


# ---------------------------------------------------------------------------
# Level 3 分类器（本机 Ollama，qwen3.5:9b）
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "你是 MoRay/Laya 的意图分类器。只输出一个 JSON 对象，"
    "禁止输出解释、markdown 围栏或任何多余文本。"
    'JSON 格式：{"mode":"chat|agent","intent_type":"chat|code|search|file_op|tool_call|mixed",'
    '"confidence":0.0到1.0}。'
    "mode=agent 表示需要执行代码/文件/工具等实际操作；mode=chat 表示纯对话问答闲聊。"
)

# few-shot 5 例：3 chat / 2 agent（任务书配比）
_FEWSHOT = [
    ("你好，在吗？", {"mode": "chat", "intent_type": "chat", "confidence": 0.95}),
    ("帮我解释一下这段报错是什么意思", {"mode": "chat", "intent_type": "chat", "confidence": 0.90}),
    ("Python 的列表和元组有什么区别", {"mode": "chat", "intent_type": "chat", "confidence": 0.90}),
    ("创建 D:\\MoRay\\workspace\\main.py 并写入入口代码",
     {"mode": "agent", "intent_type": "file_op", "confidence": 0.95}),
    ("```python\nprint(1)\n```\n运行这段代码",
     {"mode": "agent", "intent_type": "code", "confidence": 0.95}),
]


def _build_messages(text: str) -> list[dict]:
    messages = [{"role": "system", "content": _SYSTEM_PROMPT}]
    for user_text, answer in _FEWSHOT:
        messages.append({"role": "user", "content": user_text})
        messages.append({"role": "assistant", "content": json.dumps(answer, ensure_ascii=False)})
    messages.append({"role": "user", "content": text})
    return messages


def _classify_raw(text: str) -> str:
    """调本机 Ollama /api/chat（stream=false），返回 assistant 原始文本。

    任务书写 max_tokens=64：Ollama 原生协议等价项是 options.num_predict；
    temperature=0 同任务书。网络/协议错误原样向上抛，由 _classifier_decide 统一降级。
    """
    payload = {
        "model": INTENT_CLASSIFIER_MODEL,
        "messages": _build_messages(text),
        "stream": False,
        # [2026-10-01 任务A v5] think:False：qwen3.5:9b 是 thinking 模型，
        # num_predict=64 会被思考过程吃光 → content 恒空 → 恒降级 chat。
        # 关闭思考后 812ms 直接输出合法 JSON。若换非 thinking 模型，此项无副作用。
        "think": False,
        # [2026-10-01 任务A v2/v4] 显式 keep_alive=30s：L3 模型用完 30 秒自动释放
        # 槽位，不依赖全局 OLLAMA_KEEP_ALIVE=30m；30 秒内连续触发复用热模型。
        # v4 注：L3 与主对话同为 9b，同模型零互踢；但请求级 30s 会把主对话的
        # 30m 驻留重置为 30s，主对话隔 >30s 复调时需承受一次 9b 冷加载（6.6GB）。
        "keep_alive": "30s",
        "options": {"temperature": 0, "num_predict": 64},
    }
    request = urllib.request.Request(
        OLLAMA_BASE_URL + "/api/chat",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    timeout_s = max(INTENT_CLASSIFIER_TIMEOUT_MS, 1) / 1000.0
    with urllib.request.urlopen(request, timeout=timeout_s) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body["message"]["content"]


_VALID_INTENT_TYPES = ("chat", "code", "search", "file_op", "tool_call", "mixed")


def _parse_classifier_output(raw: str) -> Optional[tuple]:
    """把模型输出解析成 (mode, confidence, intent_type|None)；任何非法形态返回 None。"""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"```\s*$", "", text).strip()
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    mode = data.get("mode")
    if mode not in ("chat", "agent"):
        return None
    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        return None
    confidence = max(0.0, min(1.0, confidence))
    intent_type = data.get("intent_type")
    if intent_type not in _VALID_INTENT_TYPES:
        intent_type = None
    return mode, confidence, intent_type


def _fallback_result(reason: str) -> IntentResult:
    """任务书硬约束：分类器任何失败形态都必须落到 chat，绝不 agent。"""
    return IntentResult(mode="chat", intent_type="chat", confidence=0.0,
                        tools_hint=[], reason=reason, decided_by="fallback")


def _classifier_decide(text: str) -> IntentResult:
    if not INTENT_CLASSIFIER_ENABLED:
        return _fallback_result("分类器未启用（INTENT_CLASSIFIER_ENABLED=false）")
    try:
        raw = _classify_raw(text)
    except Exception as exc:  # 任务书降级路径：超时/连接失败/协议异常统一降级 chat；WARNING 带原因落日志，非吞异常
        logger.warning("[intent_router] 分类器调用失败，降级 chat：%s: %s",
                       type(exc).__name__, exc)
        return _fallback_result(f"分类器调用失败（{type(exc).__name__}），按任务书降级 chat")
    parsed = _parse_classifier_output(raw)
    if parsed is None:
        logger.warning("[intent_router] 分类器输出非法 JSON：%s", (raw or "")[:120])
        return _fallback_result("分类器输出非法 JSON，按任务书降级 chat")
    mode, confidence, intent_type = parsed
    if confidence < AGENT_MIN_CONFIDENCE:
        return _fallback_result(
            f"分类器 confidence {confidence:.2f} 低于阈值 {AGENT_MIN_CONFIDENCE:.2f}，降级 chat")
    if intent_type is None:
        intent_type = "chat" if mode == "chat" else "tool_call"
    return IntentResult(
        mode=mode, intent_type=intent_type, confidence=confidence,
        tools_hint=[],
        reason=f"classifier: 本地模型 {INTENT_CLASSIFIER_MODEL} 判定 mode={mode}",
        decided_by="classifier")


# ---------------------------------------------------------------------------
# 公开接口
# ---------------------------------------------------------------------------

def decide(inp: IntentInput) -> IntentResult:
    """意图判定公开入口（三级短路：explicit → rule → classifier/fallback）。"""
    started = time.monotonic()
    result = _decide_inner(inp)
    latency_ms = int((time.monotonic() - started) * 1000)
    logger.info("intent_decided %s", json.dumps({
        "trace_id": uuid.uuid4().hex,
        "mode": result.mode,
        "intent_type": result.intent_type,
        "confidence": round(result.confidence, 3),
        "decided_by": result.decided_by,
        "reason": result.reason,
        "latency_ms": latency_ms,
    }, ensure_ascii=False))
    return result


def _decide_inner(inp: IntentInput) -> IntentResult:
    # Level 1：显式覆盖
    if inp.explicit_mode in ("chat", "agent"):
        intent_type = "chat" if inp.explicit_mode == "chat" else "tool_call"
        return IntentResult(
            mode=inp.explicit_mode, intent_type=intent_type, confidence=1.0,
            tools_hint=[],
            reason=f"explicit_mode={inp.explicit_mode} 直接生效，跳过规则与分类器",
            decided_by="explicit")
    text = inp.text or ""
    # Level 2：规则层
    rule_result = _rule_decide(text)
    if rule_result is not None:
        return rule_result
    # Level 3：分类器（失败形态在内部全部降级 chat）
    return _classifier_decide(text)
