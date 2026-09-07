# -*- coding: utf-8 -*-
"""阶段1.6 备份 + 全局排查启动通知残留源（含静态骨架）"""
import io
import os
import shutil
import time

ROOT = r'D:\ai工具台'
ts = time.strftime('%Y%m%d_%H%M%S')
dest = os.path.join(ROOT, 'backup', 'pre_stage16_' + ts)
EXCLUDE_DIRS = {'backup', '.venv', '__pycache__', '.git', 'node_modules', 'venv'}


def ignore_fn(directory, entries):
    return [e for e in entries if e in EXCLUDE_DIRS or e.endswith(('.pyc', '.pyo'))]


shutil.copytree(ROOT, dest, ignore=ignore_fn, dirs_exist_ok=False)
print('BACKUP_OK dest=%s files=%d' % (dest, sum(len(f) for _, _, f in os.walk(dest))))

# 排查：全部 parts + 产物 html 静态区的启动类通知
targets = [
    ('按 ⌘/Ctrl+K', '⌘K 引导'),
    ('已就绪', '已就绪'),
    ('一切就绪', '一切就绪'),
]
for path in ['parts/110_polish.js', 'parts/135_ui_polish.js', 'parts/70_models_settings_boot.js',
             'parts/95_features.js', 'moray-workbench.html']:
    lines = io.open(path, encoding='utf-8-sig', errors='replace').readlines()
    for i, l in enumerate(lines):
        for kw, label in targets:
            if kw in l and ('showNotification' in l or 'title' in l or 'notification' in l.lower() or 'toast' in l.lower()):
                print('%s:%d [%s]' % (path, i + 1, label), l.strip()[:170])
