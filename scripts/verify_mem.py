# -*- coding: utf-8 -*-
p = r'C:\Users\莫\.zcode\cli\memories\projects\ai-c81243252ce021e1\memory\aether-workbench-project.md'
txt = open(p, encoding='utf-8').read()
print('total chars:', len(txt))
print('has 0.5 section:', 'v3.16.1 阶段0.5' in txt)
