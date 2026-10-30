from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel
import torch

base_path = "./base_model"
lora_path = "./lora-adapter"
merged_path = "./merged_moray_decision"

print("加载基座模型...")
base_model = AutoModelForCausalLM.from_pretrained(
    base_path,
    torch_dtype=torch.bfloat16,
    device_map="cpu",
    trust_remote_code=True
)
tokenizer = AutoTokenizer.from_pretrained(base_path)

print("合并LoRA适配器...")
model = PeftModel.from_pretrained(base_model, lora_path)
merged_model = model.merge_and_unload()

print(f"保存合并后模型到 {merged_path}...")
merged_model.save_pretrained(merged_path, safe_serialization=True)
tokenizer.save_pretrained(merged_path)
print("合并完成！")
