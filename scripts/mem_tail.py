# -*- coding: utf-8 -*-
lines = open(r'C:\Users\莫\.zcode\cli\memories\projects\ai-c81243252ce021e1\memory\aether-workbench-project.md', encoding='utf-8').read().splitlines()
print('total lines:', len(lines))
for l in lines[-4:]:
    print(l[:250])
