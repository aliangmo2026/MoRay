# -*- coding: utf-8 -*-
import io

p = 'README.md'
src = io.open(p, encoding='utf-8').read()
a = '原生 JavaScript（18 分片组装为约 0.9MB 单文件）'
b = '原生 JavaScript（19 分片组装为单文件）'
assert a in src, 'tech stack line not found'
src = src.replace(a, b)
io.open(p, 'w', encoding='utf-8', newline='').write(src)
print('tech stack updated:', b in src)
