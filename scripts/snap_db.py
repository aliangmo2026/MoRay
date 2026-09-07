# -*- coding: utf-8 -*-
import os
import time

p = r'server\data\moray.sqlite3'
print('real db exists:', os.path.exists(p))
if os.path.exists(p):
    st = os.stat(p)
    print('before: size=%d mtime=%s' % (st.st_size, time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(st.st_mtime))))
else:
    print('before: no real db file')
