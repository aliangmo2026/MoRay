# -*- coding: utf-8 -*-
import io

p = 'parts/125_tools.js'
src = io.open(p, encoding='utf-8-sig').read()
a = "if (!__wsTree || !document.getElementById('wsTreeBox')) return;"
b = "if (!__wsTree || !document.querySelector('.ws-tree-list')) return;"
if a in src:
    src = src.replace(a, b)
    io.open(p, 'w', encoding='utf-8', newline='').write(src)
    print('patched')
else:
    print('already patched or not found:', b in src)
