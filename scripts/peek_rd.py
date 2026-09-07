# -*- coding: utf-8 -*-
import io

lines = io.open('README.md', encoding='utf-8').readlines()
for i, l in enumerate(lines):
    if ('在线部署' in l) or ('Cloudflare Pages' in l):
        print(i + 1, l.rstrip()[:130])
