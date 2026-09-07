# -*- coding: utf-8 -*-
import os

b = r'D:\ai工具台\backup'
pre = [f for f in os.listdir(b) if f.startswith('pre_agent')]
print('pre_agent backups:', pre)
# 全部 backup 条目数
print('total backup entries:', len(os.listdir(b)))
