# -*- coding: utf-8 -*-
import io
for l in io.open('work/netstat3.txt', encoding='gbk', errors='replace'):
    cols = l.split()
    if len(cols) >= 5 and cols[1] in ('127.0.0.1:8000', '127.0.0.1:8002', '127.0.0.1:8898', '127.0.0.1:8899', '0.0.0.0:8000'):
        print(l.strip())
