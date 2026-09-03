# -*- coding: utf-8 -*-
"""扫描组装后 moray-workbench.html 中重复出现的 id（静态骨架 vs 动态模板撞名排查）。"""
import io
import re
from collections import Counter

with io.open(r'D:\ai工具台\moray-workbench.html', encoding='utf-8') as f:
    html = f.read()

# 注入块边界（parts 拼接区）
marker = '/* ============================================================\n   MoRay v2.0 应用层（第二阶段）'
inject_start = html.find(marker)
inject_script = html.rfind('<script>', 0, inject_start)
inject_end = html.find('</script>', inject_start) + len('</script>')

static_part = html[:inject_script] + html[inject_end:]
parts_part = html[inject_script:inject_end]

ids = re.findall(r'id="([^"]+)"', html)
counter = Counter(ids)
dups = {k: v for k, v in counter.items() if v > 1}

print('=== 重复 id 清单（出现次数 > 1）===')
if not dups:
    print('（无重复 id）')
for k, v in sorted(dups.items()):
    # 分布：静态 vs parts
    s_cnt = len(re.findall(r'id="' + re.escape(k) + r'"', static_part))
    p_cnt = len(re.findall(r'id="' + re.escape(k) + r'"', parts_part))
    print('%-32s x%-3d  静态:%d  parts:%d' % (k, v, s_cnt, p_cnt))

# 重点：newGroupNameInput 应只剩 1 处（parts 动态模板）
print()
print('newGroupNameInput 出现次数:', counter.get('newGroupNameInput', 0))
print('groupModalOverlay 残留:', 'groupModalOverlay' in html)
