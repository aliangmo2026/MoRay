# -*- coding: utf-8 -*-
"""全部 parts node --check"""
import glob
import subprocess

fails = []
for p in sorted(glob.glob('parts/*.js')):
    r = subprocess.run(['node', '--check', p], capture_output=True)
    if r.returncode != 0:
        fails.append((p, r.stderr.decode('utf-8', errors='replace')[-400:]))
    else:
        print('OK', p)
print('FAILS:', len(fails))
for p, err in fails:
    print('==', p)
    print(err)
