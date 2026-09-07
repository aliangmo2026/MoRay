# -*- coding: utf-8 -*-
"""build_release 输出尾部确认"""
import io

lines = io.open('work/build_release_out.txt', encoding='utf-8', errors='replace').readlines()
print('total lines:', len(lines))
for l in lines[-15:]:
    print(l.rstrip()[:160])
