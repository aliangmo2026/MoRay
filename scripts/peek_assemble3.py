# -*- coding: utf-8 -*-
import io

lines = io.open('assemble.py', encoding='utf-8').readlines()
out = []
for i, l in enumerate(lines):
    if ('assembled OK' in l) or ('构建完成' in l) or ('图标校验通过' in l) or ('stage(' in l and 'html' in l) or ('os.replace' in l) or ('替换正式文件' in l):
        out.append('%d %s' % (i + 1, l.rstrip()[:110]))
io.open('work/pas.txt', 'w', encoding='utf-8').write('\n'.join(out))
print('hits', len(out))
