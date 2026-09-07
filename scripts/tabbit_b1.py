# -*- coding: utf-8 -*-
"""tabbit 单帧调用：bootstrap + 打开 8899 离线页并验证 native 开关置灰"""
import json
import subprocess

CLI = r"C:\Users\莫\AppData\Local\Tabbit\LocalAgent\bin\tabbit-cli.exe"
frame = {
    "op": "bootstrap",
    "taskName": "moray-fix-b1",
    "requestId": "b1-01",
    "code": """
await page.goto('http://127.0.0.1:8899/moray-workbench.html', {waitUntil: 'domcontentloaded'});
await page.waitForTimeout(5000);
const skip = page.getByRole('button', {name: '跳过引导'});
if (await skip.count()) await skip.click();
await page.waitForTimeout(600);
const res = await page.evaluate(async () => {
  const btn = document.getElementById('nativeAgentBtn');
  const out = {
    connected: !!(window.MorayBackend && window.MorayBackend.connected),
    offlineClass: btn ? btn.classList.contains('native-offline') : null,
    title: btn ? btn.title : null,
    nativeOn: MoraySettings.get('nativeToolsEnabled')
  };
  // 点击开关 → 应被守卫拦下（nativeToolsEnabled 不变）
  window.__clickedNotices = [];
  const orig = window.showNotification;
  window.showNotification = function (t, m) { window.__clickedNotices.push(String(t)); return orig.apply(this, arguments); };
  if (btn) btn.click();
  await new Promise(r => setTimeout(r, 700));
  out.afterClick = { nativeOn: MoraySettings.get('nativeToolsEnabled'), notices: window.__clickedNotices.slice(-2) };
  return out;
});
return { verified: true, result: res };
"""
}
r = subprocess.run([CLI, 'nodejs', '--task', 'moray-fix-b1'],
                   input=json.dumps(frame), capture_output=True, text=True, timeout=120)
print('STDOUT:', r.stdout[-2500:])
print('STDERR:', r.stderr[-800:])
