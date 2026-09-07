# -*- coding: utf-8 -*-
import os

b = r'D:\ai工具台\backup'
print([f for f in os.listdir(b) if f.startswith('pre_agent05')])
