# -*- coding: utf-8 -*-
"""批次版本 bump：用法 bump_fix.py <新版本号 如 3.18.1>"""
import io
import sys

ver = sys.argv[1]
p = 'parts/110_polish.js'
src = io.open(p, encoding='utf-8-sig').read()
import re
src = re.sub(r"window\.MORAY_BUILD = '[^']+';", "window.MORAY_BUILD = '%s';" % ver, src)
io.open(p, 'w', encoding='utf-8', newline='').write(src)
p = 'parts/70_models_settings_boot.js'
src = io.open(p, encoding='utf-8-sig').read()
src = re.sub(r"const APP_VERSION = 'v[^']+';", "const APP_VERSION = 'v%s';" % ver, src)
io.open(p, 'w', encoding='utf-8', newline='').write(src)
p = 'server/app/config.py'
src = io.open(p, encoding='utf-8').read()
src = re.sub(r'BUILD = "[^"]+"', 'BUILD = "%s"' % ver, src)
io.open(p, 'w', encoding='utf-8', newline='').write(src)
print('version bumped to', ver)
