# -*- coding: utf-8 -*-
import io

for p, a, b in [
    ('parts/110_polish.js', "window.MORAY_BUILD = '3.17.0';", "window.MORAY_BUILD = '3.17.1';"),
    ('server/app/config.py', 'BUILD = "3.17.0"', 'BUILD = "3.17.1"'),
    ('parts/70_models_settings_boot.js', "const APP_VERSION = 'v3.17.0';", "const APP_VERSION = 'v3.17.1';"),
]:
    src = io.open(p, encoding='utf-8-sig' if p.endswith('.js') else 'utf-8').read()
    assert a in src, p + ' missing'
    io.open(p, 'w', encoding='utf-8', newline='').write(src.replace(a, b))
    print('bumped', p)
