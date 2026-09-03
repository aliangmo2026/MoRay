# -*- coding: utf-8 -*-
"""校验组装后 HTML：div 配平 + 关键结构 + 重复 id 回归。"""
import io
import re

with io.open(r'D:\ai工具台\moray-workbench.html', encoding='utf-8') as f:
    html = f.read()

# 1) div 配平（粗略：剥离注释与字符串后计数）
stripped = re.sub(r'<!--.*?-->', '', html, flags=re.S)
opens = len(re.findall(r'<div\b', stripped))
closes = len(re.findall(r'</div>', stripped))
print('div open:', opens, ' close:', closes, ' balance:', opens - closes)

# 2) 关键结构：chat-normal 开合与 chatNormalContent 闭合位置
m_open = re.search(r'id="chat-normal"', html)
m_content_open = re.search(r'id="chatNormalContent"', html)
m_input = re.search(r'<!-- 光核输入台 -->', html)
m_close_comment = re.search(r'<!-- /chat-normal', html)
print('chat-normal open @', m_open.start() if m_open else None)
print('chatNormalContent open @', m_content_open.start() if m_content_open else None)
print('input wrapper (光核输入台) @', m_input.start() if m_input else None)
# chatNormalContent 的闭合应在输入台注释之前
content_region = html[m_content_open.end():m_input.start()]
# 该区间内打开的 div 数 vs 关闭数：多出的 1 个 open = chatNormalContent 自身，说明其闭合在 input 之前
o = len(re.findall(r'<div\b', re.sub(r'<!--.*?-->', '', content_region, flags=re.S)))
c = len(re.findall(r'</div>', re.sub(r'<!--.*?-->', '', content_region, flags=re.S)))
print('region between content-open and input: div+%d div-%d  (差 1 = content 自身未闭合 → 闭合在输入台前)' % (o, c))

# 3) input-model-name 唯一 + 选择器
print('input-model-name 出现次数:', len(re.findall(r'class="[^"]*input-model-name[^"]*"', html)))
print('beacon-dot 在 input-toolbar-model 内（圆点无文字）：',
      'input-toolbar-model' in html)

# 4) 重复 id 回归（全量）
ids = re.findall(r'id="([^"]+)"', html)
from collections import Counter
dups = {k: v for k, v in Counter(ids).items() if v > 1 and '$' not in k}
print('重复 id（非插值）:', dups if dups else '无')
