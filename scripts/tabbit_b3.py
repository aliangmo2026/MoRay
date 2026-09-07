# -*- coding: utf-8 -*-
"""tabbit 验证 web/index.html 演示横幅"""
import subprocess

CLI = r"C:\Users\莫\AppData\Local\Tabbit\LocalAgent\bin\tabbit-cli.exe"
js = r"""
await page.goto('http://127.0.0.1:8899/index.html', {waitUntil: 'domcontentloaded'});
await page.waitForTimeout(3500);
const banner = await page.locator('#demoModeBanner').count();
const text = banner ? (await page.locator('#demoModeBanner').innerText()).slice(0, 90) : '';
const agentBtn = await page.locator('#nativeAgentBtn').count();
return JSON.stringify({ bannerCount: banner, bannerText: text, hasNativeBtn: agentBtn > 0 });
"""
r = subprocess.run([CLI, 'nodejs', '--task', 'moray-fix-b3'],
                   input=js, capture_output=True, text=True, timeout=150)
print('STDOUT:', r.stdout[-1500:])
print('STDERR:', r.stderr[-300:])
