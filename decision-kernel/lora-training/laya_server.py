from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn
import time

# 导入我们写好的决策客户端
from laya_decision_client import LayaDecisionClient

app = FastAPI(title="MoRay Laya决策层服务")
laya = None

# 启动时加载模型
@app.on_event("startup")
def startup():
    global laya
    print("正在加载Laya决策模型...")
    laya = LayaDecisionClient.get_instance()
    print("模型加载完成，服务启动在 http://localhost:8000")

class DecideRequest(BaseModel):
    query: str
    world_state: dict = {}

@app.post("/api/decide")
async def decide(req: DecideRequest):
    start = time.time()
    try:
        result = await laya.decide(req.query, req.world_state)
        latency = int((time.time() - start) * 1000)
        result["latency_ms"] = latency
        return {"code": 0, "data": result}
    except Exception as e:
        return {"code": 500, "msg": f"决策失败，走人工兜底: {str(e)}"}

@app.get("/api/metrics")
def get_metrics():
    from laya_decision_client import LayaDecisionClient
    m = LayaDecisionClient.metrics
    total = m["total_requests"] if m["total_requests"] > 0 else 1
    return {
        "总请求数": m["total_requests"],
        "JSON修复统计": {
            "尾部标点修复": m["repair_tail_only"],
            "执行字段修复": m["repair_body"],
            "安全字段修复(应为0)": m["repair_meta"]
        },
        "升级触发统计": {
            "总升级率": sum(m["upgrade_triggers"].values()) / total,
            "分类型": m["upgrade_triggers"]
        }
    }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)
