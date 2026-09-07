# -*- coding: utf-8 -*-
"""tabbit 批4：新会话'你好'简短回复(真机) + 指纹内容哈希变化"""
import subprocess

CLI = r"C:\Users\莫\AppData\Local\Tabbit\LocalAgent\bin\tabbit-cli.exe"
js = r"""
await page.goto('http://127.0.0.1:8000/', {waitUntil: 'domcontentloaded'});
await page.waitForTimeout(5500);
const skip = page.getByRole('button', {name: '跳过引导'});
if (await skip.count()) await skip.click();
await page.waitForTimeout(600);
// 配置真机 Ollama + 新会话发"你好"
const cfg = await page.evaluate(async () => {
  await MoraySettings.set({ ollamaURL: 'http://localhost:11434', openaiEnabled: false, defaultModel: 'qwen2.5:7b', routingEnabled: false, toolsEnabled: false, nativeToolsEnabled: false });
  await AI.detectBackend();
  createNewConversation();
  const ta = document.getElementById('chatInputNormal');
  ta.value = '你好';
  ta.dispatchEvent(new Event('input', { bubbles: true }));
  setTimeout(() => sendUserMessage(), 150);
  return { backend: AI.backend };
});
await page.waitForTimeout(20000);
const greet = await page.evaluate(() => {
  const msgs = AppState.messages.filter(m => m.role === 'assistant');
  const last = msgs[msgs.length - 1];
  return { replied: !!last, len: last ? last.content.length : 0, text: last ? last.content.slice(0, 180) : '' };
});
// #8 指纹内容哈希（后端直调写文件，模拟内容变化）
const fp = await page.evaluate(async () => {
  const def = { name: 'write_file' };
  const post = (path, content) => fetch('/api/agent/tool', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: 'write_file', args: { path, content, overwrite: true }, approved: true }) }).then(r => r.json());
  await post('fp_t.txt', '内容A');
  const fp1 = await agentFingerprintForApproval(def, { path: 'fp_t.txt', content: '内容A' });
  await post('fp_t.txt', '内容B');
  const fp2 = await agentFingerprintForApproval(def, { path: 'fp_t.txt', content: '内容A' });
  await post('fp_t.txt', '内容A');
  const fp3 = await agentFingerprintForApproval(def, { path: 'fp_t.txt', content: '内容A' });
  const readDef = { name: 'read_file' };
  const rfp = await agentFingerprintForApproval(readDef, { path: 'fp_t.txt' });
  return { fp1, fp2, fp3, sameParamsDiffContent: fp1 !== fp2, backToSameContentSameFp: fp1 === fp3, readNoContentHash: rfp === 'read_file::' + agentStableStringify({ path: 'fp_t.txt' }) };
});
return JSON.stringify({ cfg, greet, fp });
"""
r = subprocess.run([CLI, 'nodejs', '--task', 'moray-fix-b4'],
                   input=js, capture_output=True, text=True, timeout=180)
print('STDOUT:', r.stdout[-2600:])
print('STDERR:', r.stderr[-300:])
