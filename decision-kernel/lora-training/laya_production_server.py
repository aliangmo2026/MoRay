from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn
import httpx
import json
import json_repair

app = FastAPI(title="MoRay 高速决策引擎v1.1")

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL_NAME = "moray-laya-final"

BASE_SYSTEM_PROMPT = """角色：MoRay 高速决策引擎
职责：对输入任务进行快速分类、判定与路由选择，输出结构化结果
硬性规则：
1. 仅输出合法JSON字符串，禁止任何解释、说明、前缀、后缀
2. 严格按照指定字段输出，不新增、不遗漏字段
3. 置信度取值范围 0~1，保留2位小数
4. 输出尽可能简短，只给判定结果"""

class RouteRequest(BaseModel):
    task_content: str

class RiskRequest(BaseModel):
    event_content: str

@app.post("/api/route")
async def task_route(req: RouteRequest):
    """任务路由判定：fast走高速模型，full走全量大模型"""
    prompt = f"""任务内容：{req.task_content}
请判定该任务所属类型与执行路由：
- route：fast（走高速决策模型）/ full（走全量大模型）
- confidence：判定置信度
- reason：简短判定理由（不超过20字）
- task_type：分类标签（decision决策/generation生成/analysis分析/code代码）
仅输出JSON。"""
    async with httpx.AsyncClient(timeout=5) as client:
        resp = await client.post(OLLAMA_URL, json={
            "model": MODEL_NAME,
            "system": BASE_SYSTEM_PROMPT,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": 200}
        })
        raw = resp.json()["response"]
        try:
            result = json_repair.loads(raw)
            return {"code": 0, "data": result}
        except:
            return {"code": 500, "fallback": True, "msg": "解析失败，降级全量模型"}

@app.post("/api/risk")
async def risk_detect(req: RiskRequest):
    """Argus异动风险判定"""
    prompt = f"""监控事件：{req.event_content}
请判定该事件的风险等级：
- risk_level：低风险/中风险/高风险
- confidence：判定置信度
- need_check：是否需要人工复核（true/false）
- remark：简短判定说明（不超过30字）
仅输出JSON。"""
    async with httpx.AsyncClient(timeout=5) as client:
        resp = await client.post(OLLAMA_URL, json={
            "model": MODEL_NAME,
            "system": BASE_SYSTEM_PROMPT,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": 200}
        })
        raw = resp.json()["response"]
        try:
            result = json_repair.loads(raw)
            return {"code": 0, "data": result}
        except:
            return {"code": 500, "fallback": True, "msg": "解析失败，降级全量模型"}

@app.get("/health")
def health():
    return {"status": "running", "speed": "160+ token/s", "model": MODEL_NAME}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8003, log_level="error", access_log=False)
