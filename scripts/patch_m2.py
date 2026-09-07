# -*- coding: utf-8 -*-
import io

p = 'scripts/test_agent_tools.py'
src = io.open(p, encoding='utf-8').read()
a = 'check("M2-S1 search 命中 a.txt", sc == 200 and sj["ok"] and any(h["file"] == "a.txt" and h["line"] == 1 for h in s_hits), s_hits)'
b = 'check("M2-S1 search 命中 m2_a.txt", sc == 200 and sj["ok"] and any(h["file"] == "m2_a.txt" and h["line"] == 1 for h in s_hits), s_hits)'
c = 'check("M2-S2 正则模式命中 a+b", sc == 200 and sj["ok"] and {"a.txt", "b.txt"} <= s_files, s_files)'
d = 'check("M2-S2 正则模式命中 a+b", sc == 200 and sj["ok"] and {"m2_a.txt", "m2_b.txt"} <= s_files, s_files)'
assert a in src, 'S1 pattern not found'
assert c in src, 'S2 pattern not found'
src = src.replace(a, b).replace(c, d)
io.open(p, 'w', encoding='utf-8').write(src)
print('patched ok')
