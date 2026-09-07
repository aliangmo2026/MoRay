# -*- coding: utf-8 -*-
import io
hits = []
for l in io.open('work/netstat.txt', encoding='gbk', errors='replace'):
    if '8010' in l or '8011' in l:
        hits.append(l.strip())
print('port_hits:', len(hits))
for h in hits[:10]:
    print(h)
