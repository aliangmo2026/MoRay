# -*- coding: utf-8 -*-
import io

for path in ['parts/40_compare_prompts.js', 'parts/50_snippets.js', 'parts/130_search.js']:
    lines = io.open(path, encoding='utf-8-sig', errors='replace').readlines()
    for i, l in enumerate(lines):
        t = l.rstrip()
        if ('暂无' in t or '没有找到' in t or '空' in t and 'py-' in t) and ('innerHTML' in t or 'class=' in t):
            print('%s:%d' % (path, i + 1), t[:160])
