import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel
import json

base_model_path = "./base_model"
lora_adapter_path = "./lora-adapter"

print("正在加载基座模型（无LoRA）...")
tokenizer = AutoTokenizer.from_pretrained(base_model_path)
base_model = AutoModelForCausalLM.from_pretrained(
    base_model_path,
    torch_dtype=torch.bfloat16,
    device_map="cuda",
    trust_remote_code=True
)

print("正在加载蒸馏后LoRA模型...")
lora_model = PeftModel.from_pretrained(base_model, lora_adapter_path)
base_model.eval()
lora_model.eval()

system_prompt = """你是 MoRay 应用层 AGI 决策内核 v1.0 的 Laya 决策层（Layer 1）。根据用户请求与权限等级，产出一条符合 DecisionResult 契约的标准决策结果。
【输出要求】只输出一个 JSON 对象，禁止 markdown 代码块与任何解释文字。
【决策规则】
1. 风险校验：请求含「生产/线上」→ high；「删除/权限/配置」每命中一词风险升一级（封顶 critical）；
2. 普通2步（意图解析+执行）；高风险加user_confirm；生产加audit；
3. 步骤数≥3触发complexity升级；confidence<0.8触发low_confidence升级。
"""

test_case = {
    "query": "变更生产环境的数据库配置项 db.host",
    "permission": "write",
    "risk_tag": "critical"
}

user_input = f"""用户请求：{test_case['query']}
权限等级：{test_case['permission']}
世界状态：{{"operation_risk_tag": "{test_case['risk_tag']}"}}"""

messages = [
    {"role": "system", "content": system_prompt},
    {"role": "user", "content": user_input}
]
text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
inputs = tokenizer(text, return_tensors="pt").to("cuda")

print("\n" + "="*60)
print("【原生基座模型输出】（未蒸馏）")
print("="*60)
with torch.no_grad():
    out1 = base_model.generate(**inputs, max_new_tokens=512, temperature=0.1, pad_token_id=tokenizer.eos_token_id)
print(tokenizer.decode(out1[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True))

print("\n" + "="*60)
print("【蒸馏后LoRA模型输出】")
print("="*60)
with torch.no_grad():
    out2 = lora_model.generate(**inputs, max_new_tokens=512, temperature=0.1, pad_token_id=tokenizer.eos_token_id)
print(tokenizer.decode(out2[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True))
