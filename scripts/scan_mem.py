# -*- coding: utf-8 -*-
import os

d = r'C:\Users\莫\.zcode\cli\memories\projects\ai-c81243252ce021e1\memory'
print(os.path.isdir(d))
for f in os.listdir(d):
    print(' -', f, os.path.getsize(os.path.join(d, f)))
