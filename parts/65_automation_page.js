
/* ============================================================
   模块：自动化工作流页面构建（接管静态模板区）
   ============================================================ */

/** 自动化页面构建器 */
const AutomationPage = {
  /** 重建页面骨架（幂等：重复调用安全）
   * @returns {void} */
  build() {
    const page = document.getElementById('page-automation');
    if (!page || page.querySelector('#workflowGrid')) return;
    page.innerHTML = `
      <div class="h-14 px-5 flex items-center justify-between border-b border-line-ghost/50 flex-shrink-0">
        <div class="flex items-center gap-2">
          <i data-lucide="workflow" class="w-4 h-4 text-brand-cobalt"></i>
          <span class="text-sm font-medium text-text-primary">自动化工作流</span>
          <span class="tag-pill bg-brand-cobalt/15 text-brand-cobalt" id="workflowCountPill">0 个模板</span>
        </div>
        <div class="flex items-center gap-2">
          <button class="btn-ghost px-3 h-8 rounded-lg text-xs flex items-center gap-1.5 border border-line-ghost" id="importWorkflowBtn">
            <i data-lucide="upload" class="w-3.5 h-3.5"></i>导入
          </button>
          <button class="btn-ghost px-3 h-8 rounded-lg text-xs flex items-center gap-1.5 border border-line-ghost" id="exportWorkflowBtn" title="导出全部工作流（JSON）">
            <i data-lucide="download" class="w-3.5 h-3.5"></i>导出
          </button>
          <button class="btn-primary px-3 h-8 rounded-lg text-xs flex items-center gap-1.5 relative overflow-hidden" id="newWorkflowBtn" onclick="createRipple(event)">
            <i data-lucide="plus" class="w-3.5 h-3.5"></i>
            新建工作流
          </button>
        </div>
      </div>
      <div class="flex-1 overflow-y-auto p-5">
        <!-- [M5.1安全] 自定义 JS 代码节点开关（默认关闭；开启需确认风险） -->
        <div class="mb-4 flex items-center justify-between rounded-lg bg-surface-panel/60 border border-line-ghost/50 px-3 py-2">
          <div class="flex-1 mr-3">
            <div class="text-xs text-text-secondary">允许自定义 JS 代码节点</div>
            <div class="text-[10px] text-text-tertiary mt-0.5">代码节点将在本机浏览器主线程执行，可访问页面数据与本地存储（默认关闭，推荐保持关闭）</div>
          </div>
          <button class="btn-ghost px-2.5 py-1 rounded-lg text-[10px] border border-line-ghost" id="codeNodesToggleBtn">关闭</button>
        </div>
        <!-- 执行监控面板 -->
        <div class="mb-6 hidden" id="workflowRunSection">
          <h3 class="text-xs font-medium text-text-secondary mb-3 flex items-center gap-2">
            <span class="w-1.5 h-1.5 rounded-full bg-warning animate-beacon"></span>
            执行监控
          </h3>
          <div class="running-workflow" id="runningWorkflow">
            <div class="running-workflow-header">
              <div class="flex items-center gap-2">
                <div class="w-7 h-7 rounded-lg bg-warning/15 flex items-center justify-center">
                  <i data-lucide="loader-2" class="w-3.5 h-3.5 text-warning animate-spin"></i>
                </div>
                <div>
                  <div class="text-xs font-medium text-text-primary" id="runWorkflowName">工作流</div>
                  <div class="text-[10px] text-text-tertiary" id="runWorkflowMeta">准备中</div>
                </div>
              </div>
              <div class="flex items-center gap-2">
                <button class="text-[10px] text-text-secondary hover:text-text-primary px-2 py-1 rounded border border-line-ghost" id="runPauseBtn">暂停</button>
                <button class="text-[10px] text-danger hover:text-danger/80 px-2 py-1 rounded border border-danger/30" id="runStopBtn">停止</button>
              </div>
            </div>
            <div class="running-workflow-progress">
              <div class="running-workflow-progress-fill" id="runProgressFill" style="width: 0%"></div>
            </div>
            <div class="running-workflow-steps" id="runSteps"></div>
          </div>
          <div class="glass-card rounded-xl border border-line-ghost/60 mt-3 overflow-hidden">
            <div class="px-4 py-2.5 flex items-center justify-between border-b border-line-ghost/50">
              <span class="text-xs font-medium text-text-secondary flex items-center gap-1.5">
                <i data-lucide="scroll-text" class="w-3.5 h-3.5 text-brand-cobalt"></i>执行日志
              </span>
              <button class="text-[10px] text-text-tertiary hover:text-text-secondary" id="runLogClear">清空</button>
            </div>
            <div class="workflow-log max-h-56 overflow-y-auto" id="runLog"></div>
          </div>
        </div>

        <!-- 工作流列表 -->
        <h3 class="text-xs font-medium text-text-secondary mb-3">工作流</h3>
        <div class="grid grid-cols-2 gap-3" id="workflowGrid"></div>

        <!-- 执行历史 -->
        <div class="mt-6">
          <h3 class="text-xs font-medium text-text-secondary mb-3 flex items-center justify-between">
            <span>执行历史</span>
            <button class="text-[10px] text-text-tertiary hover:text-text-secondary" id="clearWfHistoryBtn">清空历史</button>
          </h3>
          <div class="space-y-2" id="workflowHistoryList"></div>
        </div>

        <!-- 模型性能监控 -->
        <div class="mt-6">
          <h3 class="text-xs font-medium text-text-secondary mb-3 flex items-center justify-between">
            <span>模型性能监控（近 20 次请求）</span>
            <span class="text-[10px] text-text-tertiary" id="monitorLiveText">等待请求</span>
          </h3>
          <div class="glass-card rounded-xl p-4 border border-line-ghost/60">
            <div class="flex items-center gap-4 mb-2" id="monitorLegend"></div>
            <div class="monitor-chart" id="monitorChart"></div>
            <div class="monitor-stats">
              <div class="monitor-stat">
                <div class="monitor-stat-value text-brand-cobalt" id="statAvgSpeed">--</div>
                <div class="monitor-stat-label">平均 tok/s</div>
              </div>
              <div class="monitor-stat">
                <div class="monitor-stat-value text-brand-violet" id="statAvgLatency">--</div>
                <div class="monitor-stat-label">首字延迟</div>
              </div>
              <div class="monitor-stat">
                <div class="monitor-stat-value text-success" id="statSuccessRate">--</div>
                <div class="monitor-stat-label">成功率</div>
              </div>
            </div>
          </div>
        </div>
      </div>`;
    refreshIcons();
    // [M5.1安全] 代码节点开关绑定（默认关闭；开启需确认风险）
    const codeToggle = document.getElementById('codeNodesToggleBtn');
    if (codeToggle) {
      const syncState = () => {
        const on = MoraySettings.get('codeNodesEnabled') === true;
        codeToggle.textContent = on ? '开启' : '关闭';
        codeToggle.classList.toggle('active', on);
      };
      codeToggle.addEventListener('click', async () => {
        const cur = MoraySettings.get('codeNodesEnabled') === true;
        if (!cur) {
          // 开启前弹风险说明
          const doEnable = async () => {
            await MoraySettings.set('codeNodesEnabled', true);
            syncState();
            try { showNotification('已开启', '自定义 JS 代码节点将在本机浏览器执行，请仅运行可信工作流', 'warning', 3000); } catch (e) { /* 忽略 */ }
          };
          if (typeof showConfirm === 'function') {
            showConfirm('安全提示', '自定义代码节点会在本机浏览器主线程执行，可访问页面数据与本地存储（含可能保存的 API Key）。仅建议运行完全可信的工作流。确定开启？', doEnable, { okText: '确定开启', danger: true });
          } else { await doEnable(); }
        } else {
          await MoraySettings.set('codeNodesEnabled', false);
          syncState();
        }
      });
      syncState();
    }
  }
};
