import requests

def test(text):
    body = {
        'model': 'moray-laya-final',
        'prompt': f'任务：{text}\n请按规则输出JSON：target_engine（四选一：Argus/ZCode/Iris/full_model）、confidence（0-1）、risk_level（low/medium/high/critical）、should_upgrade（true/false）、reason（20字内）',
        'stream': False,
        'options': {'temperature': 0.1, 'num_predict': 200}
    }
    r = requests.post('http://localhost:11434/api/generate', json=body, timeout=10)
    print(f'输入：{text}')
    print(f'输出：{r.json()["response"]}')
    print()

test('帮我查一下最新的半导体行业政策')
test('帮我写一个Python脚本批量重命名文件')
test('修改生产环境的数据库配置')
