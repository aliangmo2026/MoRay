/* ===================== [136] modern_skin —— 现代极简统一皮肤层 =====================
   职责边界（高度内聚，可整体删除回滚）：
   1) 本分片是 PARTS 注入顺序中最后一个 <style> 注入方（assemble.py 排在 135 之后、
      138/145/146 之前；这三者均不注入样式，故本层实际为最终覆盖层）。
      所有规则带 !important，用于压过：骨架静态 CSS（moray-workbench.html <style> 块）、
      135_ui_polish 的 UI 2.0 覆盖层、110_polish 的壁纸层、各分片运行时 append 的 style。
   2) 覆盖内容五大块：
      A. 品牌色统一极客蓝 #2f7cf6（令牌层重声明 + 骨架写死橙 rgba(229,115,74,…) 高频兜底）
      B. 消息流极简化：AI 裸排版强化（去头像/行高 1.85）+ 用户气泡淡蓝胶囊化
      C. 消息操作条默认全隐藏、悬停浮现（opacity 0→1 + pointer-events 隔离）
      D. 三栏毛玻璃统一档（去阴影/统一 blur 24px/更通透底色）+ 输入台瘦身
      E. §7 性能收口（掉帧排查 2026-10-26）：中栏/主区整列 backdrop-filter 关停（保留半透明
         底色与左栏玻璃）+ 信标点撤永久合成层、历史消息脉冲停振、关键帧光环改蓝
   3) 输入框自动增高上限 128→400 在骨架内联脚本（两处），本层补 CSS 侧
      max-height:400px 否则 max-h-32 工具类会把 JS 结果夹回 128px。
   4) 回滚：删除本文件 + 重跑 assemble.py 即可（或按 backup\modern_skin_pre_20261026
      还原 135/骨架/assemble.py 三件套）。本分片不改任何 JS 行为，纯展示层。 */
