# -*- coding: utf-8 -*-
"""读 server/.env 的 base_url 与 model（不打印 key）"""
env_path = r'D:\ai工具台\server\.env'
for line in open(env_path, encoding='utf-8', errors='replace'):
    s = line.strip()
    if s.startswith('MORAY_LLM_BASE_URL='):
        print('BASE_URL =', s.split('=', 1)[1].strip())
    if s.startswith('MORAY_LLM_MODEL='):
        print('MODEL    =', s.split('=', 1)[1].strip())
