# -*- coding: utf-8 -*-
import io

with io.open(r'D:\ai工具台\moray-workbench.html', encoding='utf-8') as f:
    lines = f.read().split('\n')

targets = [106, 118, 122, 127, 130, 133, 322, 379, 413, 589, 672, 1101, 1107, 1142, 1205, 1329, 1364, 1421, 1462, 1517, 1679, 1725, 1735, 1768, 1772, 1825, 1882, 1967, 2254, 2423]
for t in targets:
    # 向前找最近的选择器行（含 { 的上一行）
    sel = ''
    for i in range(t - 1, max(0, t - 12), -1):
        if '{' in lines[i - 1]:
            sel = lines[i - 1].strip()[:60]
            break
    print('%d: [%s] %s' % (t, sel, lines[t - 1].strip()[:85]))
