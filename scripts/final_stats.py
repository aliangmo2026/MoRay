# -*- coding: utf-8 -*-
"""最终统计：on() 调用数、产物大小与行数。"""
import io
import glob
import os

total = 0
for p in sorted(glob.glob(r'D:\ai工具台\parts\*.js')):
    with io.open(p, encoding='utf-8') as f:
        n = len(re.findall(r'\bon\(', f.read())) if (re := __import__('re')) else 0
    total += n
print('on() calls total in parts:', total)
st = os.stat(r'D:\ai工具台\moray-workbench.html')
print('html: %.0f KB' % (st.st_size / 1024))
with io.open(r'D:\ai工具台\moray-workbench.html', encoding='utf-8') as f:
    print('html lines:', f.read().count('\n'))
