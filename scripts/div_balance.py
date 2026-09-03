# -*- coding: utf-8 -*-
import io
import re

for label, path in [('before', r'D:\ai工具台\backup\pre_inputfix_20260830_110555\moray-workbench.html'),
                    ('after', r'D:\ai工具台\moray-workbench.html')]:
    with io.open(path, encoding='utf-8') as f:
        html = re.sub(r'<!--.*?-->', '', f.read(), flags=re.S)
    o = len(re.findall(r'<div\b', html))
    c = len(re.findall(r'</div>', html))
    print('%s: div open=%d close=%d balance=%d' % (label, o, c, o - c))
