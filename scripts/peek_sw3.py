# -*- coding: utf-8 -*-
import io

src = io.open('assemble.py', encoding='utf-8').read()
idx = src.find('SW_TEMPLATE')
print(src[idx - 300:idx + 900])
