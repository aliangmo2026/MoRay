/* ============================================================
   [M4b 2026-10-02] 记忆浏览器（写操作版）—— 左导航「记忆库」页
   ------------------------------------------------------------
   - 数据源：GET /api/memory/list（window.MorayMemory.list，M4a）；写操作
     POST /api/memory/forget（按 id 删单条）与 POST /api/memory/save（手动新增，
     source=manual），均为 M4b 新增的 window.MorayMemory 方法；用户主动操作，
     失败显式报错，绝不 fail-open 静默
   - 范式：仿 86_home_apps（运行时注入 nav 按钮 + page-panel；页面切换兜底依赖
     68 的 document 级收敛监听；自带 switchTo 供程序化进入，但不参与启动落点）
   - 范围（M4b 写操作版）：M4a 只读能力全保留（列表 + 详情展开 + 纯文本过滤 +
     memoryRecallEnabled 开关 + 刷新）；新增 单条删除（showConfirm danger 二次确认，
     文案含内容摘要 + id 尾号）与 手动新增（content 前端必填校验，空则不发请求）；
     不做 undo / 回收站
   - 安全：记忆内容是用户/模型写的 —— 动态数据一律 textContent，不进 innerHTML
     （静态骨架与图标标签沿用既有 innerHTML 范式，不含任何动态数据）
   - 样式：00_theme 令牌；hover/聚焦只改 1px 边框色，禁外发光
   ============================================================ */

