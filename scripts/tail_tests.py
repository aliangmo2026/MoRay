# -*- coding: utf-8 -*-
import io

for f, label in [('work/test_74_v2.txt', '74项安全单测'), ('work/e2e_final.txt', 'e2e最终')]:
    txt = io.open(f, encoding='utf-8', errors='replace').read()
    tail = txt.strip().splitlines()[-4:]
    fails = txt.count('FAIL ') - txt.count('FAILED:')
    print('==', label, '==')
    print('PASS count:', txt.count('PASS '), '| FAIL count:', sum(1 for l in txt.splitlines() if l.startswith('FAIL')))
    for l in tail:
        print('  ', l[:150])
