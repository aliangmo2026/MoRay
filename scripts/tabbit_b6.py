# -*- coding: utf-8 -*-
"""tabbit #3：360px 宽横幅单行不折行 + 1440px 对照"""
import subprocess

CLI = r"C:\Users\莫\AppData\Local\Tabbit\LocalAgent\bin\tabbit-cli.exe"
js = r"""
async function shotBanner(w, out) {
  await page.setViewportSize({ width: w, height: 640 });
  await page.goto('http://127.0.0.1:8899/web/index.html', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(3200);
  const info = await page.evaluate(() => {
    const b = document.getElementById('demoModeBanner');
    if (!b) return null;
    const cs = getComputedStyle(b);
    return {
      w: Math.round(b.getBoundingClientRect().width),
      h: Math.round(b.getBoundingClientRect().height),
      nowrap: cs.whiteSpace === 'nowrap',
      ellipsis: cs.textOverflow === 'ellipsis',
      scrollW: b.scrollWidth, clientW: b.clientWidth,
      singleLine: b.getBoundingClientRect().height <= 28
    };
  });
  return { width: w, info };
}
const r360 = await shotBanner(360, null);
const r1440 = await shotBanner(1440, null);
return JSON.stringify({ r360, r1440 });
"""
r = subprocess.run([CLI, 'nodejs', '--task', 'moray-fix-b6'],
                   input=js, capture_output=True, text=True, timeout=180)
print('STDOUT:', r.stdout[-1600:])
print('STDERR:', r.stderr[-300:])
