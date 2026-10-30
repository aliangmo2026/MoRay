import torch
import time
from transformers import AutoModelForCausalLM, AutoTokenizer

model_path = "./merged_moray_decision"

print("正在bf16加载模型...")
tokenizer = AutoTokenizer.from_pretrained(model_path)
model = AutoModelForCausalLM.from_pretrained(
    model_path,
    torch_dtype=torch.bfloat16,
    device_map="cuda",
    trust_remote_code=True,
    attn_implementation="sdpa"
)
model = torch.compile(model, mode="reduce-overhead")

system_prompt = """你是 MoRay 决策内核，根据用户请求输出标准决策JSON。"""
user_input = "用户请求：变更生产环境数据库配置项db.host，权限write"

messages = [
    {"role": "system", "content": system_prompt},
    {"role": "user", "content": user_input}
]
text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
inputs = tokenizer(text, return_tensors="pt").to("cuda")

# 预热
print("预热中...")
for _ in range(2):
    with torch.no_grad():
        model.generate(**inputs, max_new_tokens=128, pad_token_id=tokenizer.eos_token_id)

# 正式测速
print("开始测速...")
times = []
for i in range(5):
    torch.cuda.synchronize()
    start = time.time()
    with torch.no_grad():
        out = model.generate(**inputs, max_new_tokens=256, pad_token_id=tokenizer.eos_token_id)
    torch.cuda.synchronize()
    elapsed = time.time() - start
    new_tokens = out.shape[1] - inputs["input_ids"].shape[1]
    tps = new_tokens / elapsed
    times.append(tps)
    print(f"第{i+1}次: {elapsed:.2f}秒, 生成{new_tokens}token, 速度: {tps:.1f} token/s")

avg_tps = sum(times)/len(times)
vram = torch.cuda.max_memory_allocated() / 1024**3
print(f"\n平均速度: {avg_tps:.1f} token/s")
print(f"峰值显存占用: {vram:.2f} GB")
print(f"生成200token决策单耗时: {200/avg_tps:.2f}秒")
