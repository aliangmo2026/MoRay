# -*- coding: utf-8 -*-
import io

p = 'CHANGELOG.md'
src = io.open(p, encoding='utf-8').read()
anchor = '\n### 定版\n'
assert anchor in src, 'v1.0.0 body anchor missing'
marker = '\n### 定版\n'
fix = '\n## v1.0.0（2026-09-07）正式版封版定稿（仅版本/文档/清理，功能逻辑零改动）\n### 定版\n'
src = src.replace(anchor, fix, 1)
io.open(p, 'w', encoding='utf-8', newline='').write(src)
print('v1.0.0 heading restored:', '## v1.0.0' in src)
