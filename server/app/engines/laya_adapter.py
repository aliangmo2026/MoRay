"""[引擎解耦 v1 2026-10-21] LayaEngine：把 8001 的 Laya 决策编排服务挂上通用引擎底座。

契约面（两层，与 ZCodeAdapter 同构）：
- ExecutionEngine 五方法 capabilities/start/status/cancel/result —— Run 语义轮询面；
- 流式扩展四方法 start_stream/stream_events/kill_stream_task/read_task_file ——
  api.py /api/engines/{engine_name}/* 通用派发只认这四个方法名（getattr 可调用性硬检查，
  缺一整个引擎回 engine_unsupported，故 read_task_file 即使无产物可读也必须在）。

结构性桥接（本文件存在的理由）：Laya 的 POST /execute 是"调用即流"——响应体直接是 SSE
（start → step_start/step_result/check/retry → final 帧），没有"受理拿 id、再另开端点拉流"
的两段式；而引擎契约要求 start_stream 受理即返回 task_id、事件走 stream_events 订阅。
照抄 ZCodeAdapter 的"reader 线程 + asyncio.Queue"骨架桥接：后台线程消费 Laya 的 SSE，
把帧原文投进队列，stream_events 再从队列取。流式语义逐字对齐 zcode_adapter：
"data: <原始行>\\n\\n" 逐条透传不重序列化、"[DONE]" 哨兵、15s keep-alive 注释行。

Laya 帧词汇（decision-kernel/lora-training/modules/executor.py）：start / step_start /
step_result / check / retry / step_end / final；**终态只认 event=="final"**——step_result
帧也带 status: ok|failed 字段，按帧类型误读会把重试中的失败步当成整个任务失败（任务书
骨架即有此坑）。final.status ∈ {success, failed, cancelled}，与引擎四态映射见 _on_frame
（cancelled → failed + error="canceled"，base.py 收敛口径）。传输层失败时补发一帧合成
final{status:failed}——与 laya_std_server._sse() 自身的异常收尾形状一致，让消费端有闭合
终态可依，而非只留半条流。

出站 URL 安全闸（_assert_safe_url）：仅 http/https；host 校验钉死"同源于运维配置的 Laya
端点"，出源 URL 一律按环回/私有/链路本地/保留地址黑名单拒绝（SSRF 面向用户可控 URL 的
防线）。例外说明：本引擎的出站目标（默认 http://localhost:8001）就是需求明文指定的同机
兄弟服务，属受信部署拓扑而非用户输入——若对配置端点也按黑名单一刀切，本引擎无法存在；
故同源放行、出源拒绝。今天 body 只有 prompt/max_retry、无任何 URL 字段，此闸为未来扩展
（如接受用户回调地址）预留。
"""
import asyncio
import ipaddress
import json
import logging
import os
import threading
import time
import urllib.parse
import uuid

import httpx

from .base import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_RUNNING,
    ExecutionEngine,
    StreamTaskError,
)

_LOG = logging.getLogger("moray.laya")

#: Laya 服务默认地址（同机兄弟服务；可用环境变量 MORAY_LAYA_BASE 覆盖）
DEFAULT_LAYA_BASE = "http://localhost:8001"

# /execute 是长任务流：读超时放空（一步慢工具可达分钟级），只卡连接/写/池
_STREAM_TIMEOUT = httpx.Timeout(connect=5.0, read=None, write=15.0, pool=5.0)
_RPC_TIMEOUT = httpx.Timeout(5.0)   # health / cancel 这类小 RPC
_KEEPALIVE_SEC = 15.0               # 与 zcode_adapter.stream_events 同值
_STALE_TASK_SEC = 3600              # 终态记录惰性回收窗口（zcode 同款 1h）


# ------------------------------------------------------------ 出站 URL 安全闸
def _is_forbidden_host(host: str) -> bool:
    """环回/私有/链路本地/保留/未指定地址判定（黑名单，面向用户可控 URL）。"""
    h = (host or "").strip().lower().strip("[]")
    if not h:
        return True
    if h == "localhost" or h.endswith(".localhost") or h.endswith(".localdomain"):
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False  # 普通主机名：DNS 解析校验不在本闸范围（用户可控 URL 今天不存在）
    return bool(ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified)


def _origin(parts) -> tuple:
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return (parts.scheme, (parts.hostname or "").lower(), port)


