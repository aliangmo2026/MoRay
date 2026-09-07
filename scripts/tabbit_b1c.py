# -*- coding: utf-8 -*-
"""tabbit #2：8000 在线页（Ollama 后端）→ 开本机工具 → defaultModel 应切推荐 qwen2.5:7b"""
import subprocess

CLI = r"C:\Users\莫\AppData\Local\Tabbit\LocalAgent\bin\tabbit-cli.exe"
js = r"""
await page.goto('http://127.0.0.1:8000/', {waitUntil: 'domcontentloaded'});
await page.waitForTimeout(5500);
const skip = page.getByRole('button', {name: '跳过引导'});
if (await skip.count()) await skip.click();
await page.waitForTimeout(600);
const res = await page.evaluate(async () => {
  // 预置：defaultModel 设为不推荐模型 qwen3.5:4b，无自检记录
  await MoraySettings.set({ ollamaURL: 'http://localhost:11434', openaiEnabled: false, defaultModel: 'qwen3.5:4b', routingEnabled: false, toolsEnabled: true, nativeToolsEnabled: false });
  await MoraySettings.set('agentSelfCheck', null);
  await AI.detectBackend();
  const models = (AI.models || []).map(m => m.name);
  const before = { defaultModel: MoraySettings.get('defaultModel'), backend: AI.backend, models: models.slice(0, 4) };
  // 点开本机工具开关 → 应触发 agentApplyRecommendedModel
  document.getElementById('nativeAgentBtn')?.click();
  await new Promise(r => setTimeout(r, 2500));
  return {
    before,
    after: {
      nativeOn: MoraySettings.get('nativeToolsEnabled'),
      defaultModel: MoraySettings.get('defaultModel'),
      recommended: (typeof agentRecommendedModel === 'function') ? agentRecommendedModel() : 'n/a'
    }
  };
});
return JSON.stringify({ verified: true, result: res });
"""
r = subprocess.run([CLI, 'nodejs', '--task', 'moray-fix-b1'],
                   input=js, capture_output=True, text=True, timeout=150)
print('STDOUT:', r.stdout[-2400:])
print('STDERR:', r.stderr[-300:])
