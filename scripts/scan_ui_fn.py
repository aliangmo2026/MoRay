# -*- coding: utf-8 -*-
"""定位 showModal/showConfirm/on/refreshIcons 定义与 boot 调用点"""
import io

targets = {
    'parts/30_chat.js': ['function showModal', 'function showConfirm', 'function showNotification', 'function on(', 'window.on ='],
    'parts/110_polish.js': ['function showModal', 'function showConfirm', 'function on('],
    'parts/70_models_settings_boot.js': ['installThinkModeBtn', 'appendToolsSettings', 'appendGatewaySettings', 'async function boot', 'function boot'],
    'parts/85_robust.js': ['function showModal', 'function showConfirm'],
    'parts/95_features.js': ['function showModal', 'function showConfirm'],
    'parts/80_enhance.js': ['function showModal', 'function showConfirm'],
}
for path, kws in targets.items():
    lines = io.open(path, encoding='utf-8-sig', errors='replace').readlines()
    for i, l in enumerate(lines):
        for k in kws:
            if k in l:
                print('%s:%d: %s' % (path, i + 1, l.rstrip()[:140]))
