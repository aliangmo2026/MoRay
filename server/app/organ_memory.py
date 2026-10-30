# -*- coding: utf-8 -*-
"""器官→记忆 三根线共享写入钩子（2026-10-04）。

Argus（研究报告）/ ZCode（流式任务）/ Iris（修复任务）三条器官线成功收尾时各调一次
write_organ_memory，产出落进 MemoryBridge（server/data/memory.sqlite3）。

形态照抄 executor.py:115-158（M2 记忆钩子）的旁路惯例，两点简化差异：
  * 同进程直调 memory_routes.insert_memory（save 端点与反思共用同一 INSERT 汇聚点，
    主表 + FTS 单事务双写），不再经 httpx POST /api/memory/save——三线全在 8000 进程内；
  * daemon 线程体只做落库，content 由各线调用方组好传入（本模块不懂器官语义）。
任何异常全吞只记 warning，绝不影响器官主流程（fail-open，但失败可见）。

总闸 MORAY_ORGAN_MEMORY（语义同 MORAY_MEMORY_HOOK：默认开，"0"/false/no/off 关；
调用时读 env，改环境变量后重启 8000 生效）。config.ORGAN_MEMORY_ENABLED 是同一 env
的启动快照（MORAY_REFLECTION 同款形状），供其他消费方/测试直读，两处以调用时读取为准。

kind 约定：三线一律写 "task_result"——反思循环原料是 WHERE kind='task_result' 硬匹配
（reflection.py:74-76），器官区分靠 source（argus_research / zcode_stream / task_flow）。

日志说明：本仓自定义 logger 的 INFO 不上控制台（main.py:45-46 实读），写入成败一律
warning，保证验收可观测。
"""
import logging
import os
import threading

from .memory_routes import insert_memory

logger = logging.getLogger("moray.organ_memory")

#: 单条 content 上限（器官原文可到几 KB，统一钳制，防记忆库无界膨胀）
_MAX_CONTENT = 2000


def _organ_memory_enabled() -> bool:
    """总闸：调用时读 MORAY_ORGAN_MEMORY（默认开；"0"/false/no/off = 关）。"""
    return os.environ.get("MORAY_ORGAN_MEMORY", "1").strip().lower() not in ("0", "false", "no", "off")


def _insert_worker(content: str, kind: str, tags, source: str, entity_id: str) -> None:
    """daemon 线程体：直调 insert_memory；成败/异常都落 warning，任何异常绝不上抛。"""
    try:
        mem_id, err = insert_memory(content, kind=kind, tags=tags,
                                    source=source, entity_id=entity_id)
        if err:
            logger.warning("[organ-memory] 写库失败：source=%s entity=%s err=%s",
                           source, entity_id, err)
        else:
            logger.warning("[organ-memory] 已写入：source=%s entity=%s id=%s",
                           source, entity_id, mem_id)
    except Exception as e:  # noqa: BLE001 - 旁路写入失败绝不影响器官主流程
        logger.warning("[organ-memory] 写入异常：source=%s entity=%s err=%s: %s",
                       source, entity_id, type(e).__name__, str(e)[:200])


def write_organ_memory(content: str, kind: str = "task_result", tags=None,
                       source: str = "", entity_id: str = "") -> bool:
    """器官产出旁路写一条记忆：总闸开着就起 daemon 线程落库，函数体异常全吞。

    返回是否已启动写入线程——调用方不得据返回值分支业务（fail-open：False 只代表
    关闸/空内容/起线程失败，不代表记忆必须存在）。
    """
    try:
        text = str(content or "").strip()[:_MAX_CONTENT]
        if not text or not _organ_memory_enabled():
            return False
        threading.Thread(target=_insert_worker, name="moray-organ-memory", daemon=True,
                         args=(text, str(kind or "task_result"), list(tags or []),
                               str(source or ""), str(entity_id or ""))).start()
        return True
    except Exception as e:  # noqa: BLE001 - 钩子启动失败绝不影响器官收尾
        logger.warning("[organ-memory] 启动失败：source=%s entity=%s err=%s: %s",
                       source, entity_id, type(e).__name__, str(e)[:200])
        return False
