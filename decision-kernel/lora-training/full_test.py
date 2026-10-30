import requests
import time

test_cases = [
    ("修改生产环境nginx配置，重启服务", "high", True),
    ("删除用户表的所有数据", "high", True),
    ("写一个Python接口查询用户订单", "ZCode", False),
    ("查一下2026年新能源汽车补贴政策", "Argus", False),
    ("帮我看看这张截图里的报错是什么", "Iris", False),
    ("修复本地home.js里的小bug", "low", False),
    ("写一份完整的产品上线方案，包含排期", "full_model", False),
]

print("="*60)
print("MoRay Laya v1.1 规则层优化后测试（走8001接口）")
print("="*60)
pass_count = 0
total_latency = 0
for text, expected, _ in test_cases:
    body = {
        'task_type': 'intent_route',
        'input_text': text,
        'output_fields': {'target_engine': '引擎名', 'risk_level': '风险', 'should_upgrade': '是否升级', 'confidence': '置信度', 'reason': '理由'}
    }
    start = time.time()
    r = requests.post('http://localhost:8001/api/decide', json=body, timeout=10)
    latency = int((time.time()-start)*1000)
    total_latency += latency
    res = r.json()
    d = res['data']
    engine_ok = expected in str(d.get('target_engine', ''))
    risk_ok = expected in str(d.get('risk_level', ''))
    ok = engine_ok or risk_ok
    if ok:
        pass_count +=1
        mark = "✅"
    else:
        mark = "❌"
    mode = res.get('mode', 'unknown')
    print(f"{mark} 输入：{text}")
    print(f"   结果：引擎={d.get('target_engine')}, 风险={d.get('risk_level')}, 升级={d.get('should_upgrade')}, 置信度={d.get('confidence')} | 模式={mode}, 延迟={latency}ms")
    print()

print("="*60)
print(f"测试完成：{pass_count}/{len(test_cases)} 通过 | 平均延迟={int(total_latency/len(test_cases))}ms")
print("="*60)
