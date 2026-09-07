# -*- coding: utf-8 -*-
import io

p = 'parts/125_tools.js'
lines = io.open(p, encoding='utf-8-sig', errors='replace').readlines()
# 定位 ensureWsSidebar 函数体内的关键行（0-based）
i_def = next(i for i, l in enumerate(lines) if 'function ensureWsSidebar' in l)
i_id = next(i for i in range(i_def, i_def + 10) if lines[i].strip() == "bar.id = 'wsSidebar';")
i_collapse = next(i for i in range(i_id, i_id + 14) if "data-ws-sidebar-collapse" in lines[i] and 'addEventListener' in lines[i])

# 重写 L(i_def+3 ~ i_collapse)：innerHTML 结构 + 拖拽
new_block = '''  bar.innerHTML = '<div class="ws-resize-handle" title="拖拽调整宽度"></div>' +
    '<div class="flex items-center gap-1.5 mb-1.5">' +
    '<i data-lucide="folder-tree" class="w-3.5 h-3.5 text-brand-violet"></i>' +
    '<span class="text-[11px] font-medium text-text-primary">工作区</span>' +
    '<span class="text-[9px] text-text-tertiary">只读</span>' +
    '<button data-ws-sidebar-refresh class="ml-auto text-text-tertiary hover:text-brand-cyan" title="刷新" aria-label="刷新文件树"><i data-lucide="refresh-cw" class="w-3 h-3"></i></button>' +
    '<button data-ws-sidebar-collapse class="text-text-tertiary hover:text-brand-cyan" title="收起侧栏（可从顶部提示条再打开）" aria-label="收起侧栏"><i data-lucide="chevrons-right" class="w-3.5 h-3.5"></i></button></div>' +
    '<div class="ws-tree-list rounded-lg border border-line-ghost/50 p-1.5 flex-1 overflow-auto text-[11px]"></div>' +
    '<div class="ws-tree-preview hidden rounded-lg border border-line-ghost/50 p-2 bg-surface-panel/40 mt-1.5 max-h-48 overflow-auto text-[10px]"></div>';
  (document.getElementById('page-chat') || document.body).appendChild(bar);
  bar.querySelector('[data-ws-sidebar-refresh]').addEventListener('click', () => { if (__wsTree) __wsTree.dirCache = {}; wsTreeRender(); });
  bar.querySelector('[data-ws-sidebar-collapse]').addEventListener('click', () => toggleWsSidebar(false));
  // [阶段1.5 F] 左缘拖拽调宽（220–460px）
  const wsHandle = bar.querySelector('.ws-resize-handle');
  if (wsHandle) {
    wsHandle.addEventListener('mousedown', (e) => {
      e.preventDefault();
      const startX = e.clientX;
      const startW = bar.getBoundingClientRect().width;
      const move = (ev) => {
        const w = Math.min(460, Math.max(220, startW + (startX - ev.clientX)));
        bar.style.width = w + 'px';
      };
      const up = () => { document.removeEventListener('mousemove', move); document.removeEventListener('mouseup', up); };
      document.addEventListener('mousemove', move);
      document.addEventListener('mouseup', up);
    });
  }'''
# 替换范围：innerHTML 行到 collapse addEventListener 行
out = lines[:i_def + 3] + [new_block + '\n'] + lines[i_collapse + 1:]
io.open(p, 'w', encoding='utf-8', newline='').write(''.join(out))
print('rewritten ensureWsSidebar block: def@%d id@%d collapse@%d' % (i_def + 1, i_id + 1, i_collapse + 1))
