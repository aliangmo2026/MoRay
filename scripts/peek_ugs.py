# -*- coding: utf-8 -*-
import io

lines = io.open('parts/30_chat.js', encoding='utf-8-sig', errors='replace').readlines()
for i, l in enumerate(lines):
    if 'function updateGenStatus' in l:
        for j in range(i, min(i + 26, len(lines))):
            print(j + 1, lines[j].rstrip()[:150])
        break
else:
    print('updateGenStatus not in 30_chat')
    # 全 parts 搜
    import glob
    for p in glob.glob('parts/*.js'):
        ls = io.open(p, encoding='utf-8-sig', errors='replace').readlines()
        for k, l in enumerate(ls):
            if 'function updateGenStatus' in l:
                print('==', p, k + 1)
                for j in range(k, min(k + 26, len(ls))):
                    print(j + 1, ls[j].rstrip()[:150])
                break
