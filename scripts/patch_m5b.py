# -*- coding: utf-8 -*-
import io

p = 'parts/125_tools.js'
src = io.open(p, encoding='utf-8-sig').read()
anchor = """    '<div class="flex items-center justify-between"><span class="text-text-secondary">已信任操作（本会话，相同工具+参数免重复确认）</span>' +
    '<div class="flex items-center gap-2"><span class="font-mono text-[10px] text-text-tertiary" id="agentTrustedCount">' + agentTrustedCount() + '</span>' +
    '<button id="agentTrustedClear" class="btn-ghost px-2.5 py-1 rounded-lg text-[10px] border border-line-ghost">清除</button></div></div>',"""
add = anchor + """
    // [阶段1 M5] Agent 系统提示：查看内置 / 自定义覆盖
    (() => {
      const override = (MoraySettings.get('agentSysPromptOverride') || '').trim();
      const effective = override || PlanTracker.SYSTEM_PROMPT;
      return '<div class="pt-2 mt-2 border-t border-line-ghost/40">' +
        '<div class="text-xs text-text-secondary mb-1.5">Agent 系统提示（多步任务先列计划、工具时机、工作区边界）</div>' +
        '<textarea id="agentSysPromptInput" class="form-input w-full font-mono" rows="6" style="font-size:10px;line-height:1.6" placeholder="留空 = 使用内置规范">' + escapeHtml(effective) + '</textarea>' +
        '<div class="flex items-center gap-2 mt-1.5">' +
        '<button id="agentSysPromptSave" class="btn-ghost px-2.5 py-1 rounded-lg text-[10px] border border-line-ghost">保存覆盖</button>' +
        '<button id="agentSysPromptReset" class="btn-ghost px-2.5 py-1 rounded-lg text-[10px] border border-line-ghost">恢复内置</button>' +
        '<span class="text-[9px] text-text-tertiary">仅在本机工具开启时追加到系统提示</span></div></div>';
    })(),"""
assert anchor in src, 'anchor not found'
src = src.replace(anchor, add)
# 事件绑定：插在 trustedClear 事件块后
ev_anchor = """    showNotification('已清除', '本会话已信任操作已清空（' + n + ' 条），之后全部重新询问', 'success', 2200);
  });"""
ev_add = ev_anchor + """
  // [阶段1 M5] Agent 系统提示保存/恢复
  const sysInput = card.querySelector('#agentSysPromptInput');
  const sysSave = card.querySelector('#agentSysPromptSave');
  const sysReset = card.querySelector('#agentSysPromptReset');
  if (sysSave) on(sysSave, 'click', async () => {
    const v = String(sysInput.value || '').trim();
    if (!v) { showNotification('不能为空', '留空请用「恢复内置」；覆盖内容不能为空', 'warning', 2600); return; }
    await MoraySettings.set('agentSysPromptOverride', v);
    showNotification('已保存', '自定义 Agent 系统提示已生效（本机工具开启时追加）', 'success', 2600);
  });
  if (sysReset) on(sysReset, 'click', async () => {
    await MoraySettings.set('agentSysPromptOverride', '');
    sysInput.value = PlanTracker.SYSTEM_PROMPT;
    showNotification('已恢复内置', 'Agent 系统提示回到内置规范', 'success', 2200);
  });"""
assert ev_anchor in src, 'event anchor not found'
src = src.replace(ev_anchor, ev_add)
io.open(p, 'w', encoding='utf-8', newline='').write(src)
print('settings card patched')
