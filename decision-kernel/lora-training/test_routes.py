import requests

cases = [
    ('帮我查一下最近半导体行业的动态', 'Argus'),
    ('帮我写一个Python脚本批量重命名文件夹里的图片', 'ZCode'),
    ('帮我看看这张截图里的报错是什么意思', 'Iris'),
    ('帮我写一份完整的项目技术方案，包含架构设计和3个月排期', 'full_model'),
]

for text, expected in cases:
    body = {
        'task_type': 'intent_route',
        'input_text': text,
        'output_fields': {
            'intent': '用户意图分类',
            'target_engine': '目标引擎：Argus/ZCode/Iris/full_model',
            'confidence': '置信度0.00-1.00',
            'reason': '简短判定理由'
        }
    }
    r = requests.post('http://localhost:8001/api/decide', json=body, timeout=5)
    res = r.json()
    if res['code'] == 0:
        d = res['data']
        actual = d['target_engine']
        conf = str(d['confidence'])
        reason = d['reason']
        ok = '✅' if expected in actual else '❌'
        print(f'{ok} 输入：{text}')
        print(f'   预期：{expected} | 实际：{actual} | 置信度：{conf} | 理由：{reason}')
    else:
        print(f'❌ 接口错误：{res}')
    print()
