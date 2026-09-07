# -*- coding: utf-8 -*-
import os

b = r'D:\ai工具台\backup'
hits = sorted(f for f in os.listdir(b) if 'pre_agent' in f)
print('pre_agent* entries:', hits)
print('total:', len(os.listdir(b)))
