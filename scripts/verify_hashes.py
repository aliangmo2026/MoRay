# -*- coding: utf-8 -*-
"""核对 worker.js 同步与其余 web 产物哈希。"""
import hashlib
import io


def h(p):
    with io.open(p, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()[:12]


same = h(r'D:\ai工具台\deploy\worker.js') == h(r'D:\ai工具台\web\deploy\worker.js')
print('deploy/worker.js      ', h(r'D:\ai工具台\deploy\worker.js'))
print('web/deploy/worker.js  ', h(r'D:\ai工具台\web\deploy\worker.js'))
print('deploy 两份一致:', same)
print('--- 其余产物哈希（供确认未变）---')
for rel in [r'web\index.html', r'web\moray-workbench.html', r'web\sw.js', r'web\manifest.webmanifest']:
    print('%-24s %s' % (rel.split('\\')[-1], h(r'D:\ai工具台' + '\\' + rel)))
# worker.js 中不再有错误拼接模式
with io.open(r'D:\ai工具台\web\deploy\worker.js', encoding='utf-8') as f:
    w = f.read()
print("web/deploy/worker.js 含 m[1]/m[2]:", 'm[1]' in w and 'm[2]' in w)
print("web/deploy/worker.js 含错误 '+ m +' 模式:", "' + m + '" in w)
