# -*- coding: utf-8 -*-
import io

p = 'scripts/mock_llm_server.py'
src = io.open(p, encoding='utf-8').read()
bad = '{"title": "汇总两份素材要点", "tool": ""}]}]}},'
good = '{"title": "汇总两份素材要点", "tool": ""}]}}]},'
assert bad in src, 'bad pattern not found'
src = src.replace(bad, good)
io.open(p, 'w', encoding='utf-8', newline='').write(src)
print('fixed s10 closing brackets')
