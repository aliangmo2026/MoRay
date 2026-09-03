# -*- coding: utf-8 -*-
"""核对 web/ 导出内容与本地/线上 manifest 差异。"""
import io
import os

checks = []
with io.open(r'D:\ai工具台\web\index.html', encoding='utf-8') as f:
    idx = f.read()
checks.append(('web/index.html 存在且为成品', len(idx) > 500000))
checks.append(('web/index.html 含应用层脚本', 'MoRay v2.0 应用层' in idx))

with io.open(r'D:\ai工具台\web\manifest.webmanifest', encoding='utf-8') as f:
    wm = f.read()
checks.append(('线上 manifest start_url=./', '"start_url": "./"' in wm))
with io.open(r'D:\ai工具台\manifest.webmanifest', encoding='utf-8') as f:
    lm = f.read()
checks.append(('本地 manifest start_url=./moray-workbench.html', '"start_url": "./moray-workbench.html"' in lm))

with io.open(r'D:\ai工具台\web\deploy\worker.js', encoding='utf-8') as f:
    w = f.read()
checks.append(('worker.js 为原样透传代理（无依赖）', 'api.deepseek.com' in w and 'import' not in w.split('export default')[0].replace('// MoRay 通用 AI 接口透传代理（BYOK：不存储/不记录任何 Key）', '') and 'SSE' in w))
checks.append(('worker.js 不含任何内置 Key', 'sk-' not in w and 'Bearer' not in w))
with io.open(r'D:\ai工具台\deploy\worker.js', encoding='utf-8') as f:
    checks.append(('web/deploy 与工程 deploy 一致', f.read() == w))
with io.open(r'D:\ai工具台\web\deploy\README.md', encoding='utf-8') as f:
    rd = f.read()
checks.append(('README 含五步部署与 Key 规则', 'Workers' in rd and 'api.deepseek.com' in rd and '不存储' in rd))
checks.append(('vendor 五库齐全', all(os.path.exists(os.path.join(r'D:\ai工具台\web\vendor', v)) for v in ['highlight.min.js', 'lucide.min.js', 'marked.min.js', 'purify.min.js', 'tailwind-browser-4.js'])))
checks.append(('sw.js 存在', os.path.exists(r'D:\ai工具台\web\sw.js')))

for name, ok in checks:
    print(('PASS ' if ok else 'FAIL ') + name)
print('ALL PASS' if all(ok for _, ok in checks) else 'SOME FAIL')