def _assert_safe_url(url: str, trusted_base: str | None = None) -> None:
    """出站前校验：仅 http/https；host 必须同源于运维配置端点，否则过禁用地址黑名单。

    trusted_base 即本引擎配置的 Laya 端点（需求指定的同机服务，受信部署拓扑）——
    同源放行，其余 URL 一律 _is_forbidden_host 判黑。非法 raise StreamTaskError，
    由 api.py 路由层翻译为 _bad(code, message, status)。
    """
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise StreamTaskError("bad_url", f"非法出站 URL（仅允许 http/https）：{url}", 400)
    if trusted_base and _origin(parts) == _origin(urllib.parse.urlsplit(trusted_base)):
        return
    if _is_forbidden_host(parts.hostname):
        raise StreamTaskError("bad_url", f"拒绝访问环回/私有/保留地址：{parts.hostname}", 400)


def _post(loop, queue, item: str) -> None:
    """reader 线程 → 事件循环队列（call_soon_threadsafe 线程安全投递，zcode 同款）。"""
    if loop is None or queue is None:
        return
    try:
        loop.call_soon_threadsafe(queue.put_nowait, item)
    except RuntimeError:
        pass  # 事件循环已关：订阅端已走远，丢帧无害


class LayaEngine(ExecutionEngine):
    """Laya 决策编排引擎：goal → planner 拆解 → 串行执行 → 自检重试，SSE 实时进度。"""

    NAME = "laya"
    DESCRIPTION = "Laya 决策编排引擎（8001）：goal→planner 拆解→串行执行→自检重试，SSE 进度流"

    def __init__(self, base_url: str | None = None):
        # 构造零副作用（/api/engines/list 的模块级实例化安全，与 zcode 同惯例）
        self.base_url = (base_url or os.environ.get("MORAY_LAYA_BASE")
                         or DEFAULT_LAYA_BASE).rstrip("/")
        self._tasks: dict = {}
        self._lock = threading.Lock()

    def capabilities(self) -> dict:
        # 编排引擎经工具注册表驱动 zcode/iris/argus/file 等下游——can_exec/can_network
        # 诚实申报 True；产物文件不落地本侧（read_task_file 不支持）故读写 False。
        return {"can_read": False, "can_write": False,
                "can_exec": True, "can_network": True, "is_isolated": False}

    def unavailable_reason(self) -> str | None:
        """[Day23.6] 探针钩子：GET /health 探 Laya 存活；/api/engines/list 据此透出 offline。"""
        url = self.base_url + "/health"
        try:
            _assert_safe_url(url, self.base_url)
            r = httpx.get(url, timeout=_RPC_TIMEOUT)
        except StreamTaskError:
            return "Laya 端点配置非法（非 http/https）"
        except Exception as e:  # noqa: BLE001 - 探针失败一律按不可用，绝不崩列表
            return "Laya 服务不可达：%s" % type(e).__name__
        if r.status_code == 200:
            return None
        return "Laya 服务异常（HTTP %d）" % r.status_code

    # ==================================================== ExecutionEngine 五方法
    def start(self, task: dict) -> str:
        """同步受理（Run 语义）：起消费线程吃完整条流，调用方轮询 status()/result()。

        不合法任务按 base.py 口径"立即置为 failed（带 error）"，不静默吞。
        """
        try:
            prompt, max_retry = self._parse_task(task)
        except StreamTaskError as e:
            rec = self._new_rec(uuid.uuid4().hex, str(task)[:80] if task else "", kind="sync")
            with self._lock:
                rec["status"] = STATUS_FAILED
                rec["error"] = "bad_request: %s" % e.message
                rec["done_at"] = time.time()
            return rec["task_id"]
        task_id = uuid.uuid4().hex
        rec = self._new_rec(task_id, prompt, kind="sync")
        threading.Thread(target=self._run_sync,
                         args=(rec, self._payload(prompt, max_retry, task_id)),
                         name="moray-laya-sync-" + task_id[:8], daemon=True).start()
        return task_id

    def status(self, task_id: str) -> dict:
        rec = self._get(task_id)
        return {"task_id": task_id, "status": rec["status"], "error": rec.get("error")}

    def cancel(self, task_id: str) -> bool:
        """取消：给 Laya 发置位信号，并把记录收敛 failed(error="canceled")（base.py 口径）。

        Laya 的取消是步骤边界信号、在飞步骤不可中断；本方法按契约**立即**收敛本地终态，
        远端在下一边界收尾后照常补 final 帧（终态已定、_on_frame 不回写覆盖）。
        True=取消成功；False=未知任务/已终态/置位失败（base.py：False=无法取消）。
        """
        with self._lock:
            rec = self._tasks.get(task_id)
            if rec is None or rec["status"] not in (STATUS_PENDING, STATUS_RUNNING):
                return False
        if not self._cancel_remote(task_id):
            return False
        with self._lock:
            if rec["status"] in (STATUS_PENDING, STATUS_RUNNING):
                rec["status"] = STATUS_FAILED
                rec["error"] = "canceled"
                rec["done_at"] = time.time()
        return True

    def result(self, task_id: str) -> dict:
        rec = self._get(task_id)
        return {"task_id": task_id, "status": rec["status"],
                "result": rec.get("result"), "error": rec.get("error")}

    # ==================================================== 流式扩展四方法
    def start_stream(self, body: dict) -> dict:
        """受理流式任务：起消费线程连 Laya /execute，即刻返回（api.py 扁平出参契约）。

        出参 {"ok": True, "task_id", "status": "running"}（与 GET /stream 的 SSE 契约配对，
        前端按 task_id 直读）。需在事件循环上下文调用（内部取 running loop 供 reader 投递，
        zcode_adapter 同款约定）；校验失败 raise StreamTaskError。
        """
        prompt, max_retry = self._parse_task(body)
        task_id = uuid.uuid4().hex
        rec = self._new_rec(task_id, prompt, kind="stream")
        rec["queue"] = asyncio.Queue()
        rec["loop"] = asyncio.get_running_loop()
        threading.Thread(target=self._run_stream,
                         args=(rec, self._payload(prompt, max_retry, task_id)),
                         name="moray-laya-stream-" + task_id[:8], daemon=True).start()
        return {"ok": True, "task_id": task_id, "status": "running"}

    def stream_events(self, task_id: str):
        """SSE 订阅：**同步方法**返回 async 生成器——校验与 raise 发生在调用时而非迭代时。

        api.py 以 try/except 包裹调用并把 StreamTaskError 翻译为 404/400；若写成 async
        生成器函数（任务书骨架如此），raise 会被拖进迭代期，路由层错误翻译整段失效、
        流已 200 开写才炸——故按 zcode_adapter.py:1276 的形状（普通 def + 内嵌 _gen）。

        语义逐字对齐 zcode：15s 无事件发 ": keep-alive\\n\\n"；"[DONE]" 哨兵后收尾；
        无重连语义（流结束后再订阅 404）。与 zcode 的唯一差别：登记表条目**不物理删除**
        （留给 status()/result()/engines_list 实时态），以 stream_closed 标记拒绝二次订阅，
        外部可观测语义不变。
        """
        with self._lock:
            rec = self._tasks.get(task_id)
        if rec is None:
            raise StreamTaskError("not_found", f"任务不存在或已回收：{task_id}", 404)
        if rec.get("queue") is None:
            raise StreamTaskError("engine_unsupported",
                                  f"任务以同步受理（start()），无事件流可订阅：{task_id}", 400)
        if rec.get("stream_closed"):
            raise StreamTaskError("not_found", f"任务流已结束（无重连语义）：{task_id}", 404)
        queue = rec["queue"]

        async def _gen():
            try:
                while True:
                    try:
                        item = await asyncio.wait_for(queue.get(), timeout=_KEEPALIVE_SEC)
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    if item == "[DONE]":
                        yield "data: [DONE]\n\n"
                        break
                    yield "data: " + item + "\n\n"
            finally:
                with self._lock:
                    rec["stream_closed"] = True

        return _gen()

    async def kill_stream_task(self, task_id: str) -> dict:
        """终止：给 Laya 发 /execute/cancel 置位信号（步骤边界生效），如实回报。

        与 zcode 的"杀进程树"本质不同：Laya 的取消是**信号**、在飞步骤不可中断。故本方法
        不断本地流（提前断流会把终态帧丢掉）——SSE 在 Laya 下一边界补 final(cancelled)
        后自然收尾；killed=True 仅表示置位成功。记录状态仍由 reader 的终态帧收敛
        （kill 与状态机解耦，zcode kill 与弹表解耦同思路）。
        """
        with self._lock:
            if task_id not in self._tasks:
                raise StreamTaskError("not_found", f"任务不存在或已回收：{task_id}", 404)
        url = self.base_url + "/execute/cancel"
        _assert_safe_url(url, self.base_url)
        try:
            async with httpx.AsyncClient(timeout=_RPC_TIMEOUT) as client:
                r = await client.post(url, json={"execution_id": task_id})
            data = (r.json() or {}) if r.status_code == 200 else {}
        except Exception as e:  # noqa: BLE001 - 传输失败统一错误契约，不裸抛 httpx 异常
            raise StreamTaskError("laya_unreachable", "Laya /execute/cancel 调用失败：%s: %s"
                                  % (type(e).__name__, str(e)[:200]), 502) from e
        cancelled = bool(data.get("cancelled"))
        return {"ok": True, "task_id": task_id, "killed": cancelled,
                "reason": "cancel_signal_set" if cancelled else "not_running"}

    def read_task_file(self, task_id: str, filename: str):
        """Laya 无 per-task 沙箱产物目录，不支持产物预览（404）。

        本方法**必须存在**：api.py 通用派发对四方法做 getattr 可调用性硬检查，缺任一
        整个引擎回 engine_unsupported（base.py StreamTaskError docstring）。
        """
        raise StreamTaskError("not_found", f"Laya 引擎无任务产物文件：{task_id}/{filename}", 404)

    # ==================================================== 内部：任务与状态机
    def _parse_task(self, body) -> tuple:
        if not isinstance(body, dict):
            raise StreamTaskError("bad_request", "请求体必须是 JSON 对象")
        prompt = body.get("prompt", body.get("goal"))  # goal 是 Laya 原生字段名，接受别名
        if not isinstance(prompt, str) or not prompt.strip():
            raise StreamTaskError("bad_request", "缺少 prompt 字段（要执行的任务指令）")
        try:
            max_retry = max(0, int(body.get("max_retry") or 5))
        except (TypeError, ValueError):
            max_retry = 5
        return prompt.strip(), max_retry

    def _payload(self, prompt: str, max_retry: int, task_id: str) -> dict:
        # prompt → Laya ExecuteRequest：goal 即用户指令；execution_id 钉我们的 task_id，
        # 使 /execute/cancel 的关联键与引擎侧 task_id 一致（cancel/kill 才对得上）
        return {"goal": prompt, "max_retry": max_retry, "execution_id": task_id}

    def _new_rec(self, task_id: str, prompt: str, kind: str) -> dict:
        self._sweep_stale()
        rec = {
            "task_id": task_id,
            "status": STATUS_RUNNING,
            "prompt": prompt,
            "task": {"action": prompt[:80]},   # _engine_live_state 的 current_task.name 数据源
            "kind": kind,                       # sync=五方法面 / stream=流式面
            "queue": None,
            "loop": None,
            "result": None,
            "error": None,
            "stream_closed": False,
            "created_at": time.time(),
            "started_at": time.time(),
            "done_at": None,
        }
        with self._lock:
            self._tasks[task_id] = rec
        return rec

    def _get(self, task_id: str) -> dict:
        with self._lock:
            rec = self._tasks.get(task_id)
        if rec is None:
            raise KeyError(task_id)  # base.py 契约：未知 task_id 抛 KeyError
        return rec

    def _sweep_stale(self) -> None:
        """终态记录 1h 后惰性回收（zcode 同款）；pending/running 一律不清——清了会让
        status()/result() 对活着的任务误抛 KeyError。"""
        now = time.time()
        with self._lock:
            for tid in [t for t, r in self._tasks.items()
                        if r.get("done_at") and now - r["done_at"] > _STALE_TASK_SEC]:
                self._tasks.pop(tid, None)

    def _cancel_remote(self, task_id: str) -> bool:
        """POST /execute/cancel；返回 Laya 是否确认置位（cancelled 字段）。"""
        url = self.base_url + "/execute/cancel"
        try:
            _assert_safe_url(url, self.base_url)
            r = httpx.post(url, json={"execution_id": task_id}, timeout=_RPC_TIMEOUT)
            if r.status_code != 200:
                _LOG.warning("[laya] cancel 非 200：task=%s status=%s", task_id, r.status_code)
                return False
            return bool((r.json() or {}).get("cancelled"))
        except Exception as e:  # noqa: BLE001 - 取消失败如实报 False，不崩调用方
            _LOG.warning("[laya] cancel 失败：task=%s err=%s: %s",
                         task_id, type(e).__name__, str(e)[:200])
            return False

    def _consume_stream(self, rec: dict, payload: dict, on_line) -> None:
        """消费 Laya /execute 的 SSE 流：每条 data: 载荷回调 on_line(原文)。

        传输层失败统一转 StreamTaskError（502），由调用方的线程收尾补合成终态帧。
        """
        url = self.base_url + "/execute"
        _assert_safe_url(url, self.base_url)
        try:
            with httpx.stream("POST", url, json=payload, timeout=_STREAM_TIMEOUT) as r:
                if r.status_code != 200:
                    raise StreamTaskError("laya_http_error",
                                          "Laya /execute 返回 HTTP %d" % r.status_code, 502)
                for line in r.iter_lines():
                    if not line or line.startswith(":"):
                        continue  # 空行 / SSE 注释行（保活）
                    if line.startswith("data:"):
                        on_line(line[5:].strip())
        except StreamTaskError:
            raise
        except Exception as e:  # noqa: BLE001 - 连接失败/流中断等传输层异常
            raise StreamTaskError("laya_unreachable", "Laya /execute 调用失败：%s: %s"
                                  % (type(e).__name__, str(e)[:200]), 502) from e

    def _run_stream(self, rec: dict, payload: dict) -> None:
        """流式面消费线程：帧原文投 asyncio.Queue（zcode reader 同款），终态改登记表。"""
        queue, loop = rec["queue"], rec["loop"]
        try:
            self._consume_stream(
                rec, payload,
                lambda s: (self._on_frame(rec, s), _post(loop, queue, s)))
        except StreamTaskError as e:
            self._fail_transport(rec, e.message, loop, queue)
        finally:
            self._ensure_terminal(rec)
            _post(loop, queue, "[DONE]")

    def _run_sync(self, rec: dict, payload: dict) -> None:
        """五方法面消费线程：只吃流改状态，无队列、无事件循环依赖（start() 可同步调）。"""
        try:
            self._consume_stream(rec, payload, lambda s: self._on_frame(rec, s))
        except StreamTaskError as e:
            self._fail_transport(rec, e.message, None, None)
        finally:
            self._ensure_terminal(rec)

    def _fail_transport(self, rec: dict, message: str, loop, queue) -> None:
        """传输层失败 → 补发合成 final{status:failed}（形状对齐 laya_std_server._sse 的
        异常收尾帧），让消费端拿到闭合终态；已见过真 final 则不再补。"""
        frame = json.dumps({"event": "final", "status": "failed", "error": message},
                           ensure_ascii=False)
        with self._lock:
            already_done = rec["done_at"] is not None
        if not already_done:
            self._on_frame(rec, frame)
            _post(loop, queue, frame)

    def _on_frame(self, rec: dict, raw: str) -> None:
        """帧状态机：**只认 event=="final"**（step_result 也带 status 字段，误读会把重试
        中的失败步当整任务失败）。终态只定一次：cancel()/传输层收尾先定则不回写覆盖。"""
        try:
            d = json.loads(raw)
        except ValueError:
            return
        if not isinstance(d, dict) or d.get("event") != "final":
            return
        st = str(d.get("status") or "")
        with self._lock:
            if rec["done_at"] is not None:
                return
            if st == "success":
                rec["status"] = STATUS_COMPLETED
                rec["result"] = {"result": d.get("result"), "steps": d.get("steps"),
                                 "note": d.get("note"), "execution_id": d.get("execution_id")}
            elif st == "cancelled":
                rec["status"] = STATUS_FAILED
                rec["error"] = "canceled"  # base.py：取消收敛 failed(error="canceled")
            else:
                rec["status"] = STATUS_FAILED
                rec["error"] = str(d.get("error") or ("laya_final_status=" + st if st
                                                      else "laya_final_missing_status"))
            rec["done_at"] = time.time()

    def _ensure_terminal(self, rec: dict) -> None:
        """流结束仍未见 final 帧 → 如实 failed（zcode 未截到 result 帧同口径）。"""
        with self._lock:
            if rec["done_at"] is None:
                rec["status"] = STATUS_FAILED
                rec["error"] = "stream ended without final frame"
                rec["done_at"] = time.time()
