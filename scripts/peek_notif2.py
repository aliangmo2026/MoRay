# -*- coding: utf-8 -*-
"""在产物 html 与全部 parts 中定位 showNotification / 通知容器定义"""
import io

html = io.open('moray-workbench.html', encoding='utf-8', errors='replace').readlines()
hits = [i for i, l in enumerate(html) if 'function showNotification' in l or "id=\"notification" in l or 'toast' in l.lower() and 'class' in l]
print('html hits:', [h + 1 for h in hits][:10])
if hits:
    i = hits[0]
    for j in range(i, min(i + 60, len(html))):
        print(j + 1, html[j].rstrip()[:160])
