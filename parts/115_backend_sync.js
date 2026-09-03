/* ============================================================
   [里程碑M3] BackendStore：会话/消息/设置 SQLite 同步层
   方法签名与 IndexedDB 封装（DB 对象）对齐，上层聊天代码无感知：
   - 在线（MorayBackend.connected && useBackendSync）：写路径先落本地
     IndexedDB（快、不丢），再即时推后端（失败 → 标记"待同步"，不抛错）
   - 读路径日常走 IndexedDB（本地为离线缓存）；启动/恢复时从后端 pull 合并
   - "立即同步"：pull → push → pull（冲突以 updated_at 较新者为准）
   - 后端离线：完全走现有 IndexedDB，行为与改造前一致
   ============================================================ */
(function () {
  const W = window;
  const BackendStore = {
    lastSyncAt: 0,
    _pushing: false,

    /** 后端 base URL（[M5修复] 以 MorayBackend.origin 为事实源，任意 host 均可；
     * 兜底用 new URL 从 url 提取，不再限定 127.0.0.1）
     * @returns {string} */
    base() {
      try {
        const mb = W.MorayBackend;
        if (!mb) return '';
        if (mb.origin) return mb.origin;
        if (mb.url) {
          const u = new URL(mb.url);
          return u.origin;
        }
        return '';
      } catch (e) { return ''; }
    },

    /** 是否启用后端同步（开关开 + 后端在线） @returns {boolean} */
    enabled() {
      try {
        if (MoraySettings.get('useBackendSync') === false) return false;
        const mb = W.MorayBackend;
        return !!(mb && mb.connected && this.base());
      } catch (e) { return false; }
    },

    /** 统一 JSON 请求 @returns {Promise<{ok:boolean,data?:any,message?:string}>} */
    async _req(method, path, body, timeoutMs) {
      const base = this.base();
      if (!base) throw new Error('后端不可用');
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), timeoutMs || 8000);
      try {
        const res = await fetch(base + path, {
          method,
          headers: { 'Content-Type': 'application/json' },
          signal: ctrl.signal,
          body: body !== undefined ? JSON.stringify(body) : undefined
        });
        const j = await res.json().catch(() => null);
        if (!res.ok || !j || j.ok !== true) {
          throw new Error((j && j.message) || ('后端返回 ' + res.status));
        }
        return j;
      } finally { clearTimeout(timer); }
    },

    /** 标记会话同步状态（写 IndexedDB 扩展字段；listConversations 未包装可直接用，
     * updateConversation 必须走原始方法避免包装递归） */
    async _markConvStatus(convId, status) {
      try {
        const convs = await DB.listConversations();
        const conv = convs.find(c => c.id === convId);
        if (conv) { conv.syncStatus = status; await _origDB.updateConversation(convId, { syncStatus: status }); }
      } catch (e) { /* 忽略 */ }
    },

    // ================= 写路径（先本地，再推后端） =================
    /** 推送新建/更新会话 @returns {Promise<boolean>} 后端是否成功 */
    async pushConversation(conv) {
      const body = {
        id: conv.id,
        title: conv.title || '',
        model: conv.model || '',
        system_prompt: conv.systemPrompt || '',
        user_picked_model: !!conv.userPickedModel,
        pinned: !!conv.pinned,
        created_at: conv.createdAt ? new Date(conv.createdAt).toISOString() : undefined,
        updated_at: conv.updatedAt ? new Date(conv.updatedAt).toISOString() : undefined
      };
      await this._req('POST', '/api/conversations', body);
      return true;
    },

    /** 推送会话更新（title/model/systemPrompt/userPickedModel/pinned） */
    async pushConversationPatch(convId, patch) {
      await this._req('PUT', '/api/conversations/' + encodeURIComponent(convId), patch);
      return true;
    },

    /** 推送删除会话 */
    async pushConversationDelete(convId) {
      await this._req('DELETE', '/api/conversations/' + encodeURIComponent(convId));
      return true;
    },

    /** 推送单条消息 */
    async pushMessage(msg) {
      await this._req('POST', '/api/conversations/' + encodeURIComponent(msg.conversationId) + '/messages', {
        id: msg.id,
        conversationId: msg.conversationId,
        role: msg.role,
        content: msg.content || '',
        reasoning: msg.reasoning || '',
        model: msg.model || '',
        createdAt: msg.createdAt ? new Date(msg.createdAt).toISOString() : undefined
      });
      return true;
    },

    /** 推送删除消息 */
    async pushMessageDelete(msgId) {
      await this._req('DELETE', '/api/messages/' + encodeURIComponent(msgId));
      return true;
    },

    /** 推送设置（排除 API Key 字段，key 绝不进后端） */
    async pushSettings() {
      const c = MoraySettings.get();
      const body = {};
      for (const k of Object.keys(c)) {
        if (k === 'openaiAPIKey' || k === 'openaiAPIKeyStored') continue;
        const v = c[k];
        body[k] = (typeof v === 'object' && v !== null) ? JSON.stringify(v) : String(v);
      }
      await this._req('PUT', '/api/settings', body);
      return true;
    },

    // ================= 读路径（pull 合并到本地） =================
    /** 从后端拉取会话列表 + 详情消息，合并进 IndexedDB（updated_at 较新者胜） */
    async pull() {
      if (!this.enabled()) return 0;
      const j = await this._req('GET', '/api/conversations?limit=2000');
      const remoteConvs = j.data || [];
      const localConvs = await DB.listConversations();
      const localById = {};
      localConvs.forEach(c => { localById[c.id] = c; });
      let merged = 0;
      for (const rc of remoteConvs) {
        const lc = localById[rc.id];
        const rTs = rc.updated_at ? new Date(rc.updated_at).getTime() : 0;
        const lTs = lc && lc.updatedAt ? new Date(lc.updatedAt).getTime() : 0;
        if (!lc || rTs > lTs) {
          // [修复] 时间戳统一转数字（本地 conv.updatedAt 为 epoch ms，避免与 ISO 字符串混用导致排序错乱）
          const toNum = (v) => { const t = v ? new Date(v).getTime() : 0; return isNaN(t) ? 0 : t; };
          const conv = {
            id: rc.id, title: rc.title, model: rc.model, systemPrompt: rc.system_prompt,
            userPickedModel: !!rc.user_picked_model, pinned: !!rc.pinned,
            createdAt: toNum(rc.created_at), updatedAt: toNum(rc.updated_at),
            syncStatus: '已同步'
          };
          await DB.createConversation(conv);
          merged++;
        }
        // 拉该会话消息合并
        try {
          const dj = await this._req('GET', '/api/conversations/' + encodeURIComponent(rc.id));
          const rmsgs = (dj.data && dj.data.messages) || [];
          const localMsgs = await DB.listMessagesByConversation(rc.id);
          const lmById = {};
          localMsgs.forEach(m => { lmById[m.id] = m; });
          for (const rm of rmsgs) {
            const lmsg = lmById[rm.id];
            const rMt = rm.created_at ? new Date(rm.created_at).getTime() : 0;
            const lMt = lmsg && lmsg.createdAt ? new Date(lmsg.createdAt).getTime() : 0;
            if (!lmsg || rMt > lMt) {
              // [修复] 消息 createdAt 统一转数字时间戳
              const t = rm.created_at ? new Date(rm.created_at).getTime() : Date.now();
              await DB.putMessage({
                id: rm.id, conversationId: rm.conversation_id, role: rm.role,
                content: rm.content, reasoning: rm.reasoning, model: rm.model,
                createdAt: isNaN(t) ? Date.now() : t
              });
              merged++;
            }
          }
        } catch (e) { /* 单会话拉取失败不阻断整体 */ }
      }
      // 本地有而后端没有的会话标记"待同步"（等待一键上传）
      const remoteIds = new Set(remoteConvs.map(c => c.id));
      for (const lc of localConvs) {
        if (!remoteIds.has(lc.id) && lc.syncStatus !== '待同步') {
          await this._markConvStatus(lc.id, '待同步');
        }
      }
      this.lastSyncAt = Date.now();
      this._saveLastSync();
      return merged;
    },

    /** 一键同步：pull → push（本地全部）→ pull（较新者胜） */
    async pushAll(onProgress) {
      if (this._pushing) return;
      this._pushing = true;
      try {
        if (onProgress) onProgress('同步中…');
        // 1) 先拉远端较新的数据到本地
        await this.pull();
        // 2) 本地全部会话 + 消息上传（upsert 幂等）
        const convs = await DB.listConversations();
        for (let i = 0; i < convs.length; i++) {
          const conv = convs[i];
          if (onProgress) onProgress('同步会话 ' + (i + 1) + '/' + convs.length);
          await this._markConvStatus(conv.id, '同步中');
          await this.pushConversation(conv).catch(() => {});
          const msgs = await DB.listMessagesByConversation(conv.id);
          if (msgs.length) {
            await this._req('POST', '/api/conversations/batch-messages', {
              messages: msgs.map(m => ({
                id: m.id, conversationId: m.conversationId, role: m.role,
                content: m.content || '', reasoning: m.reasoning || '', model: m.model || '',
                createdAt: m.createdAt ? new Date(m.createdAt).toISOString() : undefined
              }))
            }).catch(() => {});
          }
          await this._markConvStatus(conv.id, '已同步');
        }
        // 3) 设置
        await this.pushSettings().catch(() => {});
        // 4) 拉回合并（较新者胜，避免覆盖远端更新）
        await this.pull();
        this.lastSyncAt = Date.now();
        this._saveLastSync();
        if (onProgress) onProgress('done');
        return true;
      } finally { this._pushing = false; }
    },

    // ================= 辅助 =================
    _saveLastSync() {
      try { localStorage.setItem('moray_last_sync', String(this.lastSyncAt)); } catch (e) { /* 忽略 */ }
    },
    lastSyncTimeText() {
      try {
        const raw = localStorage.getItem('moray_last_sync');
        if (!raw) return '从未同步';
        const d = new Date(parseInt(raw, 10));
        return d.toLocaleString();
      } catch (e) { return '从未同步'; }
    }
  };
  W.BackendStore = BackendStore;

  /* ================= DB 写方法包装（聊天代码无感知） ================= */
  const _origDB = {};
  function wrapDB(method, pusher) {
    if (typeof DB[method] !== 'function' || _origDB[method]) return;
    _origDB[method] = DB[method].bind(DB);
    DB[method] = async function (...args) {
      // 先落本地（快、不丢）
      const r = await _origDB[method](...args);
      // 即时推后端（失败 → 标记待同步，不抛错不阻断）
      const payload = (r && typeof r === 'object' && r.id) ? r : args[0];
      const convId = (payload && (payload.id || payload.conversationId)) || (typeof args[0] === 'string' ? args[0] : null);
      if (BackendStore.enabled()) {
        try {
          await pusher(payload, args);
          if (convId) await BackendStore._markConvStatus(convId, '已同步');
        } catch (e) {
          if (convId) await BackendStore._markConvStatus(convId, '待同步').catch(() => {});
        }
      } else if (MoraySettings.get('useBackendSync') !== false && convId) {
        // [M3] 后端离线但同步开关开启：标记"待同步"（重启后端后可一键同步上传）
        await BackendStore._markConvStatus(convId, '待同步').catch(() => {});
      }
      return r;
    };
  }
  // createConversation / updateConversation / deleteConversation / putMessage / deleteMessage
  wrapDB('createConversation', (conv) => BackendStore.pushConversation(conv));
  wrapDB('updateConversation', (conv, args) => {
    const patch = args[1] || {};
    const p = {};
    if ('title' in patch) p.title = patch.title;
    if ('model' in patch) p.model = patch.model;
    if ('systemPrompt' in patch) p.system_prompt = patch.systemPrompt;
    if ('userPickedModel' in patch) p.user_picked_model = !!patch.userPickedModel;
    if ('pinned' in patch) p.pinned = !!patch.pinned;
    if ('syncStatus' in patch) return Promise.resolve(); // 同步状态不入后端
    return BackendStore.pushConversationPatch(conv.id, p);
  });
  wrapDB('deleteConversation', (conv, args) => BackendStore.pushConversationDelete((conv && conv.id) || args[0]));
  wrapDB('putMessage', (msg) => BackendStore.pushMessage(msg));
  wrapDB('deleteMessage', (msg, args) => BackendStore.pushMessageDelete(args[0]));

  /* ================= 设置同步（排除 key） ================= */
  const _origSettingsSet = MoraySettings.set.bind(MoraySettings);
  MoraySettings.set = async function (patch, value) {
    await _origSettingsSet(patch, value);
    if (BackendStore.enabled()) {
      BackendStore.pushSettings().catch(() => {});
    }
  };

  /* ================= 会话列表同步状态小标记 ================= */
  /** 按 conv.syncStatus 在会话项上追加小徽章（已同步/待同步/同步中）；渲染后调用 */
  W.refreshSyncBadges = async function () {
    try {
      const items = document.querySelectorAll('.session-item');
      if (!items.length) return;
      const convs = await DB.listConversations();
      const byId = {};
      convs.forEach(c => { byId[c.id] = c; });
      items.forEach(item => {
        const old = item.querySelector('.sync-badge');
        if (old) old.remove();
        const convId = item.getAttribute('data-conv-id') || (item.dataset && item.dataset.convId);
        const conv = convId ? byId[convId] : null;
        if (!conv || !conv.syncStatus || conv.syncStatus === '已同步') return;
        const badge = document.createElement('span');
        badge.className = 'sync-badge';
        badge.style.cssText = 'font-size:8px;line-height:1;padding:2px 4px;border-radius:4px;margin-left:4px;flex-shrink:0;';
        if (conv.syncStatus === '待同步') {
          badge.textContent = '待同步';
          badge.style.color = '#F2B24C';
          badge.style.background = 'rgba(242,178,76,0.12)';
          badge.title = '后端离线时创建/修改，重启后端后点「立即同步」上传';
        } else {
          badge.textContent = '同步中';
          badge.style.color = '#5B8CFF';
          badge.style.background = 'rgba(91,140,255,0.12)';
        }
        item.appendChild(badge);
      });
    } catch (e) { /* 标记失败不影响列表 */ }
  };

  /* ================= 启动恢复 + 事件挂钩 ================= */

  /** [M3修复] 从 DB 重载内存会话列表并渲染：
   * renderSessionList 只渲染内存 AppState.conversations，pull 写 IndexedDB 后必须重新读入内存；
   * 防御：DB 结果为空且内存非空时不用空列表覆盖（避免时序竞态把已显示的列表清空）。 */
  async function reloadSessionsFromDB() {
    try {
      const fromDB = await DB.listConversations();
      const inMem = (AppState.conversations || []).length;
      if (!fromDB.length && inMem > 0) return; // 竞态防御：不空覆盖
      // 按现有规则排序：(b.pinned-a.pinned) || ((b.order||b.updatedAt)-(a.order||a.updatedAt))
      fromDB.sort((a, b) => (b.pinned - a.pinned) || ((b.order || b.updatedAt) - (a.order || a.updatedAt)));
      AppState.conversations = fromDB;
      if (typeof rebuildMsgIndex === 'function') await rebuildMsgIndex();
      if (typeof renderSessionList === 'function') renderSessionList();
      if (typeof refreshSyncBadges === 'function') await refreshSyncBadges();
    } catch (e) { /* 重载失败不打断 */ }
  }

  async function onBackendProbed() {
    if (!BackendStore.enabled()) return;
    try {
      // 首次启用且后端为空、本地有数据 → 提示引导一键上传（不静默）
      const j = await BackendStore._req('GET', '/api/conversations?limit=1');
      const remoteCount = (j.data || []).length;
      const localCount = (await DB.listConversations()).length;
      const prompted = localStorage.getItem('moray_sync_first_prompted');
      if (remoteCount === 0 && localCount > 0 && !prompted) {
        localStorage.setItem('moray_sync_first_prompted', '1');
        try {
          if (typeof showConfirm === 'function') {
            showConfirm('检测到本地数据', `后端(SQLite)为空，本地 IndexedDB 有 ${localCount} 条会话。是否现在一键上传到后端？（不会删除本地数据）`, async () => {
              await BackendStore.pushAll();
              await reloadSessionsFromDB();
              try { showNotification('同步完成', '本地数据已上传到后端', 'success', 2500); } catch (e) { /* 忽略 */ }
            }, { okText: '一键上传' });
          }
        } catch (e) { /* 忽略 */ }
        return;
      }
      // 常规恢复：pull 合并 → 重载内存并渲染（修复"刷新后列表空白"）
      await BackendStore.pull();
      await reloadSessionsFromDB();
    } catch (e) { /* 同步失败可见但不打断 */ }
  }

  W.addEventListener('moray-backend-probed', onBackendProbed);
})();
