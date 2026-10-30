import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel
import json
import json_repair
import time

# 单例加载，启动时加载一次，常驻内存
class LayaDecisionClient:
    _instance = None
    metrics = {
        # JSON修复分区
        "repair_tail_only": 0,   # 只修尾部标点，安全字段合法
        "repair_body": 0,        # 修复了执行字段
        "repair_meta": 0,        # 修复了安全字段（高危，必须为0）
        # 升级触发分区
        "upgrade_triggers": {
            "low_confidence": 0,
            "high_complexity": 0,
            "high_risk": 0,
            "unknown_composite": 0
        },
        "total_requests": 0
    }
    
    def __init__(self):
        self.base_path = "./base_model"
        self.lora_path = "./lora-adapter"
        self.tokenizer = AutoTokenizer.from_pretrained(self.base_path)
        self.model = AutoModelForCausalLM.from_pretrained(
            self.base_path, torch_dtype=torch.bfloat16, device_map="cuda", trust_remote_code=True
        )
        self.model = PeftModel.from_pretrained(self.model, self.lora_path)
        self.model.eval()
        self.system_prompt = """你是 MoRay 应用层 AGI 决策内核 v1.1 的 Laya 决策层（Layer 1）。根据用户请求与权限等级，产出一条符合 DecisionResult 契约的标准决策结果。
【输出要求】只输出一个 JSON 对象，禁止 markdown 代码块与任何解释文字。"""

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def decide(self, query: str, world_state: dict) -> dict:
        start_time = time.time()
        self.metrics["total_requests"] += 1
        
        user_input = f"""用户请求：{query}
权限等级：{world_state.get('user_permission_level', 'write')}
世界状态（L0 编码结果）：{json.dumps(world_state)}"""
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_input}
        ]
        text = self.tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = self.tokenizer(text, return_tensors="pt").to("cuda")
        
        with torch.no_grad():
            out = self.model.generate(
                **inputs, max_new_tokens=2048, temperature=0.1,
                top_p=0.9, do_sample=True, pad_token_id=self.tokenizer.eos_token_id
            )
        raw = self.tokenizer.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)
        
        # 先尝试直接解析，判断是否修复了meta字段
        meta_repaired = False
        try:
            result = json_repair.loads(raw)
            # 简单判断：如果raw长度远短于预期JSON，说明修复了meta
            if len(raw) < 200:
                meta_repaired = True
        except:
            meta_repaired = True
        
        repaired = json_repair.loads(raw)
        
        # 契约校验：安全字段不合法直接抛错走人工兜底
        try:
            risk_level = repaired["quality"]["risk_level"]
            should_upgrade = repaired["upgrade"]["should_upgrade"]
            confidence = repaired["quality"]["confidence"]
            steps = repaired["output"]["step_sequence"]
            assert risk_level in ["low", "medium", "high", "critical"]
            assert isinstance(should_upgrade, bool)
            assert 0 <= confidence <= 1
            assert len(steps) > 0
        except Exception as e:
            self.metrics["repair_meta"] += 1
            raise RuntimeError(f"决策安全字段校验失败，走人工兜底: {e}")
        
        # 统计升级触发类型
        if should_upgrade:
            for t in repaired["upgrade"].get("trigger_types", []):
                if t in self.metrics["upgrade_triggers"]:
                    self.metrics["upgrade_triggers"][t] += 1
        
        # 记录修复分类
        if meta_repaired:
            self.metrics["repair_meta"] += 1
        else:
            # 简单判断：尾部修复
            if raw.endswith(",") or not raw.endswith("}"):
                self.metrics["repair_tail_only"] += 1
            else:
                self.metrics["repair_body"] += 1
        
        return repaired

# 灰度覆盖检查提醒：
# 5%流量上线前确认：
# 1. 覆盖至少2个Agent
# 2. 覆盖读/写/执行至少3种请求类型
# 3. 高风险操作占比≥10%
# 不满足则调整灰度路由规则