const MorayMemoryBrowser = (() => {

  const PAGE = 'memory';
  const LIST_LIMIT = 200;   // 后端钳制上限（memory_routes.py:314 min(limit, 200)）

  /** 模块状态：loaded=true 表示已完成至少一次成功拉取；error 非空时列表区显示错误行 */
  let __state = { items: [], total: 0, loaded: false, error: '' };
  let __filter = '';
  /** 展开态跨重绘保持（键=memory id，85_engines __enginesOpen 同款思路） */
  const __openIds = new Set();

  /* ---------------- 样式（本页私有；令牌化，深浅主题自适应） ---------------- */

  function ensureStyle() {
    if (document.getElementById('morayMemoryBrowserStyle')) return;
    const el = document.createElement('style');
    el.id = 'morayMemoryBrowserStyle';
    // 中栏收敛与 135 对 #page-settings / 86 对 #page-apps 的做法同款（body:has 一行）
    el.textContent = `
body:has(#page-memory:not(.hidden)) #middleSidebar { display: none; }
.mem-toolbar { display: flex; align-items: center; gap: 8px; }
.mem-filter-input {
  flex: 1; min-width: 0; height: 32px; padding: 0 12px; font: inherit; font-size: 12px;
  color: var(--color-text-primary, #ededed);
  background: var(--color-bg-hover, rgba(255,255,255,0.04));
  border: 1px solid var(--color-border, rgba(255,255,255,0.08));
  border-radius: var(--radius-control, 6px); outline: none;
  transition: border-color 0.15s ease;
}
.mem-filter-input::placeholder { color: var(--color-text-tertiary, #666666); }
.mem-filter-input:focus { border-color: var(--color-brand, #E5734A); }
.memsw {
  position: relative; width: 36px; height: 20px; padding: 0; flex-shrink: 0;
  border-radius: 999px; cursor: pointer; font: inherit;
  border: 1px solid var(--color-border, rgba(255,255,255,0.16));
  background: var(--color-bg-hover, rgba(255,255,255,0.06));
  transition: background 0.15s ease, border-color 0.15s ease;
}
.memsw .memsw-knob {
  position: absolute; top: 2px; left: 2px; width: 14px; height: 14px; border-radius: 50%;
  background: var(--color-text-tertiary, #666666);
  transition: left 0.15s ease, background 0.15s ease;
}
.memsw.on { background: var(--color-brand-subtle, rgba(229,115,74,0.16)); border-color: var(--color-brand, #E5734A); }
.memsw.on .memsw-knob { left: 18px; background: var(--color-brand, #E5734A); }
.memsw:focus-visible { outline: none; border-color: var(--color-brand, #E5734A); }
.mem-list { display: flex; flex-direction: column; gap: 12px; }
.memcard {
  padding: 12px 16px;
  background: var(--color-bg-card, #1a1a1a);
  border: 1px solid var(--color-border, rgba(255,255,255,0.08));
  border-radius: var(--radius-card, 8px);
  cursor: default; transition: border-color 0.15s ease;
}
.memcard:hover { border-color: var(--color-brand, #E5734A); }
.memcard-top { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }
.mem-kind {
  padding: 1px 8px; border-radius: 999px; font-size: 10px; font-weight: 500; flex-shrink: 0;
  background: var(--color-brand-subtle, rgba(229,115,74,0.08));
  color: var(--color-brand, #E5734A);
}
.mem-tag {
  padding: 1px 8px; border-radius: 999px; font-size: 10px;
  background: var(--color-bg-hover, rgba(255,255,255,0.04));
  color: var(--color-text-secondary, #a3a3a3);
}
.mem-src { font-size: 10px; color: var(--color-text-tertiary, #666666); }
.mem-time { font-size: 10px; color: var(--color-text-tertiary, #666666); margin-left: auto; }
.memcard-content {
  margin-top: 2px; font-size: 13px; line-height: 1.6;
  color: var(--color-text-primary, #ededed);
  white-space: pre-wrap; word-break: break-word;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.memcard.is-open .memcard-content { display: block; -webkit-line-clamp: unset; }
.memcard-meta { margin-top: 8px; font-size: 10px; color: var(--color-text-tertiary, #666666); font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.mem-notice { font-size: 11px; color: var(--color-text-tertiary, #666666); }
.mem-empty { padding: 24px 0; text-align: center; font-size: 12px; color: var(--color-text-tertiary, #666666); }
.mem-error {
  padding: 12px 12px; font-size: 12px; line-height: 1.6;
  color: var(--color-danger, #f87171);
  border: 1px solid var(--color-danger, #f87171);
  border-radius: var(--radius-card, 8px);
}
.mem-loading { padding: 24px 0; text-align: center; font-size: 12px; color: var(--color-text-tertiary, #666666); }
.mem-del {
  display: inline-flex; align-items: center; justify-content: center;
  width: 22px; height: 22px; padding: 0; margin-left: 2px; flex-shrink: 0;
  border: 1px solid transparent; border-radius: var(--radius-control, 6px);
  background: transparent; color: var(--color-text-tertiary, #666666);
  cursor: pointer; font: inherit;
  transition: border-color 0.15s ease, color 0.15s ease, background 0.15s ease;
}
.mem-del:hover { color: var(--color-danger, #f87171); border-color: var(--color-danger, #f87171); background: rgba(248,113,113,0.08); }
.mem-del:focus-visible { outline: none; border-color: var(--color-danger, #f87171); }
/* refreshIcons 会把 <i> 换成 <svg>：尺寸两种标签都写（同根因坑，见 memory 备注） */
.mem-del svg, .mem-del i { width: 13px; height: 13px; }
#memActionError { cursor: pointer; }
.mem-slabel { display: block; margin: 12px 0 4px; font-size: 11px; color: var(--color-text-secondary, #a3a3a3); }
.mem-slabel:first-child { margin-top: 0; }
.mem-sinput, .mem-stextarea {
  width: 100%; box-sizing: border-box; padding: 8px 12px; font: inherit; font-size: 12px; line-height: 1.6;
  color: var(--color-text-primary, #ededed);
  background: var(--color-bg-hover, rgba(255,255,255,0.04));
  border: 1px solid var(--color-border, rgba(255,255,255,0.08));
  border-radius: var(--radius-control, 6px); outline: none;
  transition: border-color 0.15s ease;
}
.mem-stextarea { min-height: 84px; resize: vertical; }
.mem-sinput:focus, .mem-stextarea:focus { border-color: var(--color-brand, #E5734A); }
.mem-serr { margin-top: 8px; font-size: 11px; line-height: 1.5; color: var(--color-danger, #f87171); }
.mem-shint { margin-top: 12px; font-size: 10px; color: var(--color-text-tertiary, #666666); }
`;
    document.head.appendChild(el);
  }

  /* ---------------- 注入：导航按钮（nav 末位，68 同款 appendChild）+ 页面容器 ---------------- */

  function ensureNavButton() {
    if (document.querySelector('.nav-icon-btn[data-page="' + PAGE + '"]')) return;
    // [A-1] 插入骨架「知识」分组容器；容器缺失时回退旧的 cost 同级 appendChild
    const group = document.querySelector('.nav-group[data-nav-group="knowledge"]');
    const costBtn = document.querySelector('.nav-icon-btn[data-page="cost"]');
    const nav = group || (costBtn ? costBtn.parentElement : null);
    if (!nav) return;
    const btn = document.createElement('button');
    btn.className = 'nav-icon-btn';
    btn.setAttribute('data-page', PAGE);
    btn.setAttribute('data-tooltip', '记忆库');
    btn.innerHTML = '<i data-lucide="brain" class="w-4 h-4"></i><span class="nav-icon-label">记忆库</span>';
    nav.appendChild(btn);
    btn.addEventListener('click', () => { switchTo(PAGE); });
  }

  function ensurePage() {
    if (document.getElementById('page-' + PAGE)) return;
    const ref = document.getElementById('page-cost');
    if (!ref || !ref.parentElement) return;
    const div = document.createElement('div');
    div.id = 'page-' + PAGE;
    div.className = 'page-panel hidden flex-1 flex flex-col min-h-0';
    ref.parentElement.insertBefore(div, ref.nextSibling);
  }

  /* ---------------- 页面骨架（首次构建一次；动态数据不在此层，一律 textContent） ---------------- */

  function buildPage() {
    const page = document.getElementById('page-' + PAGE);
    if (!page || page.querySelector('#memList')) return;
    page.innerHTML = `
      <div class="h-14 px-5 flex items-center justify-between border-b border-line-ghost/50 flex-shrink-0">
        <div class="flex items-center gap-2 min-w-0">
          <i data-lucide="brain" class="w-4 h-4 text-brand-cobalt flex-shrink-0"></i>
          <span class="text-sm font-medium text-text-primary">记忆库</span>
          <span class="tag-pill bg-brand-cobalt/15 text-brand-cobalt" id="memCountPill">…</span>
          <span class="text-[11px] text-text-tertiary truncate">长期记忆 · 可看可管（M4b）</span>
        </div>
        <div class="mem-toolbar flex-shrink-0">
          <span class="text-[11px] text-text-secondary" title="开启后，发送消息时自动召回相关记忆注入 system prompt（M3b 链路，默认关）">召回总闸</span>
          <button type="button" id="memRecallSwitch" class="memsw" role="switch" aria-checked="false" title="长期记忆召回总闸：当前关"><span class="memsw-knob"></span></button>
          <button type="button" id="memAddBtn" class="btn-ghost px-3 h-8 rounded-lg text-xs flex items-center gap-2 border border-line-ghost" title="手动保存一条记忆（来源记为 manual）">
            <i data-lucide="plus" class="w-3.5 h-3.5"></i>新增记忆
          </button>
          <button type="button" id="memRefreshBtn" class="btn-ghost px-3 h-8 rounded-lg text-xs flex items-center gap-2 border border-line-ghost" title="重新拉取 GET /api/memory/list">
            <i data-lucide="refresh-cw" class="w-3.5 h-3.5"></i>刷新
          </button>
        </div>
      </div>
      <div class="flex-1 overflow-y-auto p-5">
        <div class="max-w-4xl mx-auto space-y-3">
          <div class="mem-toolbar">
            <i data-lucide="search" class="w-3.5 h-3.5 text-text-tertiary flex-shrink-0"></i>
            <input type="text" id="memFilterInput" class="mem-filter-input" autocomplete="off" spellcheck="false"
                   placeholder="过滤已加载条目（匹配内容 / 标签 / 类型 / 来源，纯文本不区分大小写）" />
          </div>
          <div id="memActionError" class="mem-error" style="display:none" title="点击隐藏"></div>
          <div id="memNotice" class="mem-notice" style="display:none"></div>
          <div id="memList" class="mem-list"></div>
        </div>
      </div>`;
  }

  /* ---------------- 事件（绑一次；列表区委托用 composedPath，避开 refreshIcons 游离节点） ---------------- */

  function bindEvents() {
    const sw = document.getElementById('memRecallSwitch');
    if (sw && !sw.__memBound) {
      sw.__memBound = true;
      sw.addEventListener('click', async () => {
        if (typeof MoraySettings === 'undefined' || typeof MoraySettings.set !== 'function') {
          if (typeof showNotification === 'function') showNotification('开关不可用', 'MoraySettings 未就绪', 'error', 3000);
          return;
        }
        const cur = MoraySettings.get('memoryRecallEnabled') === true;
        try {
          await MoraySettings.set('memoryRecallEnabled', !cur);
        } catch (e) {
          if (typeof showNotification === 'function') showNotification('开关写入失败', String((e && e.message) || e).slice(0, 120), 'error', 3000);
        }
        syncSwitch(); // 回读持久化真值刷新外观（不臆造写入成功）
        const now = MoraySettings.get('memoryRecallEnabled') === true;
        if (typeof showNotification === 'function') {
          showNotification(now ? '长期记忆召回已开启' : '长期记忆召回已关闭',
            now ? '发送消息时将自动召回相关记忆（M3b 链路）' : '发送链路不再注入记忆',
            'info', 2500);
        }
      });
    }
    const refresh = document.getElementById('memRefreshBtn');
    if (refresh && !refresh.__memBound) {
      refresh.__memBound = true;
      refresh.addEventListener('click', async () => {
        refresh.disabled = true;
        try { await loadList(true); }
        finally { refresh.disabled = false; }
      });
    }
    const add = document.getElementById('memAddBtn');
    if (add && !add.__memBound) {
      add.__memBound = true;
      add.addEventListener('click', openAddModal);
    }
    const actErr = document.getElementById('memActionError');
    if (actErr && !actErr.__memBound) {
      actErr.__memBound = true;
      actErr.addEventListener('click', () => { actErr.style.display = 'none'; }); // 可点掉
    }
    const input = document.getElementById('memFilterInput');
    if (input && !input.__memBound) {
      input.__memBound = true;
      input.addEventListener('input', () => { __filter = String(input.value || '').trim(); renderList(); });
    }
    const list = document.getElementById('memList');
    if (list && !list.__memBound) {
      list.__memBound = true;
      list.addEventListener('click', (ev) => {
        const path = typeof ev.composedPath === 'function' ? ev.composedPath() : [];
        // 删除按钮嵌套在卡片内：必须先判删除按钮，再判卡片展开，顺序不能反
        let delBtn = null;
        let card = null;
        for (let i = 0; i < path.length; i++) {
          const n = path[i];
          if (!n || n.nodeType !== 1 || !n.classList) continue;
          if (!delBtn && n.classList.contains('mem-del')) delBtn = n;
          if (!card && n.classList.contains('memcard')) card = n;
        }
        if (delBtn && card) {
          const delId = card.getAttribute('data-mem-id') || '';
          const bodyEl = card.querySelector('.memcard-content');
          requestDelete(delId, bodyEl ? bodyEl.textContent : '');
          return; // 命中删除按钮：不落入展开/收起分支
        }
        if (!card) return;
        const id = card.getAttribute('data-mem-id') || '';
        const open = !__openIds.has(id);
        if (open) __openIds.add(id); else __openIds.delete(id);
        card.classList.toggle('is-open', open);
      });
    }
  }

  /** 按持久化真值同步开关外观（回读 MoraySettings，不维护独立镜像状态） */
  function syncSwitch() {
    const sw = document.getElementById('memRecallSwitch');
    if (!sw) return;
    const on = (typeof MoraySettings !== 'undefined') && MoraySettings.get('memoryRecallEnabled') === true;
    sw.classList.toggle('on', on);
    sw.setAttribute('aria-checked', on ? 'true' : 'false');
    sw.title = '长期记忆召回总闸：当前' + (on ? '开' : '关');
  }

  /* ---------------- 数据 ---------------- */

  /** 拉取列表。失败显式落地为 __state.error（列表区红字 + 通知），绝不静默。
      @param {boolean} [force] - 占位（M4a 每次都全量重拉，无缓存层） */
  async function loadList(force) {
    const box = document.getElementById('memList');
    if (!box) return;
    if (typeof window.MorayMemory === 'undefined' || typeof window.MorayMemory.list !== 'function') {
      __state = { items: [], total: 0, loaded: false, error: 'window.MorayMemory.list 不可用（memory_client.js 未加载或版本过旧）' };
      renderList();
      if (typeof showNotification === 'function') showNotification('记忆库不可用', __state.error, 'error', 4000);
      return;
    }
    box.innerHTML = '';
    const loading = document.createElement('div');
    loading.className = 'mem-loading';
    loading.textContent = '正在读取记忆列表…';
    box.appendChild(loading);
    try {
      const r = await window.MorayMemory.list({ limit: LIST_LIMIT, offset: 0 });
      __state = { items: Array.isArray(r.items) ? r.items : [], total: (typeof r.total === 'number') ? r.total : 0, loaded: true, error: '' };
    } catch (e) {
      __state = { items: [], total: 0, loaded: false, error: String((e && e.message) || e) };
      if (typeof showNotification === 'function') showNotification('记忆列表加载失败', __state.error.slice(0, 160), 'error', 4000);
    }
    renderList();
  }

  /** 过滤谓词：纯文本不区分大小写，匹配 content / tags / kind / source */
  function matches(it) {
    if (!__filter) return true;
    const f = __filter.toLowerCase();
    return String((it && it.content) || '').toLowerCase().includes(f) ||
      ((it && it.tags) || []).join('/').toLowerCase().includes(f) ||
      String((it && it.kind) || '').toLowerCase().includes(f) ||
      String((it && it.source) || '').toLowerCase().includes(f);
  }

  /** 单条卡片：全 DOM API + textContent（记忆内容是用户/模型写的，禁 innerHTML） */
  function cardEl(it) {
    const id = String((it && it.id) || '');
    const content = String((it && it.content) || '');
    const card = document.createElement('div');
    card.className = 'memcard' + (__openIds.has(id) ? ' is-open' : '');
    card.setAttribute('data-mem-id', id);

    const top = document.createElement('div');
    top.className = 'memcard-top';
    const kind = document.createElement('span');
    kind.className = 'mem-kind';
    kind.textContent = String((it && it.kind) || 'fact');
    top.appendChild(kind);
    const tags = (it && it.tags) || [];
    for (let i = 0; i < tags.length; i++) {
      if (!tags[i]) continue;
      const tag = document.createElement('span');
      tag.className = 'mem-tag';
      tag.textContent = String(tags[i]);
      top.appendChild(tag);
    }
    const src = document.createElement('span');
    src.className = 'mem-src';
    src.textContent = '来源：' + (String((it && it.source) || '') || '-');
    top.appendChild(src);
    const time = document.createElement('span');
    time.className = 'mem-time';
    time.textContent = String((it && it.created_at) || '').replace('T', ' ');
    top.appendChild(time);
    // [M4b] 删除按钮：仅单条入口，点击走 showConfirm danger 二次确认（委托在 #memList，先判 .mem-del）
    const del = document.createElement('button');
    del.type = 'button';
    del.className = 'mem-del';
    del.setAttribute('aria-label', '删除这条记忆');
    del.title = '删除这条记忆';
    del.innerHTML = '<i data-lucide="trash-2"></i>';
    top.appendChild(del);
    card.appendChild(top);

    const body = document.createElement('div');
    body.className = 'memcard-content';
    body.textContent = content;
    card.appendChild(body);

    const meta = document.createElement('div');
    meta.className = 'memcard-meta';
    meta.textContent = 'id ' + id + (content.length > 80 ? ' · 点击正文展开/收起' : '');
    card.appendChild(meta);
    return card;
  }

  function renderList() {
    const box = document.getElementById('memList');
    if (!box) return;
    const notice = document.getElementById('memNotice');
    box.innerHTML = '';

    if (__state.error) {
      const err = document.createElement('div');
      err.className = 'mem-error';
      err.textContent = '加载失败：' + __state.error + '（点右上「刷新」重试）';
      box.appendChild(err);
      if (notice) notice.style.display = 'none';
      updateCountPill();
      return;
    }
    if (!__state.loaded) {
      const loading = document.createElement('div');
      loading.className = 'mem-loading';
      loading.textContent = '正在读取记忆列表…';
      box.appendChild(loading);
      if (notice) notice.style.display = 'none';
      updateCountPill();
      return;
    }

    const items = __state.items.filter(matches);
    for (let i = 0; i < items.length; i++) box.appendChild(cardEl(items[i]));

    if (!__state.items.length) {
      const empty = document.createElement('div');
      empty.className = 'mem-empty';
      empty.textContent = '记忆库为空（还没有任何记忆）';
      box.appendChild(empty);
    } else if (!items.length) {
      const empty = document.createElement('div');
      empty.className = 'mem-empty';
      empty.textContent = '无匹配条目（过滤词：' + __filter + '）';
      box.appendChild(empty);
    }

    if (notice) {
      if (__filter) {
        notice.textContent = '过滤命中 ' + items.length + ' / ' + __state.items.length + ' 条';
        notice.style.display = '';
      } else if (__state.total > __state.items.length) {
        notice.textContent = '仅显示最近 ' + __state.items.length + ' 条（库中共 ' + __state.total + ' 条；M4a 只读版暂不提供翻页）';
        notice.style.display = '';
      } else {
        notice.style.display = 'none';
      }
    }
    // [M4b] 卡片现含 lucide 图标（删除按钮），重绘后必须补扫一次，否则退化成 circle
    if (typeof refreshIcons === 'function') refreshIcons();
    updateCountPill();
  }

  function updateCountPill() {
    const pill = document.getElementById('memCountPill');
    if (!pill) return;
    pill.textContent = __state.error ? '…' : ('共 ' + __state.total + ' 条');
  }

  /* ---------------- 写操作（M4b：forget 按 id 删 / save 手动新增） ---------------- */

  /** 写操作失败红字（红线：失败显式报错 = #memActionError 红字 + 通知，绝不静默）。
      位于过滤行下方，点击可隐藏 */
  function showActionError(msg) {
    const box = document.getElementById('memActionError');
    if (!box) return;
    box.textContent = String(msg || '') + '（点击隐藏）';
    box.style.display = '';
  }

  /** 单条删除入口：showConfirm danger 二次确认（文案含内容摘要截 60 字 + id 尾号 8 位）。
      确认 → forget(id) → 成功清展开态、通知并重拉列表；失败写 #memActionError + 通知，
      列表保持不变（刻意不 loadList）。onOk 内部自行 catch：错误以具体文案上报，
      不再让 showConfirm 的兜底 catch 二次弹「操作失败」。 */
  function requestDelete(id, content) {
    id = String(id || '').trim();
    if (!id) return;
    const c = String(content || '');
    const summary = c.length > 60 ? c.slice(0, 60) + '…' : (c || '（无正文内容）');
    if (typeof showConfirm !== 'function') {
      showActionError('删除失败：showConfirm 组件不可用');
      if (typeof showNotification === 'function') showNotification('删除失败', 'showConfirm 组件不可用', 'error', 4000);
      return;
    }
    showConfirm('删除这条记忆？',
      '将永久删除该条记忆，不可撤销 · id 尾号 ' + id.slice(-8) + ' · 内容：「' + summary + '」',
      async () => {
        try {
          if (typeof window.MorayMemory === 'undefined' || typeof window.MorayMemory.forget !== 'function') {
            throw new Error('window.MorayMemory.forget 不可用（memory_client.js 未加载或版本过旧）');
          }
          const r = await window.MorayMemory.forget(id);
          __openIds.delete(id);
          if (typeof showNotification === 'function') {
            if (r && r.deleted > 0) showNotification('已删除', '记忆（尾号 ' + id.slice(-8) + '）已从记忆库移除', 'success', 3000);
            else showNotification('未删除', '该记忆已不存在（可能已被删除），列表已刷新', 'info', 3200);
          }
          await loadList(true);
        } catch (e) {
          const msg = String((e && e.message) || e);
          showActionError('删除失败：' + msg);
          if (typeof showNotification === 'function') showNotification('删除失败', msg.slice(0, 160), 'error', 4000);
        }
      },
      { okText: '删除', danger: true });
  }

  /** 手动新增入口：showModal 弹窗表单（静态骨架，用户输入只经 .value 读取，
      绝不回插 HTML）。content 必填（空则不发请求，行内红字提示）；kind 可选（留空
      后端记 fact）；tags 可选逗号分隔（中英文逗号都认）；source 固定 'manual'。
      成功关闭弹窗 + 通知 + 重拉列表（created_at DESC，新条目应出现在顶部）；
      失败弹窗保持打开（行内红字可改后重试）+ #memActionError + 通知。 */
  function openAddModal() {
    if (typeof showModal !== 'function' || typeof closeModal !== 'function') {
      showActionError('新增失败：showModal 组件不可用');
      if (typeof showNotification === 'function') showNotification('新增记忆不可用', 'showModal 组件未就绪', 'error', 3000);
      return;
    }
    const bodyHtml =
      '<label class="mem-slabel" for="memSaveContent">内容（必填）</label>' +
      '<textarea id="memSaveContent" class="mem-stextarea" rows="4" autocomplete="off" spellcheck="false" ' +
      'placeholder="要长期记住的内容，例如：MoRay 服务重启后先跑预检探针再发真实请求"></textarea>' +
      '<div id="memSaveErr" class="mem-serr" style="display:none"></div>' +
      '<label class="mem-slabel" for="memSaveKind">类型（可选，留空记为 fact）</label>' +
      '<input id="memSaveKind" class="mem-sinput" type="text" maxlength="32" autocomplete="off" spellcheck="false" placeholder="例如：preference" />' +
      '<label class="mem-slabel" for="memSaveTags">标签（可选，逗号分隔）</label>' +
      '<input id="memSaveTags" class="mem-sinput" type="text" autocomplete="off" spellcheck="false" placeholder="例如：部署,脚本" />' +
      '<div class="mem-shint">来源将记录为 manual（手动新增）；标签每项最长 32 字</div>';
    const footerHtml =
      '<button type="button" id="memSaveCancel" class="btn-ghost px-4 py-2 rounded-lg text-sm border border-line-ghost">取消</button>' +
      '<button type="button" id="memSaveOk" class="btn-primary px-4 py-2 rounded-lg text-sm">保存</button>';
    const box = showModal(bodyHtml, { title: '新增记忆', icon: 'plus', footer: footerHtml });
    if (!box) return;
    const contentEl = box.querySelector('#memSaveContent');
    const kindEl = box.querySelector('#memSaveKind');
    const tagsEl = box.querySelector('#memSaveTags');
    const errEl = box.querySelector('#memSaveErr');
    const okBtn = box.querySelector('#memSaveOk');
    const cancelBtn = box.querySelector('#memSaveCancel');
    if (cancelBtn) cancelBtn.addEventListener('click', () => closeModal());
    if (okBtn) okBtn.addEventListener('click', async () => {
      const content = String((contentEl && contentEl.value) || '').trim();
      if (!content) {
        if (errEl) { errEl.textContent = '内容不能为空'; errEl.style.display = ''; }
        if (contentEl) { try { contentEl.focus(); } catch (e) { /* 焦点失败不阻断 */ } }
        return; // 前端必填校验：空则不发请求
      }
      if (errEl) errEl.style.display = 'none';
      okBtn.disabled = true;
      try {
        if (typeof window.MorayMemory === 'undefined' || typeof window.MorayMemory.save !== 'function') {
          throw new Error('window.MorayMemory.save 不可用（memory_client.js 未加载或版本过旧）');
        }
        const tags = String((tagsEl && tagsEl.value) || '').split(/[,，]/).map(s => s.trim()).filter(Boolean);
        const r = await window.MorayMemory.save({
          content: content,
          kind: String((kindEl && kindEl.value) || '').trim(),
          tags: tags,
          source: 'manual'
        });
        closeModal();
        if (typeof showNotification === 'function') {
          showNotification('已保存', '新记忆已入库（尾号 ' + String((r && r.memory_id) || '').slice(-8) + '）', 'success', 3000);
        }
        await loadList(true);
      } catch (e) {
        const msg = String((e && e.message) || e);
        if (errEl) { errEl.textContent = '保存失败：' + msg; errEl.style.display = ''; }
        showActionError('保存失败：' + msg);
        if (typeof showNotification === 'function') showNotification('保存失败', msg.slice(0, 160), 'error', 4000);
      } finally {
        okBtn.disabled = false;
      }
    });
    if (contentEl) { try { contentEl.focus(); } catch (e) { /* 焦点失败不阻断 */ } }
  }

  /* ---------------- 页面切换（86 同款实现；本页不参与启动落点，仅供电内/卡片跳转） ---------------- */

  function switchTo(page) {
    document.querySelectorAll('.nav-icon-btn[data-page]').forEach(b => {
      b.classList.toggle('active', b.dataset.page === page);
    });
    document.querySelectorAll('.page-panel').forEach(p => {
      p.classList.add('hidden');
      p.classList.remove('flex');
    });
    const target = document.getElementById('page-' + page);
    if (target) {
      target.classList.remove('hidden');
      target.classList.add('flex');
      void target.offsetWidth;
    }
    document.querySelectorAll('.sidebar-panel').forEach(p => p.classList.add('hidden'));
    const sb = document.getElementById('sidebar-' + page);
    if (sb) { sb.classList.remove('hidden'); sb.classList.add('flex'); }
    if (typeof refreshIcons === 'function') refreshIcons();
  }

  /* ---------------- 挂载（70 bootApp 末步调用；幂等） ---------------- */

  function mount() {
    ensureStyle();
    ensureNavButton();
    ensurePage();
    buildPage();
    bindEvents();
    syncSwitch();
    loadList(false);
    if (typeof refreshIcons === 'function') refreshIcons();
  }

  return { mount: mount, loadList: loadList, PAGE: PAGE };
})();
window.MorayMemoryBrowser = MorayMemoryBrowser;
