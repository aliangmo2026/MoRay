# -*- coding: utf-8 -*-
"""定位 showNotification 函数定义（产物 html 5147 行附近）"""
import io

html = io.open('moray-workbench.html', encoding='utf-8', errors='replace').readlines()
i = 5146
for j in range(i, min(i + 75, len(html))):
    print(j + 1, html[j].rstrip()[:170])