(function () {
  const el = document.createElement('style');
  el.id = 'morayModernSkin136';
  el.textContent = `
/* ---------- §1 品牌令牌统一：极客蓝 #2f7cf6（压过 00_theme 橙基线与 135 §38 同名声明） ---------- */
:root,
html[data-theme="dark"] {
  --brand: #2f7cf6 !important;
  --brand-hover: #1d4ed8 !important;
  --brand-light: rgba(47, 124, 246, 0.10) !important;
  --brand-text: #60a5fa !important;
  --brand-on: #ffffff !important;
  --accent: #2f7cf6 !important;
  --glow-blue: rgba(47, 124, 246, 0.28) !important;
  --color-brand: #2f7cf6 !important;
  --color-brand-hover: #1d4ed8 !important;
  --color-brand-subtle: rgba(47, 124, 246, 0.10) !important;
  --color-brand-cobalt: #2f7cf6 !important;
  --color-brand-cobalt-hover: #1d4ed8 !important;
  --color-brand-violet: #2f7cf6 !important;
  --color-brand-cyan: #2f7cf6 !important;
  --brand-cobalt: #2f7cf6 !important;
  --brand-cobalt-hover: #1d4ed8 !important;
}
html[data-theme="light"] {
  --brand-text: #1e40af !important;
  --brand-light: rgba(47, 124, 246, 0.10) !important;
  --glow-blue: rgba(47, 124, 246, 0.22) !important;
}

/* ---------- §2 消息流极简化（DeepSeek 式裸排版） ----------
   AI 侧 135 §"去气泡"已把底色/边框归零，本层在其上补三点：
   ① 隐藏装饰头像（flex 布局下正文自动占满）② 行高 1.85 ③ 元信息行留白 */
#chatMessagesNormal .msg-bubble > .msg-avatar { display: none !important; }
#chatMessagesNormal .md-body { line-height: 1.85 !important; }
#chatMessagesNormal .msg-bubble__ai-box { padding: 6px 0 !important; }
/* 用户气泡：淡蓝圆角胶囊（覆盖 135 的 --brand-light 卡片形态） */
#chatMessagesNormal .msg-bubble__user-box {
  background: rgba(47, 124, 246, 0.12) !important;
  border: 1px solid rgba(47, 124, 246, 0.20) !important;
  border-radius: 20px !important;
  padding: 10px 18px !important;
}

/* ---------- §3 消息操作条：默认全隐藏，悬停浮现 ----------
   骨架只有 opacity 过渡（隐藏态仍可点中），这里补 pointer-events 隔离 + 0.2s 缓动 */
#chatMessagesNormal .msg-actions {
  opacity: 0 !important;
  pointer-events: none !important;
  transition: opacity 0.2s ease !important;
}
#chatMessagesNormal .msg-bubble:hover .msg-actions,
#chatMessagesNormal .msg-bubble:focus-within .msg-actions {
  opacity: 1 !important;
  pointer-events: auto !important;
}

/* ---------- §4 三栏毛玻璃统一档：blur 24px / 去阴影 / 底色更通透 ---------- */
#app > aside.bg-surface-deep {
  background-color: rgba(16, 18, 26, 0.58) !important;
  -webkit-backdrop-filter: blur(24px) saturate(1.2) !important;
  backdrop-filter: blur(24px) saturate(1.2) !important;
  box-shadow: none !important;
}
#middleSidebar {
  background-color: rgba(16, 18, 26, 0.40) !important;
  -webkit-backdrop-filter: blur(24px) saturate(1.2) !important;
  backdrop-filter: blur(24px) saturate(1.2) !important;
  box-shadow: none !important;
}
main.bg-surface-void {
  background-color: rgba(16, 18, 26, 0.28) !important;
  -webkit-backdrop-filter: blur(24px) saturate(1.2) !important;
  backdrop-filter: blur(24px) saturate(1.2) !important;
  box-shadow: none !important;
}
html[data-theme="light"] #app > aside.bg-surface-deep {
  background-color: rgba(255, 255, 255, 0.62) !important;
}
html[data-theme="light"] #middleSidebar {
  background-color: rgba(255, 255, 255, 0.45) !important;
}
html[data-theme="light"] main.bg-surface-void {
  background-color: rgba(255, 255, 255, 0.32) !important;
}
/* 壁纸档：模糊统一 24px；110 的橙色辉光环改蓝 */
#app[data-wallpaper] { --wall-blur: 24px !important; --wall-glow-rgb: 47, 124, 246 !important; }
#app[data-wallpaper] > aside.bg-surface-deep,
#app[data-wallpaper] #middleSidebar { box-shadow: none !important; }

/* ---------- §5 输入台瘦身 ----------
   ① 隐藏提示文案与字/Token 统计（功能保留，仅不展示）
   ② 工具行间距 6px、溢出改为换行而不是横向裁切
   ③ textarea 上限 400px（配合骨架 JS 400；压过 max-h-32 工具类的 128px 夹断） */
#coreInputContainer .input-stats-hint { display: none !important; }
#coreInputContainer .input-stats-left { display: none !important; }
#coreInputContainer .input-toolbar {
  gap: 6px !important;
  flex-wrap: wrap !important;
  overflow-x: visible !important;
  row-gap: 4px !important;
}
#coreInputContainer .input-toolbar-btn { border-radius: 8px !important; }
#coreInputContainer .input-toolbar-divider { margin: 0 6px !important; }
#chatInputNormal { max-height: 400px !important; }

/* ---------- §6 残余橙兜底表（骨架写死 rgba(229,115,74,…) 的高频可见落点） ---------- */
/* 欢迎页建议卡/快捷指令/工作流图标：骨架内联 style 写死橙底，!important 类规则可压内联 */
.welcome-suggestion-icon,
.slash-menu-item-icon,
.workflow-icon {
  background: rgba(47, 124, 246, 0.12) !important;
  color: var(--brand-text) !important;
}
#newChatMoreMenu .command-item:hover { background: rgba(47, 124, 246, 0.10) !important; }
.new-chat-main-btn:hover { box-shadow: 0 0 14px rgba(47, 124, 246, 0.35) !important; }
.input-toolbar-btn:hover { background: rgba(47, 124, 246, 0.10) !important; color: var(--brand-text) !important; }
/* 正文引用块/链接下划线/引用胶囊/消息操作激活态 */
.md-body blockquote {
  border-left-color: rgba(47, 124, 246, 0.6) !important;
  background: rgba(47, 124, 246, 0.06) !important;
}
.md-body a { border-bottom-color: rgba(47, 124, 246, 0.4) !important; }
.citation-chip { background: rgba(47, 124, 246, 0.10) !important; border-color: rgba(47, 124, 246, 0.25) !important; }
.citation-chip:hover { background: rgba(47, 124, 246, 0.18) !important; }
.msg-action-btn.active { background: rgba(47, 124, 246, 0.14) !important; }
.argus-mode-pill:hover { background: rgba(47, 124, 246, 0.16) !important; }
/* Laya 面板（92 运行时 style 写死 #E5734A 系） */
.laya-agent-btn.on {
  background: rgba(47, 124, 246, 0.16) !important;
  color: #60a5fa !important;
  border-color: rgba(47, 124, 246, 0.5) !important;
}
.laya-btn:hover,
.laya-raw-btn:hover { border-color: rgba(47, 124, 246, 0.45) !important; color: #60a5fa !important; }
.laya-tag { background: rgba(47, 124, 246, 0.07) !important; }
.laya-tag:hover { border-color: rgba(47, 124, 246, 0.5) !important; color: #60a5fa !important; }
.laya-sr .tt { color: #60a5fa !important; }
.laya-chat-detail:hover { border-color: rgba(47, 124, 246, 0.5) !important; color: #60a5fa !important; }
.laya-p-prog i { background: linear-gradient(90deg, #2f7cf6, #60a5fa) !important; }

/* ---------- §7 性能收口（掉帧排查 2026-10-26） ----------
   ① 只保留左栏一层毛玻璃；中栏/主区整列 backdrop-filter 全关（保留半透明底色，
      通透度几乎不变，省掉两个整列 GPU 模糊 + 输入台对主区的链式二次采样）。
      覆盖对象：135 §2 拆封印块（深浅双档）+ 110 壁纸三列规则 + 本层 §4 自身。
   ② 信标点：撤 will-change 永久合成层；历史消息的永动脉冲（box-shadow 关键帧=每帧重绘）
      全停，只留最新一条呼吸；关键帧光环保留但从写死橙 rgba(229,115,74,…) 改蓝。 */
#middleSidebar,
main.bg-surface-void,
#app[data-wallpaper] #middleSidebar,
#app[data-wallpaper] main.bg-surface-void {
  -webkit-backdrop-filter: none !important;
  backdrop-filter: none !important;
}
.beacon-dot { will-change: auto !important; }
#chatMessagesNormal .msg-bubble:not(:last-child) .beacon-dot {
  animation: none !important;
  box-shadow: none !important;
}
@keyframes beacon {
  0%, 100% { opacity: 1; box-shadow: 0 0 0 0 rgba(47, 124, 246, 0.45); }
  50%      { opacity: 0.65; box-shadow: 0 0 0 4px rgba(47, 124, 246, 0); }
}

/* ---------- §8 字体与卡片质感（真实数据链路批 2026-10-26） ----------
   ① 数字等宽：金额/统计/Token 计数统一 tabular-nums，数值变化时字符等宽不跳动。
   ② 卡片去生硬边框：1px 描边降为极淡（浅色0.05/深色0.05 黑白系），改双层微阴影
      （近沿 1px + 远沿 24px 大扩散）撑层次。覆盖对象与 135 §9 卡片族对齐，另含
      .memcard（memory_browser 运行时内联定义）与 Tailwind border 工具类实例
      （成本中心/工作流监控卡的 border border-line-ghost/60 —— border-color 覆盖即达成）。
      135 §9 的 hover 规则无 !important，被本层静态阴影常驻压过 → 末尾补 hover 抬升。 */
.font-mono, .monitor-stat-value, .cost-pill, .token-meter span,
.input-stats-item .value, #costStatCards .text-sm { font-variant-numeric: tabular-nums; }
.glass-card, .doc-card, .workflow-card, .memcard, .timestamp-card,
.model-detail-card, .error-card, .card, .tl-card, .prompt-card, .snippet-card {
  border-color: rgba(255, 255, 255, 0.05) !important;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.5), 0 8px 24px -12px rgba(0, 0, 0, 0.8) !important;
}
html[data-theme="light"] .glass-card, html[data-theme="light"] .doc-card,
html[data-theme="light"] .workflow-card, html[data-theme="light"] .memcard,
html[data-theme="light"] .timestamp-card, html[data-theme="light"] .model-detail-card,
html[data-theme="light"] .error-card, html[data-theme="light"] .card,
html[data-theme="light"] .tl-card, html[data-theme="light"] .prompt-card,
html[data-theme="light"] .snippet-card {
  border-color: rgba(0, 0, 0, 0.05) !important;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.06), 0 8px 24px -12px rgba(0, 0, 0, 0.12) !important;
}
.glass-card:hover, .doc-card:hover, .workflow-card:hover, .memcard:hover {
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.5), 0 12px 32px -12px rgba(0, 0, 0, 0.9) !important;
}
html[data-theme="light"] .glass-card:hover, html[data-theme="light"] .doc-card:hover,
html[data-theme="light"] .workflow-card:hover, html[data-theme="light"] .memcard:hover {
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.08), 0 12px 32px -12px rgba(0, 0, 0, 0.16) !important;
}
`;
  document.head.appendChild(el);
})();
