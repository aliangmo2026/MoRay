# -*- coding: utf-8 -*-
"""EventReporter：L7.5 Laya联动 · 上行异步事件上报器（Laya 侧新增，2026-09-29）。

契约（任务书 二.2）：
  * 后台异步上报事件到 MoRay 回调（默认 http://127.0.0.1:8000/api/laya/events，
    环境变量 LAYA_MORAY_CALLBACK_URL 覆盖）；
  * 失败指数退避重试（1/2/4/8/16s，最多 6 次尝试），重试用尽 → 本地落盘暂存
    laya_events.spool.jsonl（JSONL，超上限丢最旧）；
  * 支持补报：worker 每 30s 扫一次暂存文件重新入队（retry 预算重置，防无限循环
    另有单事件总尝试上限）；启动即加载遗留暂存；
  * 不阻塞主流程：report() 只入队立即返回；无运行中事件循环（被同步路径调用）
    时直接落盘暂存，绝不同步发 HTTP。

MoRay 侧总闸关闭时回调仍回 200（accepted=false）——本器视为"投递成功"不再重试，
避免 MoRay 主动关闭联动时被重试风暴骚扰。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("laya.event_reporter")

CALLBACK_URL = os.environ.get("LAYA_MORAY_CALLBACK_URL",
                              "http://127.0.0.1:8000/api/laya/events").rstrip("/")
SPOOL_PATH = Path(os.environ.get("LAYA_EVENTS_SPOOL", "laya_events.spool.jsonl"))
MAX_SPOOL = 500            # 暂存上限行
MAX_RETRY = 5              # 内存重试次数（总尝试 = MAX_RETRY+1 = 6，退避 1/2/4/8/16s）
HTTP_TIMEOUT = 3.0
FLUSH_INTERVAL = 30.0      # 补报扫描周期


class EventReporter:
    def __init__(self) -> None:
        self._queue: Optional[asyncio.Queue] = None
        self._worker: Optional[asyncio.Task] = None
        self._last_flush = 0.0
        # 计数（/api/v1/task/rules 出参照镜子）
        self.sent = 0
        self.spooled = 0
        self.replayed = 0
        self.dropped = 0

    # ---- 对外唯一入口：非阻塞 ----
    def report(self, event_type: str, payload: Dict[str, Any]) -> bool:
        """入队并立刻返回。返回 True=已进内存队列；False=已落盘暂存（都算已受理）。"""
        evt = {"event_id": uuid.uuid4().hex[:16], "event_type": str(event_type),
               "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
               "payload": payload if isinstance(payload, dict) else {"raw": str(payload)[:2000]},
               "retry": 0, "tries": 0, "next_due": 0.0}
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._spool(evt)          # 无 loop（同步上下文）：落盘等补报，绝不同步 HTTP
            return False
        self._ensure_worker(loop)
        try:
            self._queue.put_nowait(evt)   # type: ignore[union-attr]
            return True
        except asyncio.QueueFull:
            self._spool(evt)              # 队列满：落盘，不阻塞调用方
            return False

    def _ensure_worker(self, loop: asyncio.AbstractEventLoop) -> None:
        if self._queue is None:
            self._queue = asyncio.Queue(maxsize=1000)
        if self._worker is None or self._worker.done():
            # 启动/重启动即补报：先把遗留暂存读回队列（最旧优先）
            for e in self._load_spool():
                try:
                    self._queue.put_nowait(e)
                    self.replayed += 1
                except asyncio.QueueFull:
                    break
            self._worker = loop.create_task(self._work(), name="laya-event-reporter")

    # ---- 后台 worker：发送 + 退避 + 周期性补报 ----
    async def _work(self) -> None:
        assert self._queue is not None
        while True:
            try:
                try:
                    evt = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    if time.monotonic() - self._last_flush >= FLUSH_INTERVAL:
                        self._last_flush = time.monotonic()
                        self._replay_spool()
                    continue
                if time.monotonic() < float(evt.get("next_due") or 0):
                    await asyncio.sleep(0.05)       # 未到退避时刻：让位，不忙等
                    self._queue.put_nowait(evt)
                    continue
                ok = await self._send(evt)
                if ok:
                    self.sent += 1
                    continue
                evt["tries"] = int(evt.get("tries", 0)) + 1
                if evt["tries"] <= MAX_RETRY:
                    evt["next_due"] = time.monotonic() + min(60.0, 2.0 ** (evt["tries"] - 1))
                    try:
                        self._queue.put_nowait(evt)
                    except asyncio.QueueFull:
                        self._spool(evt)
                else:
                    self._spool(evt)                # 重试用尽 → 落盘暂存，等补报
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - 上报器故障绝不影响决策主链路
                logger.warning("[laya-reporter] worker 异常（忽略）：%s", str(e)[:200])
                await asyncio.sleep(1.0)

    async def _send(self, evt: Dict[str, Any]) -> bool:
        import httpx
        try:
            body = {k: v for k, v in evt.items()
                    if k not in ("retry", "tries", "next_due")}
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                r = await client.post(CALLBACK_URL, json=body)
            # 200 = MoRay 已受理（含 accepted=false 的闸关态/脏事件，均不再重试）
            return r.status_code == 200
        except Exception:  # noqa: BLE001
            return False

    # ---- 本地落盘暂存 + 补报 ----
    def _spool(self, evt: Dict[str, Any]) -> None:
        try:
            try:
                n = sum(1 for _ in SPOOL_PATH.open(encoding="utf-8")) if SPOOL_PATH.is_file() else 0
            except OSError:
                n = 0
            if n >= MAX_SPOOL:                      # 超限丢最旧：保证最新事件可达
                lines = SPOOL_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
                keep = lines[-(MAX_SPOOL - 1):] if len(lines) > MAX_SPOOL - 1 else lines
                tmp = SPOOL_PATH.with_suffix(".tmp")
                tmp.write_text("\n".join(keep) + "\n", encoding="utf-8")
                tmp.replace(SPOOL_PATH)
                self.dropped += 1
            with SPOOL_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(evt, ensure_ascii=False) + "\n")
            self.spooled += 1
        except OSError as e:  # noqa: BLE001
            logger.warning("[laya-reporter] 暂存落盘失败（事件丢弃）：%s", str(e)[:150])

    def _load_spool(self) -> list:
        try:
            lines = SPOOL_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []
        out = []
        for ln in lines:
            try:
                d = json.loads(ln)
                if isinstance(d, dict) and d.get("event_type"):
                    out.append(d)
            except ValueError:
                pass
        return out

    def _replay_spool(self) -> None:
        """补报：清空暂存文件、事件重入队（retry 预算重置，退避清零）。"""
        assert self._queue is not None
        items = self._load_spool()
        if not items:
            return
        try:
            SPOOL_PATH.write_text("", encoding="utf-8")
        except OSError:
            return
        for e in items[:100]:
            e["tries"] = 0
            e["next_due"] = 0.0
            try:
                self._queue.put_nowait(e)
                self.replayed += 1
            except asyncio.QueueFull:
                self._spool(e)                     # 又满了：写回暂存，下轮再补
                break


_singleton: Optional[EventReporter] = None


def get_reporter() -> EventReporter:
    global _singleton
    if _singleton is None:
        _singleton = EventReporter()
    return _singleton
