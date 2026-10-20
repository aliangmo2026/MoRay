/* ===================== [Day19] 00_theme —— 主题规范令牌层（极简深灰 + 暖橙） =====================
   注入顺序：PARTS 数组首位（assemble.py），先于 135_ui_polish 令牌层。
   职责边界：
   1) 只定义"新命名空间"令牌（--color-bg-app / --color-brand / --radius-card / --shadow-menu 等），
      与 135 既有令牌（--bg-app / --brand / --radius-btn）及 Tailwind v4
      vendor 变量（--radius-xs..4xl / --shadow-2xs..2xl / --color-<palette>）不重名，避免级联互覆盖。
   2) [A-3b] 接线现状（修正旧注释"从未接线/纯机读规范"的说法）：memory_browser / 86_home_apps /
      88_local_log_intent / 91_log_monitor / 92_laya_agent 以 var(--color-* / --radius-*) + 字面量
      回退 的方式消费本层令牌，因此本层不能整层删除——部分消费方的回退值与令牌值不一致
      （如 --color-bg-hover 回退 .06 vs 令牌 .04、--color-border 回退 .12/.16 vs 令牌 .08），
      整层删除会改变这些面板的渲染。生效值仍以 135_ui_polish 的令牌层为主体，本层同名令牌值与之一致。
   3) [A-3b] 已删除的死令牌子集：--font-size-xs..2xl、--space-page / --space-card /
      --space-card-lg / --space-card-gap、--height-listitem、--width-sidebar。
      其中 --font-size-md/xs 与 --space-card-gap 仅被 86_home_apps 消费且其回退值与令牌值
      完全一致（14px / 12px / 12px），删除后渲染不变；其余为零引用。
   4) --color-success / --color-warning 在 135 也有定义（同名同值），在此重复声明仅为令牌表完整；
      从未被引用的 --shadow-menu / --color-error / --radius-chip / --radius-popover /
      --color-bg-bubble / --color-brand-hover / --font-sans-ui / --font-mono-ui / --line-height-*
      留待后续批次裁决（本层现时无消费方，删除不影响渲染）。 */
(function () {
  const el = document.createElement('style');
  el.id = 'morayThemeTokens00';
  el.textContent = `
:root {
  /* —— 色板：极简深灰（Linear / Vercel 式） —— */
  --color-bg-app: #0a0a0a;
  --color-bg-panel: #111111;
  --color-bg-card: #1a1a1a; /* [Day21d] #161616 → #1a1a1a：卡片抬一档，与页面底拉开可见层级（对标 zcode 卡片） */
  --color-bg-hover: rgba(255, 255, 255, 0.04);
  --color-bg-bubble: rgba(255, 255, 255, 0.06);
  --color-border: rgba(255, 255, 255, 0.08);
  --color-border-strong: rgba(255, 255, 255, 0.16);
  --color-text-primary: #ededed;
  --color-text-secondary: #a3a3a3;
  --color-text-tertiary: #666666;
  /* —— 强调色：暖橙 —— */
  --color-brand: #E5734A;
  --color-brand-hover: #D9663B;
  --color-brand-subtle: rgba(229, 115, 74, 0.08);
  /* —— 功能色 —— */
  --color-success: #4ade80;
  --color-warning: #fbbf24;
  --color-error: #ef4444;
  /* —— 几何：圆角（[UI 2.0] 卡片 16 / 控件 12 / 小标签 4）——
     命名避开 Tailwind 的 --radius-xs..4xl 标尺，防止全局工具类被改写 */
  --radius-chip: 4px;
  --radius-control: 12px;
  --radius-card: 16px;
  --radius-popover: 12px;
  /* —— [UI 2.0] 毛玻璃模糊半径（唯一消费方：135 三栏玻璃规则） —— */
  --backdrop-blur: 24px;
  /* —— 阴影：唯一允许的下拉菜单投影 —— */
  --shadow-menu: 0 8px 24px rgba(0, 0, 0, 0.5);
  /* —— 排版 —— */
  --font-sans-ui: system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, "PingFang SC", "Microsoft YaHei", sans-serif;
  --font-mono-ui: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace;
  /* [A-3b] --font-size-xs..2xl 已删：唯二消费方（86_home_apps）的回退值与令牌值一致 */
  --line-height-body: 1.6;
  --line-height-heading: 1.3;
  /* [A-3b] --space-page / --space-card / --space-card-lg / --space-card-gap、--height-listitem、
     --width-sidebar 已删：--space-card-gap 的消费方（86_home_apps）回退值 12px 与令牌值一致，
     其余零引用 */
}
/* [UI 2.0] 浅色覆盖：随 html[data-theme="light"] 生效，与 135 浅色块同值
   （文件头"同名令牌与 135 一致"约定的浅色侧；此前本层无浅色块，注入面板在浅色主题下
   一直沿用深色值——本轮补齐后 memory_browser / 86_home_apps / 88 / 91 / 92 面板随主题翻转） */
html[data-theme="light"] {
  --color-bg-app: #f4f5f7;
  --color-bg-panel: rgba(255, 255, 255, 0.7);
  --color-bg-card: rgba(255, 255, 255, 0.85);
  --color-bg-hover: rgba(0, 0, 0, 0.04);
  --color-bg-bubble: rgba(255, 255, 255, 0.9);
  --color-border: rgba(0, 0, 0, 0.04);
  --color-border-strong: rgba(0, 0, 0, 0.08);
  --color-text-primary: #1d1d1f;
  --color-text-secondary: #86868b;
  --color-text-tertiary: #8b949e;
}
`;
  document.head.appendChild(el);
})();
