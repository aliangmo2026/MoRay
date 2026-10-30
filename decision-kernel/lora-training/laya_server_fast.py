from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn
import httpx
import time

app = FastAPI(title="MoRay Laya高速决策层")

class DecideRequest(BaseModel):
    query: str
    world_state: dict = {}

@app.post("/api/decide")
async def decide(req: DecideRequest):
    start = time.time()
    prompt = f"""用户请求：{req.query}
权限等级：{req.world_state.get('user_permission_level', 'write')}
世界状态：{req.world_state}"""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "http://localhost:11434/api/generate",
            json={
                "model": "moray-laya-fast",
                "prompt": prompt,
                "stream": False,
                "options": {"num_predict": 512, "temperature": 0.1}
            }
        )
        data = resp.json()
    latency = int((time.time() - start) * 1000)
    return {
        "code": 0,
        "data": {
            "raw_output": data["response"],
            "latency_ms": latency,
            "speed_token_per_s": round(data["eval_count"] / (data["eval_duration"] / 1e9), 1)
        }
    }

@app.get("/api/metrics")
def metrics():
    return {"status": "running", "speed_target": "150+ token/s", "version": "v1.1-fast"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8002)
