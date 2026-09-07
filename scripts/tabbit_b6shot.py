# -*- coding: utf-8 -*-
"""tabbit 截图 360px 横幅"""
import subprocess

CLI = r"C:\Users\莫\AppData\Local\Tabbit\LocalAgent\bin\tabbit-cli.exe"
js = r"""
await page.setViewportSize({ width: 360, height: 500 });
await page.goto('http://127.0.0.1:8899/web/index.html', { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(3000);
await page.locator('#demoModeBanner').screenshot({ path: 'D:/ai工具台/work/shots/banner_360.png' });
return JSON.stringify({ saved: true });
"""
r = subprocess.run([CLI, 'nodejs', '--task', 'moray-fix-b6'],
                   input=js, capture_output=True, text=True, timeout=120)
print('STDOUT:', r.stdout[-600:])
