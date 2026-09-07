# -*- coding: utf-8 -*-
import os
import shutil

m = [
    ('work/shots/agent-states/s1_plan_card.png', 'docs/screenshots/agent-plan-card.png'),
    ('work/shots/agent-states/s3_approval_diff.png', 'docs/screenshots/agent-approval-diff.png'),
    ('work/shots/agent-states/s5_tree_highlight.png', 'docs/screenshots/agent-file-tree.png'),
]
for s, d in m:
    if os.path.exists(s):
        shutil.copyfile(s, d)
        print('copied', d)
    else:
        print('MISSING', s)
