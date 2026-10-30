"""
MoRay L4 编排层 - Laya高速决策引擎客户端
一次编写，全系统复用，内置超时降级 + 事件埋点
"""
import requests
import time
import logging

logger = logging.getLogger("moray.laya")

LAYA_API = "http://localhost:8001/api/decide"
TIMEOUT = 2  # 超过2秒直接降级，不阻塞主链路
CONFIDENCE_THRESHOLD = 0.7  # 置信度低于0.7直接走全量模型

def laya_intent_detect(user_input: str) -> dict | None:
    """
    调用Laya做意图识别与引擎路由
    成功返回路由结果，失败/低置信度返回None触发自动降级
    """
    payload = {
        "task_type": "intent_route",
        "input_text": user_input,
        "output_fields": {
            "intent": "用户意图分类",
            "target_engine": "目标引擎：Argus/ZCode/Iris/full_model",
            "confidence": "置信度0.00-1.00",
            "reason": "简短判定理由，不超过20字"
        }
    }
    start_time = time.time()
    try:
        resp = requests.post(LAYA_API, json=payload, timeout=TIMEOUT)
        resp.raise_for_status()
        result = resp.json()
        if result.get("code") == 0 and result.get("data"):
            data = result["data"]
            latency = round((time.time() - start_time) * 1000)
            conf = float(data.get("confidence", 0))
            # 埋点记录
            logger.warning("[laya_decision] input=%s, intent=%s, engine=%s, conf=%s, latency=%sms, status=success",
                user_input[:50], data.get("intent"), data.get("target_engine"), conf, latency)
            # 置信度达标才路由
            if conf >= CONFIDENCE_THRESHOLD:
                return data
            else:
                logger.warning("[laya_decision] low confidence=%s, fallback to full model", conf)
                return None
        return None
    except Exception as e:
        latency = round((time.time() - start_time) * 1000)
        logger.warning("[laya_decision] error=%s, latency=%sms, status=fallback", str(e), latency)
        return None
