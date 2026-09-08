# MoRay 变更日志

## 3.19.0（2026-09-08）通宵综合迭代：UI 精修 D1-D8 + 13 项核心功能全链路回归

- **UI 精修（3.18.11，全部落在 135 注入段 / parts）**：
  - D1 输入区：聚焦光核描边增强（:focus-within 提亮，代码模式激活态不被覆盖）、发送按钮 active 按压反馈与过渡；
  - D2 消息气泡：AI 气泡叠极淡品牌蓝底（rgba(91,140,255,.05) 区分层级，色卡质感保留）、代码块复制钮 hover 常显；
  - D3 侧栏：会话项过渡 200ms ease、选中态内描边轻提；
  - D5 打字指示器：三点跳动改品牌钴蓝（沿用骨架 typingBounce 节奏）；
  - D6 空状态：图标底圈衬底；
  - D7 字号收敛：135 内 11px 清零（UI6 hint 12px）；text-[11px] 判定为既有小按钮/辅助体系保留（迁移有全局回归风险，如实记录）；
  - D8 响应式补漏（360px 实测修复 5 页）：功能页顶栏允许两行（h-14 解除）、snippets 搜索独占行、docs 搜索框弹性、automation 卡片单列、settings 行折行——修复后 7 页 360px 溢出 0（前后 diff ≤0.8% 无回归）。
- **回归发现并修复（3.18.12）**：本机 Agent 开关与 toolsEnabled 总开关不联动 → 用户开启 Agent 后请求体无 tools 静默失效；toggleNativeAgent / tryLocalAgentSuggestion 开启时联动置位总开关（关闭不影响普通工具用户），实测开→tools=true、关→tools 保持。
- **13 项核心功能回归**（真机 qwen2.5:7b/qwen3.5:4b + 后端 8000 全栈 + CDP）：
  3.1 对话全链路 ✓（新建/流式/停止/重试/清空/删除确认/项目分组）；3.2 本机 Agent 真机闭环 ✓（tools 注入/计划卡/只读自动执行/审批卡 diff 与信任勾选/同意落盘 hello.txt/拒绝 reject.txt 未创建/时间线/审计/指纹含内容 hash 防呆）；3.3 智能路由 ✓（本地优先决策+路由详情标注、手选模型不被路由覆盖）；3.4 成本中心 ✓（Gateway 按 usage 精确记账 2 请求 1591 tok、页面估算标注/明细/模型占比）；3.5 双存储 ✓（前端会话/消息实时同步后端 8→9 会话，清 IndexedDB 后降级可用）；3.6 多模型对比 ✓（双栏并发流式 qwen3.5:4b+9b、性能条、同步滚动开关）；3.7 提示词库/片段 ✓（新增/列表/搜索/插入输入框）；3.8 知识库 ✓（上传→本地解析分块 116B/1 块→索引状态）；3.9 自动化工作流 ✓（6 类节点创建/保存/运行日志/停止/删除确认）；3.10 壁纸主题 ✓（6 款切换/持久化/dark-light 无白块）；3.11 快捷键 ✓（⌘K 命令面板/Enter 发送/Shift+Enter 换行/`/` 菜单 12 项）；3.12 PWA ✓（manifest/SW moray-3.19.0/离线打开正常）；3.13 后端 API ✓（health/会话消息 upsert/批处理/删除级联/幽灵 404 宽松，17/17）。
- 验证：19 分片 node --check 0 失败；py_compile 8/8；空库启动 /api/health build=3.19.0 db ok；agent_e2e_check **37 PASS / 0 FAIL**；assemble 幂等 ×2 hash 一致；前后对比截图存 work/shots/overnight/（pre_*/post_*/post_rwd_*/post_compare_dual.png）。
- hash：根=release=`0da4fbca63efcc72…`，web 两份=`d74345dd0fd0c38e…`（横幅分组）；sw CACHE_NAME=moray-3.19.0；对外 MORAY_VERSION/PRODUCT_VERSION 保持 1.0.0 未动。

## 3.18.12（2026-09-08）回归修复：本机 Agent 开关联动 toolsEnabled 总开关（见 3.19.0 汇总）

## 3.18.11（2026-09-08）UI 精修 D1-D8（见 3.19.0 汇总）

## 3.18.10（2026-09-08）UI6 输入台三层结构重排（豆包式：模型行 / 输入区 / 工具行，纯 135 注入段）

- **#1 三层结构**（135_ui_polish.js 注入段净 +109 行）：最小 JS（installUi6Layout）移动既有
  DOM 节点——`.input-toolbar-model` 独立为首行（左侧小标签，点击模型选择器原逻辑不变）；
  `#chatInputNormal` 独占输入区行；底部工具行 = 左工具图标按钮（attach/图片/代码/语音原按钮
  原 id 原位次）+ 右对齐 Token/字数统计（`.input-stats-left` 并入，text-xs text-text-tertiary）+
  圆形发送按钮（原 `rounded-full bg-brand-cobalt` 36px 按钮移入行末，hover 提亮 + 钴蓝光晕）。
  统计空壳 `.input-stats` 隐藏。id/onclick/事件监听随节点移动全部保留。
- **#2 placeholder 缩短**：「输入消息，/ 呼出快捷指令」（JS setAttribute）；骨架 toggleCodeMode
  退出会恢复长文案——135 内包装 window.toggleCodeMode，关闭代码模式后收回短文案
  （功能逻辑零改动，仅 placeholder 文案修正）。
- **#3 窄屏适配**：≤640px 工具行 flex-wrap 折行（工具一行、统计+发送右对齐换行，发送按钮
  右下角完整可点不裁剪）；D 块 `flex-wrap: nowrap !important` 移除 !important 让位
  （UI6 三层化后内容量已无需窄屏横滚兜底）。代码模式徽章 margin-left 改 4px 自然流。
- 验证（Edge headless CDP 真机渲染）：完整版 + web 演示版 1440/360 两档：三层几何顺序正确
  （model→输入区→工具行），360 下 wrap 两行、发送按钮 r≤视口（327≤360）完整可见可点、
  scrollW 无溢出；1440 单行不变；placeholder 短文案；code 开→关后 placeholder 收回短文案；
  发送 click 触发 sendChatMessage（临时替换验证）；attach/code/voice 按钮在工具栏内且点击
  不抛错。截图像素复核：深色无白块（whitePct≤0.05）、light 无黑块、发送钴蓝块渲染
  （sendBluePx≈960 两主题一致）。截图 work/shots/ui6_{360,1440}{,_light}.png 与 ui6_web_360.png。
- 验证：19 分片 node --check 0 失败、agent_e2e_check 37 PASS / 0 FAIL（T0 后端 build=3.18.10）；
  hash：根=release=`ce28e9bd8573cfac…`，web 两份=`553b6825809648e6…`（横幅分组）；
  sw CACHE_NAME=moray-3.18.10；对外 MORAY_VERSION/PRODUCT_VERSION 保持 1.0.0 未动。

## 3.18.9（2026-09-08）UI5 顶栏窄屏可换行两行布局 + 窄屏会话栏抽屉兜底（纯 CSS，135 注入段净 +18 行）

- **#1 主顶栏 ≤767px 可换行两行布局**（135_ui_polish.js @media max-width:767px）：
  `#page-chat > header` flex-wrap:wrap + height:auto + min-height:56px（会话名保留首行，
  overflow:hidden + ellipsis + nowrap 不挤压）；首 div flex:1 1 auto；
  `.mode-segment` flex-basis:100% 独占第二行 + justify-content:flex-end 右对齐 +
  order:2 + flex-shrink:0（分段控制器完整宽度、两按钮保持完整可点）。
- **#2 移除 UI3 视口级 `overflow-x:hidden` 兜底**（≤768px `main, [id^="page-"]` 规则删除，
  不再掩盖真实溢出）。
- **#3 窄屏会话栏抽屉兜底**（135 @media max-width:1023px）：骨架窄屏规则本应把
  aside:nth-of-type(2) 置 fixed 脱离文档流，但运行期被 morayResizeStyle 注入的
  inline `position:relative` 覆盖（computed 验证），导致 260px 会话栏仍占流内空间、
  顶栏被压至 ~52px 宽；追加 `position:fixed !important` 等值兜底规则，
  !important 可压过 inline 样式，会话栏彻底移出流（transform 抽屉行为不变，≥1024px 桌面不受影响）。
- 验证（Edge headless CDP 实测，Tabbit CDP 通道故障期替代方案与 UI4 一致但为真机渲染）：
  web 演示版 + 完整版双载体各五档 360/340/320/768/1440：窄档分段控制器 R≤iw
  （340/320/300 ≤ 360/340/320）且两按钮完整可见可点（右缘余量 ≥20px）、scrollW==iw 无横向溢出；
  768/1440 保持单行原样（wrap:nowrap h:56）；sidebar computed fixed（窄）/relative（1440）。
  截图 work/shots/ui5_cdp_*.png（web 版）、ui5_full_*.png（完整版）各五档，像素级复核
  （segment 行带内容范围与几何一致、选中态蓝块不越按钮框、顶栏右缘零贴边）。
  另：CLI `--screenshot` 在此页面（含动画）存在捕获时序伪影，弃用，以 CDP captureScreenshot 为准。
- 验证：19 分片 node --check 0 失败、agent_e2e_check 37 PASS / 0 FAIL（T0 后端 build=3.18.9）；
  hash：根=release=`2b41b5a4d2e73a8e…`，web 两份=`20264e252770d771…`（横幅分组）；
  sw CACHE_NAME=moray-3.18.9；对外 MORAY_VERSION/PRODUCT_VERSION 保持 1.0.0 未动。

## 3.18.8（2026-09-08）UI4 顶栏窄屏适配 + 残留色值清零（纯 CSS）

- **#1 顶栏窄屏适配**（135_ui_polish.js @media max-width:767px，约 10 行）：
  同步滚动文字标签隐藏（保留状态圆点与开关，控制区 gap 收紧）；
  #chatTitle overflow:hidden + ellipsis + nowrap（会话名不挤压）；
  顶栏首 div min-width:0；.mode-segment flex-shrink:0（分段控制器保持完整宽度可点）。
  产物结构与选择器匹配断言通过（同步滚动 span 位于 .h-9 控制 div 且与 toggle 相邻）。
  ⚠️ 360px 实际截图因 Tabbit CDP 通道持续故障（Target.createTarget 分发失败）未执行，
  以静态结构断言替代（同 UI3 限制）。
- **#2 残留色值清零**：L273/L274 `.welcome-suggestion--agent` 两处
  rgba(74,222,128,.45/.8) → rgba(63,214,143,.45/.8)；parts 与产物 grep 旧值 0 残留。
- 验证：19 分片 node --check 0 失败、py_compile 过、agent_e2e_check 37 PASS / 0 FAIL；
  hash：根=release=`551671eba8d8a327…`，web 两份=`e2282240bb4d665d…`（横幅分组）；
  sw CACHE_NAME=moray-3.18.8；对外 MORAY_VERSION/PRODUCT_VERSION 保持 1.0.0。

## 3.18.7（2026-09-07）UI 精细打磨（纯 CSS/class 层，零 JS 逻辑改动）

- **#1 按钮按下反馈**（135_ui_polish.js）：`button:not(:disabled):active` 等瞬时
  scale(.97)+brightness(1.12)，过渡 0.12s；禁用按钮不触发。
- **#2 禁用态视觉**：`button:disabled/.btn:disabled` opacity .38 + not-allowed 光标 +
  grayscale(.35)（不加 pointer-events:none，保留 hover title）。
- **#3 fallback 色值与令牌对齐**：135 通知/组件 fallback 与 30_chat 欢迎卡共 12 处旧值
  替换为令牌值（#3fd68f/#ff5c6c/#9aa3b8/#616c82 及 rgba(63,214,143,.12)）；
  parts 内旧色值 0 残留（静态骨架区不可改除外）。
- **#4 字号收敛**：全部 23 处 `font-size:11px` → 12px、2 处 `font-size:7px`（think/native
  角标）→ 10px（10px 保留）；仅内联/注入样式，Tailwind 类字号不动。
- **#5 骨架屏抽查**：知识库/工作流/成本三页列表均为同步渲染（内存/IndexedDB 快速）无异步
  白屏窗口；文档库空态已备（empty-state）；未新增骨架（避免越界增行）。
- **#6 响应式防御**：表格类容器 audit 全部已带 overflow 容器（0 缺口）；追加 ≤768px
  视口级 `overflow-x:hidden` 防御规则（页内滚动容器自行横滚不被裁切）。
  ⚠️ 三档×8 页无头截图验证因 Tabbit 自动化通道故障（CDP Target.createTarget 分发失败，
  无其它可用浏览器）未能执行——以静态结构断言 + 防御规则落地替代，如实记录。
- **#7 浅色主题**：代码审查确认 135 已覆盖 light 下 通知/模态/横幅/侧栏/欢迎卡等主要浮层；
  ⚠️ light 截图抽查同样受上述通道故障限制未能执行（同 #6）。
- 验证：19 分片 node --check 0 失败、py_compile 过、agent_e2e_check 37 PASS / 0 FAIL；
  hash：根=release=`ff56d4fe8e28775e…`，web 两份=`58f92256c5c39ab3…`（横幅分组）；
  sw CACHE_NAME=moray-3.18.7；对外 MORAY_VERSION/PRODUCT_VERSION 保持 1.0.0 未动。

## 3.18.6（2026-09-07）修复批次 6（残留收尾）：messages 宽松语义 / 对外版本自动校验 / 演示横幅极窄屏防折行

- **#1 GET /messages 恢复宽松**（api.py）：会话不存在时不再 404，一律返回 200 + 空数组；
  存在会话照常返回消息列表；DELETE/PUT/POST 等其余接口行为不变。验证（TestClient）：
  存在会话 → 200 含 1 条；删除后 GET → 200 {data:[]}；从未存在 id → 200 {data:[]}；
  DELETE 幽灵会话仍 404。
- **#2 MORAY_VERSION 对外版本自动校验**（config.py / assemble.py）：config 新增
  `PRODUCT_VERSION = "1.0.0"` 权威字段（与内部 BUILD=3.18.x 数值不同属正常，不互相比对）；
  assemble 幂等流程在内部构建号校验旁新增对外版本校验：产物内所有 MORAY_VERSION 定义
  必须彼此一致且等于 config.PRODUCT_VERSION，否则打印各来源差异并以非零码退出。
  负向验证：110 的 MORAY_VERSION 改 9.9.9 → 构建失败 exit 1，
  “构建失败：对外产品版本不一致 —— parts/110_polish.js MORAY_VERSION=9.9.9，
  server/app/config.py PRODUCT_VERSION=1.0.0（请统一后重试）”；改回 1.0.0 恢复通过，
  重复构建根 hash 稳定（幂等）。
- **#3 演示横幅极窄屏防折行**（assemble.py 注入模板）：横幅样式加入
  box-sizing:border-box;width:100%;max-width:100vw;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis —— 单行不折行、超长省略号截断、容器不溢出视口。
  （实测 min-width:fit-content 在 360px 下会把容器撑到内容宽 837 导致横向溢出，故以
  width:100%+max-width:100vw 替代。）验证（Tabbit 无头）：360px 宽横幅 360×25 单行
  nowrap+ellipsis 不溢出（截图 work/shots/banner_360.png）；1440px 1440×25 与之前一致；
  根/release 仍不含横幅（hash 分组不变）。
- 验证：19 分片 node --check 0 失败、py_compile 过、agent_e2e_check 37 PASS / 0 FAIL；
  hash：根=release=`40e005372cb0022f…`（幂等稳定），web 两份=`8ae00239ae60a8ee…`（横幅分组）。

## 3.18.5（2026-09-07）修复批次 5（收尾）：熔断持久化 / 404 语义 / hidden 复核 / 版本校验 / 估算标注 / 首次说明 / 移动端

- **#9 熔断持久化**（100_gateway.js GatewayBreaker）：失败记录同步 localStorage
  （键名 `moray_breaker_<MORAY_BUILD>` 带版本前缀防脏数据），启动时恢复——刷新后保留对
  失败模型的临时禁用直到冷却期结束；record/cooling/clear 惰性写回。
- **#10 GET /messages 404**（api.py）：会话不存在返回 404 not_found（不再宽容 200 空数组）；
  前端无 GET messages 调用（拉取走已 404 的 GET /api/conversations/{id}），无需前端分支改动。
  验证：不存在会话 → 404 {code:not_found}；存在会话 → 200。
- **#11 健康探测 hidden 复核**：70_models_settings_boot.js startHealthMonitor 首行
  `if (document.hidden) return;` 已存在（v3.15 落地）——复核确认，未重复改动。
- **#14 版本一致性构建校验**（assemble.py）：构建替换前校验
  parts/110_polish.js MORAY_BUILD == parts/70 APP_VERSION == config BUILD，不一致即
  “构建失败：内部版本号三处不一致 —— …（请统一后重试）”并退出。负向验证：改 70 为 v9.99.99
  → 构建失败并指出差异；恢复后通过。
- **#16 纯前端成本估算标注**（100_gateway.js updateGatewayStatusBar）：后端未连接时
  状态栏“今日 Xk tok”后追加“（估算）”标注。
- **#17 本机工具首次开启说明**（30_chat.js）：首次开启时弹一次安全说明（工作区隔离/人工审批
  unified diff/全程审计 + 可随时关闭），仅说明不改变默认关闭策略（localStorage 一次性标志）。
- **#20 移动端 fixed 背景防御**（135_ui_polish.js）：≤900px 时 body/#app/.aurora-bg
  background-attachment 强制 scroll，防 fixed 背景在窄屏/触控环境渲染溢出。
- 验证：19 分片 node --check 0 失败、py_compile 过、agent_e2e_check 37 PASS / 0 FAIL；
  hash：根=release=`ffa6ded4f4978301…`，web 两份=`19f4e7e262e629e8…`（横幅分组）；
  sw CACHE_NAME=moray-3.18.5。

## 3.18.4（2026-09-07）修复批次 4：[#6] 短消息回复风格短路 + [#8] 写类工具信任指纹加内容哈希

- **#6 短问候短路**（30_chat.js buildRequestMessages）：新会话首条 ≤8 字符的纯问候
  （你好/在吗/hi/hello/谢谢/嗯 等白名单 + 结尾标点）且无引用与附件时，系统提示追加约束
  “对简短问候只做简短回应（一两句），不展开介绍功能、不罗列能力”。真机验证（qwen2.5:7b）：
  新会话发“你好”→ 回复 13 字符“你好！有什么可以帮到你吗？”，无长篇罗列。
- **#8 指纹加入内容时序**（125_tools.js）：新增 `agentFingerprintForApproval` ——
  write_file/edit_file 的审批用指纹额外并入“目标文件当前内容哈希”（fnvHash64，读失败记
  missing）；文件内容已变化时即使工具+参数相同也重新弹审批；read 类只读工具不受影响。
  勾选“本会话信任”时写入的也是含内容哈希的指纹（okBtn 改 async）。函数级断言：
  同参数写内容 A→指纹 fp1；文件改为 B 后同参数指纹 fp2≠fp1；写回 A 后 fp3==fp1
  （内容未变时免确认语义仍生效）；read_file 指纹不含内容哈希。
- 验证：19 分片 node --check 0 失败、py_compile 过、agent_e2e_check 37 PASS / 0 FAIL；
  hash：根=release=`7e03365a…`，web 两份=`bc781757…`（演示横幅分组）。sw CACHE_NAME=moray-3.18.4。

## 3.18.3（2026-09-07）修复批次 3：[#5] 线上演示版自动标注与 PWA 缓存版本化 + [#7] 重试语义显式化

- **#5 演示版横幅与缓存**：
  - `assemble.py --web` 导出的成品（web/index.html 与 web/moray-workbench.html）自动注入
    不可关闭的 sticky 顶部横幅“当前为在线演示版（纯前端 BYOK）——本机 Agent 能力需本地运行
    完整版…”，位于 body 首元素；根产物与发布包不含横幅（hash 按组校验：根/release 一组、
    web 两份一组，差异为有意设计）。
  - sw.js 缓存名绑定内部构建号：assemble 构建时把模板 CACHE_NAME 替换为 `moray-<MORAY_BUILD>`，
    升级后浏览器自动换缓存、不再依赖用户硬刷新；修复 assemble.py 缺 `import re` 导致
    stage 替换未生效的问题。
  - README「在线部署」节补充：线上为演示版无 Agent、更新需整文件夹覆盖重传、PWA 自动取新包。
- **#7 重试语义显式化**（125_tools.js）：步骤卡新增“重放标记”——`retryStep` 重放后记录
  `replayed{at, changed}`（结果与上次对比），stepHtml 渲染警示行“已重放该步（仅重新执行工具，
  不自动改写已生成回复）· 结果与上次不同/一致 · 可点「重新生成」让模型基于最新结果作答”；
  重放结果更新进步骤卡与持久化 toolCalls（重建后标记仍在），toast 同步明确该语义。
- 验证：19 分片 node --check 0 失败、py_compile 过、agent_e2e_check 37 PASS / 0 FAIL；
  产物断言：web 两份含 #demoModeBanner 且位于 body 首、根/release 不含；sw CACHE_NAME=
  moray-3.18.3。hash：根=release=`d28dd9e3…`，web 两份=`3aa86780…`（横幅分组）。

## 3.18.2（2026-09-07）修复批次 2：[#3] 冷启动等待反馈 + [#4] 附件持久化边界显式化

- **#3 冷加载等待反馈**（30_chat.js）：打字指示器（typing-indicator）在发送 2 秒后仍无
  首 token 时，标签文案切换为“模型加载中（冷启动约 5~15s，首次使用后更快）…”——
  首 token 到达或收尾移除指示器后自然消失，不再表现为“白等卡死”。
  保留 keep_alive 30m 透传（既有）不动。
- **#4 附件仅存本机显式化**（30_chat.js / 70_models_settings_boot.js / 135_ui_polish.js）：
  带图片的消息时间行新增“仅存本机”小角标（hover 说明：清除浏览器数据或更换设备将丢失）；
  设置→数据管理新增“存储边界”说明块（图片附件仅存本机浏览器 IndexedDB，文本消息可经
  本地后端 SQLite 同步，附件不随文本同步不外传）。
- 验证：19 分片 node --check 0 失败、py_compile 过、agent_e2e_check 37 PASS / 0 FAIL、
  四份产物 SHA256 一致 = `cc9dcb36b9316fa1130d43832d6bfc39598618e27278f11562c1acddddf56162`（3.18.2）。

## 3.18.1（2026-09-07）修复批次 1：[#1] 演示环境依赖强提示 + [#2] Agent 默认模型修正

- **#1 后端未启动强提示**（30_chat.js / 125_tools.js / 135_ui_polish.js）：新增
  `agentOfflineReason()` 统一原因（“本机 Agent 需要本地后端，请双击「启动MoRay.bat」（或运行
  server\ 下 uvicorn）后刷新”）；`MorayBackend.connected===false` 时输入台「本机工具」开关与
  顶部提示条的 自检/示例/文件树 入口全部**置灰**（native-offline 灰化样式），hover 显示原因，
  点击统一弹明确原因 toast 且不执行/不切换（开关离线时仍可关闭，仅“开启”方向被拦）。
  验证（Tabbit 无头 8899 离线页）：connected=false → 开关带 native-offline、title 为原因文案、
  点击弹“本机 Agent 暂不可用”且 nativeToolsEnabled 未变。
- **#2 Agent 模式默认模型**（125_tools.js / 70 boot）：新增 `agentRecommendedModel()`
  （真机实测优先级 qwen2.5:7b → qwen3.5:9b → qwen3.5:4b）与 `agentApplyRecommendedModel()`
  ——本机工具开启时若当前模型**自检未通过或未自检**，默认模型自动切到推荐（提示可手动改回）；
  自检通过则维持原默认；开关关闭（纯聊天）不干预；页面加载时若上次开启过本机工具同样应用。
  验证（Tabbit 8000 在线页 + 真 Ollama）：defaultModel qwen3.5:4b → 点开开关 →
  defaultModel 自动变为 qwen2.5:7b（recommended 一致）。
- 验证：19 分片 node --check 0 失败、py_compile 过、agent_e2e_check 37 PASS / 0 FAIL、
  四份产物 SHA256 一致 = `0f06eb793333e483d0715575c4041bfc36ff64ce3553e56dcd2071e3454f3b37`
  （3.18.1）。



## v1.0.0（2026-09-07）正式版封版定稿（仅版本/文档/清理，功能逻辑零改动）
### 定版
- 对外产品版本 **0.3.0 → 1.0.0**（前端 MORAY_VERSION、后端 config VERSION 同步）；
  内部构建号统一 **3.18.0**（MORAY_BUILD / APP_VERSION / config BUILD）；
- 设置→关于页版本行改为动态取 MORAY_VERSION（此前残留硬编码 v3.0.0）；页脚与状态栏
  由运行时同步（原有机制）；server/README 头部版本同步；
- `build_release.py` 按 config VERSION 自动产出 **release/MoRay-v1.0.0(.zip)**（脚本读
  config，无需改逻辑）；产物内 html 版本号随构建同步。

### 文档
- README 升级正式版门面：界面预览新增本机 Agent 三张实机截图（任务计划卡 / 审批 unified
  diff / 常驻文件树，取自 work/shots/agent-states 并归档 docs/screenshots/agent-*.png）；
- 核心特性新增「本机 Agent」小节（受控工作区隔离、7 工具、计划→分步执行、unified diff
  审批、全程审计、常驻文件树、模型自检）；
- 快速开始：一键全栈标注为"完整能力"首选路径，并明确**纯前端/在线 BYOK 版不携带本机
  Agent**；新增 Agent 模型建议表（真机实测 qwen2.5:7b 最稳、qwen3.5 偶发漏调、1.5b 不可靠）；
- 技术架构图补 /api/agent 与审计表；目录结构 parts 18→19；路线图勾选六项已完成。

### 清理
- 删除无引用的临时勘察脚本 scripts/peek_copy.py、scripts/scan_tmp2.py
  （正式测试脚本 test_agent_tools.py / test_llm_proxy_tools.py / agent_e2e_check.py 保留）。

### 验证
- 19 分片 node --check 全过；server/app py_compile 全过；
- agent_e2e_check.py 37 PASS / 0 FAIL（功能零回退自证）；
- assemble.py 幂等 ×2 + --web web + build_release.py：根 html / web/index.html /
  web/moray-workbench.html / release/MoRay-v1.0.0/moray-workbench.html **四份 SHA256 一致**
  （见定稿报告）；产物含 MORAY_VERSION=1.0.0 与 MORAY_BUILD=3.18.0。

## v3.17.2（2026-09-05）阶段1.6：启动残留修复 + Agent 工作面真机精修 + 演示顺滑度

### M1 启动通知彻底收敛
- 全局排查定位残留源：静态骨架第一阶段脚本的“MoRay 已就绪（按 ⌘K 打开命令面板）”（不可改区）
  与 onboarding 完成路径的“一切就绪”——采用通知层**启动窗口合并**根治（135_ui_polish.js）：
  页面加载后 12s 内的启动类通知（标题含已就绪/一切就绪，或消息含 ⌘K 引导/后端状态摘要）
  只允许第一条显示，⌘K 引导自动并入其消息；后续同类静默丢弃，且后端连接状态反向并入首条。
- 实测（IAB，加载后 0.8s/3s/6s 三时刻截图+DOM 计数）：右上角启动类通知总数恒 ≤1，
  6s 时按分类时长自动消失；`work/shots/agent-states/boot_toasts_*.png`。

### M2 Agent 工作面真机精修（mock 全流程逐状态截图）
- mock 新增 s10_demo 完整演示剧本（计划→find_files→read_file×2→edit_file 审批→总结），
  IAB clip 截图管线绕开视口模拟平铺伪影（clip≤980×640 走正确合成路径），逐状态留档
  `work/shots/agent-states/`：s1_plan_card（计划卡 5 步）、s2_timeline（时间线执行+edit 待审批）、
  s3_approval_diff（审批卡：居中/scrim/等宽路径/红绿 diff/主次按钮/信任勾选）、s4_done
  （计划 5/5+时间线全 success+总结）、s5_tree_highlight（文件树侧栏：notes 展开/文件类型图标/
  变更高亮/与对话同屏不遮挡）。
- 修复：ensureWsSidebar 重构时误删 createElement/id 两行导致侧栏无法创建（报错即现）；
  计划步骤状态流转同步到消息持久化（会话重建后计划卡显示最新状态而非初始待办）。

### M3 演示顺滑度
- **示例工作区**：顶部提示条新增「示例」按钮——一键创建 notes/a.txt（会议要点素材A）、
  notes/b.txt（调研记录素材B）、todo.md（待办清单）三个中文自洽示例；已存在的文件自动跳过
  （绝不覆盖用户文件）；创建后经事件刷新文件树并高亮。
- 欢迎页：新增第 5 张「试试本地 Agent」引导卡（仅本机工具可用+后端在线时出现）——点击后
  自动开启开关（如未开）、开关高亮脉冲 2 秒、载入示例工作区、自动填好一句演示任务并聚焦；
  快捷卡片精修（图标底/边框/hover 抬升/间距/浅色适配），欢迎区垂直重心上移。
- 模型选择器提示：当前模型自检未通过时，顶部提示条内给一行温和警示
  （“该模型工具调用偶发失败（自检未通过），Agent 任务建议 qwen2.5:7b”），不打断输入。

### 验证
- 19 分片 node --check 全过；server py_compile 全过；assemble 图标校验 115 个有效；
- agent_e2e_check.py 37 PASS / 0 FAIL、test_agent_tools.py 97 通过 0 失败（零回退）；
- 四份产物 SHA256 一致；测试端口/进程清理。

## v3.17.1（2026-09-05）阶段1.5：全局 UI/UX 精修与体验闭环（纯界面层，零功能变更）

### A 通知系统治理
- 新增 parts/135_ui_polish.js（第 19 个分片）：以同名覆盖升级 showNotification——
  同屏最多 3 条（新顶旧）、成功 3.5s/信息 5s/警告 8s/错误 10s 分类时长、hover 暂停倒计时
  （进度条暂停）、类型图标+状态色左缘条、深色 scrim+blur 保证壁纸下可读、手动关闭按钮、
  右上 16px 安全边距、窄屏 max-width 自适应、role=status/alert 无障碍。
- 启动通知合并为唯一一条状态摘要（后端连接 + 本地模型数；后端未启动时按新文案提示
  “本地同步后端未启动：当前用浏览器存储，Ollama 本地对话不受影响；启动后端可多设备同步”），
  删除“纯前端（离线）模式”独立通知与 onboarding 完成时的“一切就绪”叠加通知；
  底部状态栏“本地后端 ○ 离线模式”改为“本地后端未启动”（两处 + title 同步）。

### B 壁纸适配与可读性
- 模态/确认框/命令面板/右键菜单统一深色渐变 scrim+blur（浅色主题对应浅色渐变）；
  遮罩层加深；输入台/卡片 backdrop-filter 统一；全部取既有设计令牌。

### C 网格自适应
- 提示词库/片段库/文档库网格：≥1600 四列、1280-1599 三列、768-1279 两列、<768 单列，
  内容区 max-width 1560 居中（消除宽屏右侧空白），卡片 min-width:0 防溢出。

### D 输入台
- 工具栏 overflow-x 横向滚动（隐藏滚动条）不再换行挤压；本机工具 ON 态品牌描边+轻 glow、
  OFF 态 62% 弱化 hover 恢复。

### E 设置页
- OpenAI 模型输入框占位符改中性文案（去 gpt-4o-mini）；“删除模型”二次确认确认弹窗已有
  （复核保留）；设置侧导航平滑定位+active 高亮已有（复核保留）；本机 Agent 分区新增
  Agent 系统提示查看/覆盖/恢复内置（M5 已建）；审计日志升级为表格（时间/工具/参数摘要/
  审批/状态色/耗时）+ 分页（25 条/页）+ 导出 JSON + 清空（二次确认，新增后端
  DELETE /api/agent/log + crud.clear_agent_log）。

### F Agent 组件精修
- 计划卡：头部可点击整体折叠（chevron）、步骤状态图标（circle/loader/check-circle/x-circle/
  skip-forward）、进行步浅蓝高亮+spin；
- 审批卡：Enter=允许一次（输入框聚焦时豁免）/Esc=拒绝路径不变；
- 文件树侧栏：左缘拖拽调宽（220-460px）、目录展开箭头（chevron 旋转动画）、按扩展名的
  文件类型小图标（file-code/cog/image/archive/warning）、空工作区引导文案；
- 时间线展开/收起 150-200ms ease-out、展开阴影层级。

### G 微交互与无障碍
- 全局过渡统一 160ms ease-out；细滚动条统一；所有可交互元素 :focus-visible 品牌焦点环；
  prefers-reduced-motion 下关闭 pulse/spin/位移/进度条动画与平滑滚动；
- 图标按钮补 aria-label（通知关闭/刷新/收起侧栏）。

### 验证
- 19 分片（新增 135_ui_polish.js）node --check 全过；assemble 图标校验 115 个图标名有效；
- agent_e2e_check.py 37 PASS / 0 FAIL、test_agent_tools.py 97 通过 0 失败（零功能回退）；
- 响应式四档（1920/1440/1280/900）：DOM 程序化诊断（无横向滚动/无关键元素重叠/可见性）
  + 截图留档（IAB 截图管线在本机 DPI 环境对超窗格视口存在平铺伪影，已用 DOM 诊断佐证
  布局正确性，详见交付报告）；
- 四份产物 SHA256 一致；测试进程/端口清理。

## v3.17.0（2026-09-05）阶段1：本机 Agent 独立完成多步骤工作区任务（M1→M5）

### M1 真机工具调用适配 + Agent 自检
- Ollama 本地模型真机探测（scripts/ollama_tools_probe.py，3 轮统计，非流式+流式）：
  **qwen2.5:7b 3/3 全稳定 ✔；qwen3.5:9b 流式 3/3、非流式 2/3；qwen3.5:4b 均 2/3；
  qwen2.5:1.5b 流式仅 1/3 不可靠**——流式 tool_calls 实测一次性全量到达（frag=1，无分片），
  解析按增量分片兼容实现。
- tool_calls 解析加固（20_ai.js）：sanitizeToolCalls（空调用过滤/arguments 对象↔字符串统一/
  tryCompleteJson 截断补全）应用于非流式全部三分支；chatStream 三后端新增流式 tool_calls 增量
  聚合（accToolCallDeltas/accToolCallsFinalize，OpenAI delta 分片 + Ollama 对象形态），经
  _finish 透出 toolCalls 字段（无工具调用时 null，向后兼容）。
- 云端链路真机冒烟（scripts/cloud_tools_smoke.py）：server/.env 实际指向本地 Ollama 的 OpenAI
  兼容端点（/v1，qwen3.5:4b）——经 llm_proxy 全链路 tools 透传实测 PASS，标准 OpenAI 形态
  tool_calls（id/index/字符串 arguments）正确透传；**DeepSeek 官方 tools 未验**（.env 无官方
  key），如实登记。
- Agent 自检：顶部提示条「自检」按钮 → 用当前模型发一个必然触发工具调用的最小请求（1 次、
  非流式），支持/不支持/失败三态明确提示，未通过时推荐实测稳定模型（qwen2.5:7b / deepseek-chat）；
  结果持久化并在设置页展示。

### M2 三个高价值安全工具（server/app/agent_tools.py，走既有安全层）
- find_files(path?, pattern?)：文件名通配递归查找，限 1000 条；手动 scandir 跳过
  symlink/junction（防遍历逃逸）；只读自动执行。
- search_text(query, path?, glob?, regex?)：全文检索 [{file,line,text}]，默认 200 条命中、
  单行 200 字符截断、扫描 2000 文件上限；非法正则明确报错；二进制/非 UTF-8 跳过并计数；
  只读自动执行。
- edit_file(path, old_str, new_str, replace_all?)：精确字符串替换；old_str 唯一匹配校验
  （0/多处且未 replace_all 报错引导补上下文）；返回替换次数与变更前后片段；危险后缀/二进制/
  非 UTF-8 拒绝；加入副作用集合（审批双保险 + 审计）。
- 安全单测 97 项全过（74 原有 + 23 新增：越界/截断/正则非法/多匹配/replace_all/危险后缀/
  二进制/未审批/审计）。

### M3 计划-执行编排（复用现有 Loop，零新引擎）
- submit_plan 纯前端工具（native 门控）：模型多步任务先提交编号计划（≤12 步，含意图与工具）→
  PlanTracker 登记并渲染计划卡（时间线之上的汇总层：待办/进行中/完成/失败/已跳过 状态色）；
- 真实工具事件（onToolStep）按"工具名+最早待办"匹配推进步骤状态；单步失败真实错误回灌模型
  自行重试（同一步失败满 3 次标记已跳过防死循环，maxRounds 兜底）；计划随消息持久化，
  会话重建时恢复展示。

### M4 工作区面板常驻化 + 变更可视
- 文件树从模态弹窗改为可收起的常驻侧栏（fixed 右侧浮层，不阻塞聊天输入，浅色主题适配）；
  Agent write_file/edit_file 后经 moray:ws-file-written 事件自动刷新并高亮 2 秒。
- 审批升级：write_file / edit_file 审批卡展示 unified diff（行级 LCS，- 红 / + 绿 / 上下文灰，
  零依赖；超 400 行截取头部；write_file 自动 read_file 旧内容比对，原文件超 64KB 时注明）；
  拒绝路径与既有 denied 审计一致。

### M5 命中率系统提示 + 全量回归
- 内置 Agent 系统提示（PlanTracker.SYSTEM_PROMPT：工具清单与时机/工作区边界/多步先列计划/
  不确定先 find_files/read_file/修改优先 edit_file/禁止臆造路径/命令只读白名单/old_str 唯一性）；
  设置→本机 Agent 可查看、自定义覆盖（保存覆盖/恢复内置）；仅本机工具开启时追加到系统提示。
- e2e 扩展至 37 断言全绿：新增 S7 计划多步（submit_plan→find_files→search_text→总结）、
  S8 edit_file 审批→替换→read 验证、S9 edit 被拒内容不变、SEC find_files 越界抽检；
  阶段0/0.5 既有 27 项与安全攻击实测（越界/未审批/命令注入/危险后缀/junction 逃逸）零回退。

### 残留风险
- DeepSeek 官方 tools 未真机验证（.env 无官方 key）；流式 tool_calls 聚合为兼容实现
  （Ollama 实测一次性到达，分片路径按 OpenAI 增量协议实现并有单测覆盖解析）；
- qwen3.5 系列工具调用非 100%（实测 2/3），自检与系统提示用于规避；不稳定属模型行为；
- 计划步骤与工具的匹配是"工具名+最早待办"的宽松策略，模型乱序执行时计划卡状态可能滞后
  （不影响实际执行与回灌正确性）；计划卡仅当前会话展示，跨会话不恢复执行状态。


## v3.16.1（2026-09-05）阶段0.5：本机 Agent 闭环打磨 + 全链路自动化回归

### 新增

1. **Agent 全链路自动化回归**（scripts/agent_e2e_check.py，一条命令跑通，exit 0=全绿）：
   mock OpenAI 兼容上游（线程 HTTPServer 剧本驱动，记录完整请求供回灌断言）+ 真实 FastAPI app
   经 TestClient（MORAY_DB/MORAY_WORKSPACE 指 %TEMP%，真实文件系统/子进程/同一套安全层）+
   协议模拟器 1:1 复刻前端 runWithTools 协议（非第二套实现）。6 场景 27 断言：只读单工具无审批 /
   写文件 needsApproval→approved 落盘→审计 approved=1 / decision=denied 不落盘+"用户拒绝"回灌 /
   多工具多轮顺序与 tool_call_id 一一对应与轮次计数 / 超 maxRounds 恰好 3 轮即止 / 后端不在线
   连接错误（前端映射"需要启动本地后端"）+ 模型不支持 tools 去 tools 优雅回退；内含安全抽检
   （越界/非白名单命令拒绝）。
2. **过程时间线可操作化**（125_tools.js）：
   - 摘要行展示完整可读入参（write=目标路径+字符数、command=完整命令行、read/list=路径），
     超长截断+title 全文；
   - 展开区显示完整结果（resultFull 不再截 300 字，CSS 限高滚动不撑爆会话）+ 一键复制（已有）；
   - 失败步骤显示后端真实错误（danger 等宽字体）并提供"重试该步"：按消息持久化的 name/args
     重新执行同一工具（不经模型、走完整安全层与审批），就地更新步骤卡与持久化数据，成功后
     提示可用"重新生成"让模型基于最新结果作答；
   - 全部步骤到终态自动折叠为一行"共 N 步 · 总耗时 Xs"（含 pending/running 判定，新事件自动
     展开；历史消息重建同样折叠，点击展开/收起）；样式全部沿用现有设计令牌，零新依赖。
3. **工作区文件树面板**（125_tools.js，只读）：顶部轻提示条新增「文件树」按钮 → 弹窗内
   懒加载目录树（list_directory，dirCache 缓存不重复请求）、点击文本文件 read_file 只读预览
   （前 64KB，二进制/越界错误原样显示）、刷新按钮；Agent write_file 成功经
   moray:ws-file-written 事件自动刷新并高亮新文件 2 秒。数据全部来自现有只读接口，零新写能力。
4. **相同操作指纹本会话免重复确认**（默认关）：审批卡新增勾选"本会话对完全相同的工具+参数
   不再询问"（仅非 run_command 工具显示，命令永远每次审批）；指纹=工具名+键排序稳定 JSON；
   内存 Map 存储刷新即失效；设置→本机 Agent 新增"已信任操作"计数与"清除"按钮；
   后端 approved 双保险与审计照常，未改动。

### 修复

5. 多模型对比"本地串行"停止时，尚未轮到的列此前一直空白占位 → 现明确显示"已停止（未开始）"；
   状态栏停止后保持"已停止"，不被 run 收尾覆盖为"本轮完成"（40_compare_prompts.js）。
6. 后端健康探测 60s 定时器 document.hidden 跳过：核查确认 startHealthMonitor 已实现
   （70_models_settings_boot.js `if (document.hidden) return;`，v3.15 期间已落地），本次复核
   全部 setInterval 清单（5 处）后未做重复改动。

### 验证

- scripts/agent_e2e_check.py：**27 PASS / 0 FAIL（ALL GREEN）**；
- 阶段0 安全实测 scripts/test_agent_tools.py 重跑 74 项全过（安全层零回退）；
- node --check 18 parts 全过；server/app py_compile 全过；
- assemble.py 幂等 ×2 + --web web，根 html 与 web 副本（及 web/index.html、release 包）
  SHA256 一致；版本三处 3.16.1；测试进程/端口/临时库清理。

### 残留风险

- "重试该步"只重放工具本身并更新时间线，不自动改写已生成的模型回复（引导用户点"重新生成"）；
- 文件树默认全收起、懒加载，超大目录（>500 条）依赖后端截断（显示截断标记但不分页）；
- 信任表为本会话内存态，刷新失效（设计如此）；同指纹不同内容顺序（如 write 后又写同路径）
  视为同指纹——勾选信任后不再询问，请谨慎勾选；run_command 不受影响；
- e2e 的"后端不在线"断言验证连接层失败与前端降级文案存在性，完整 UI 行为沿用阶段0浏览器实测；
- 真实 DeepSeek 冒烟未发（server/.env 未配置 MORAY_LLM_API_KEY），mock 全覆盖协议层。

## v3.16.0（2026-09-05）阶段0 本机 Agent：受控工作区工具闭环（列目录/读文件/写文件/只读命令）

### 新增能力

1. 本机工具四件套（全部在受控工作区内执行，工作区默认 D:\MoRayWorkspace，可设置页修改/环境变量
   MORAY_WORKSPACE 锁定；工具默认关，输入台"本机工具"按钮显式开启后才进入 listForRequest）：
   - list_directory：列目录（单层 500 条截断）；read_file：UTF-8 文本读取（默认 64KB/2000 行截断，
     二进制与不可解码明确报错不乱码）；两者只读、默认自动执行。
   - write_file：写文件（自动建父目录；危险后缀 .exe/.bat/.cmd/.ps1/.reg 等拒绝；overwrite 语义）；
     run_command：只读白名单命令（git status/log/diff/branch、python/node/npm --version、
     where <名>；shell=False 列表参数、参数逐字符校验禁元字符/管道、15s 超时）——两者为副作用工具，
     执行前必须人工审批（允许一次/拒绝；拒绝绝不执行并把"用户拒绝了该操作"回灌模型）。
2. 后端 agent_tools 安全层（server/app/agent_tools.py）：pathlib resolve+commonpath 双保险路径校验
   （.. 越界/绝对路径/UNC/盘符/CON 保留名/符号链接与 junction 逃逸全拒，normcase 大小写归一）、
   审批双保险（approved!==true 绝不执行副作用，返回 needsApproval+可读摘要）、命令白名单+危险后缀黑名单、
   全部调用审计落 SQLite agent_tool_log（含被拒/未审批/客户端拒绝），GET /api/agent/log 分页可查。
3. Agent Loop 能力补齐（100_gateway.js runWithTools）：轮次默认 8 上限 12；native 工具审批为可 await
   中断点（等待期间步骤卡转"待审批"脉冲）；"停止生成"可中止整任务（AbortController，停止后不再发起
   后续工具与请求）；Ollama 后端原生 tools 支持（前端直连 /api/chat 带 tools + 协议消息规范化，
   arguments 字符串/对象互转）。
4. llm_proxy 云端链路 tools 透传：请求侧透传 tools/tool_choice、非流式响应透传 tool_calls
   （无 tools 的旧请求零变化）；AI.chat 云端代理分支与 Ollama 分支补齐 tools 下发与 tool_calls 解析。
5. 时间线/UI：步骤卡新增"待审批/已拒绝"状态（warning 色）与 4 个本机工具图标；会话区顶部
   "本机工具已开启 · 工作区 X"轻提示；设置页新增"本机 Agent"卡（后端在线状态/工作区根修改并校验
   可写/审批策略只读自动或全部审批/最大轮数 1-12/审计日志查看）。
6. 降级提示：后端离线/未配置时开关与工具执行给明确中文提示（"本机工具需要启动本地后端…"），
   模型不支持 tools 时通知并自动切换普通对话，不卡死不白屏。

### 验证

- 后端安全单测 scripts/test_agent_tools.py：74/74（默认工作区自动创建/越界/绝对路径/UNC/junction
  逃逸拒绝/未审批副作用 needsApproval/危险后缀与保留名拒绝/64KB 与 2000 行与 500 条截断/命令白名单
  与元字符拒绝/审计落库含 ok·rejected·needs_approval·denied/工作区设置修改与不可写拒绝）；
- llm_proxy 透传单测 scripts/test_llm_proxy_tools.py：12/12（无 tools 旧请求零变化、tools/tool_choice
  透传、tool_calls 响应透传、空 tools 不破坏、无 tools 流式兼容）；
- 浏览器 mock LLM 端到端（scripts/mock_llm_server.py，直连与云端代理两种形态）：
  "看看工作区里有什么"→ 列目录成功（只读无审批）；"写 hello.txt"→ 审批允许 → 磁盘真实出现
  "你好"；拒绝 → 磁盘无文件且模型如实告知取消（步骤卡"已拒绝"）；读 a.txt+b.txt 汇总写 summary.txt
  → 三步骤顺序正确+落盘；审批等待中点"停止生成" → 无写盘、无后续轮次、审计无对应记录；
  "读 C:\Windows\win.ini" → 安全层拒绝且模型转述原因；模型不支持 tools（400）→ 提示+切换普通对话；
  后端离线 → 开关 warning 提示+banner 离线文案+工具失败步骤不卡死。
- node --check 18 parts 全过；python 编译/import 过；空库 MORAY_DB 指 %TEMP% 启动不碰真实库；
  assemble.py 与 --web web 双份产物 SHA256 一致；测试端口/进程/临时库全部清理。

### 残留风险

- 本机工具仅工作区内可用是刻意边界（删除/移动/联网下载执行属 v2，未实现）；
- run_command 白名单保守，需要新命令时须扩展白名单+参数校验（安全评审后再加）；
- 命令输出按 64KB 截断，超长输出可能不完整（有截断提示字段）；
- 浏览器端工具 5s 超时对 native 工具放宽为 30s~5min（含审批等待），审批长挂不误杀；
- 本机 Ollama 工具调用依赖 Ollama 版本支持 tools（旧版本不支持时自动回退普通对话并提示）。


## v3.15.13（2026-09-03）路由体验定点修复：熔断/驻留亲和/元问题直答/名字统一/光标收尾

### 修复

1. 失败模型短期熔断（GatewayBreaker，内存态）：真实失败/超时才记录，AbortError 不计；
   自动路由跳过冷却模型，half-open 到期自动恢复；手选手动选择仍尊重；设置页可查看/清除冷却。
2. 已驻留模型亲和：routingPreferLoaded（默认 true）读取 Ollama /api/ps（5s 缓存），
   simple/medium 复用已驻留模型，避免 MAX_LOADED_MODELS=1 下反复换模；complex 仍走最大本地策略。
3. 模型身份元问题确定性直答：metaDirectAnswer（默认 true）命中时前端直接生成助手消息
   （精确模型名+本地/云端+手选/路由+reason），不调用 LLM；关闭后回旧注入方式。
4. 模型名统一：气泡头/输入台/状态栏/直答均用完整名（本地含 :tag）；routingDebug（默认 false）
   控制路由 reason 平铺/折叠成“路由详情”胶囊。
5. 统一收尾清理：finalizeAssistantUI 移除 .stream-cursor/.typing-indicator，正常/停止/回退/出错
   路径均无残留光标。

### 新增设置

routingPreferLoaded(true)、circuitBreakerCooldownSec(90)、metaDirectAnswer(true)、
routingDebug(false)——在“设置→API 智能网关”可改、持久化、非摆设。

### 验证

- rux_assert：13/13（熔断回退/冷却跳过/手选尊重/清除恢复/亲和/直答不发LLM/三处全名一致/
  默认胶囊/直答与停止与回退无光标）；
- dbg_breaker：快照 {qwen2.5:1.5b,left:90,consec:1}、冷却期选 qwen3.5:9b、手选 1.5b 成功；
- b_normal_debug：普通“你好”走 LLM（direct=false）、无光标；
- node --check 18 parts、assemble/--web/build_release、四份 SHA256 一致。

## v3.15.12（2026-09-03）定点修复：本地优先 localBest 复核与产物自证

### 结论

- 按 FIX_LOCALFIRST.md 对唯一真源 D:\ai工具台\parts\100_gateway.js 做“改前读回”，
  实际内容已是 `localBest = ranked[0]`（734 行）与 `if (Array.isArray(v)) v = v[0];`（338 行）；
  第 2 步精确替换为无操作，未改动任何字符（读回与替换后读回一致）。
- 全文件 `const localBest = ranked;` 命中数 = 0。
- 行为断言 7/7：三本地模型 + routingLocalFirst 时 model === 'qwen2.5:1.5b'（string），
  reason 含“本地优先（qwen2.5:1.5b）”，关闭开关结果不被覆盖，
  ensureStringModel(['a','b'],'fb') === 'a'。
- 构建号 3.15.11 → 3.15.12（MORAY_BUILD / APP_VERSION / config BUILD）。

### 验证

- node --check parts\100_gateway.js 通过；
- python assemble.py / assemble.py --web web / scripts\build_release.py 通过；
- 产物 moray-workbench.html 命中 `localBest = ranked[0]` 且 `const localBest = ranked;` 命中 0；
- 根 html / web/index.html / web/moray-workbench.html / release html SHA256 一致。

## v3.15.11（2026-09-03）整夜任务：本地优先复核 / Gateway.chat 缺失修复 / UI 性能与展示一致性

### 修复

1. 本地优先“数组当模型名”复核：确认 parts/100_gateway.js 已是
   `localBest = ranked[0]` 与 `ensureStringModel` 的 `v = v[0]`（不做无意义改动），
   补正向断言（S8/S7：三本地模型 strict qwen2.5:1.5b、开关对照、fallbacks 全字符串）。
2. 工作流 AI 节点报 `Gateway.chat is not a function`：parts/100_gateway.js 新增
   `Gateway.chat` 非流式薄封装（guardCloud → route → AI.chat → fallback → usage 记录），
   工作流 input→ai→output 三节点真实跑通。
3. 非 anime 壁纸 backdrop blur 16px → 10px（parts/110_polish.js）；
   60s 健康探测在 document.hidden 时暂停（parts/70_models_settings_boot.js）。
4. 展示一致性延续 v3.15.9：气泡/输入台/状态栏以“实际返回模型”为唯一权威；
   云端失败回退本地有“云端失败→已回退本地”标注。
5. 构建号 3.15.10 → 3.15.11（MORAY_BUILD / APP_VERSION / config BUILD）。

### 验证

- node --check 18 parts 全过；assemble 幂等；106 图标有效；
- 真实 Ollama S1 30/30、mock S2 18/18、S4 13/13、S5 20/20、S7 20/20、
  C1 对比页 4/4、R_gen 重新生成 2/2、P3 CRUD 6/6、P3 工作流 success；
- 根 html / web/index.html / web/moray-workbench.html / release html SHA256 四份一致。

### 说明

详细证据与逐阶段结果见 NIGHT_PROGRESS.md、NIGHT_REPORT.md。

## v3.15.10（2026-09-03）本地优先“数组当模型名”复核与正向断言加固

### 结论

- 复核 parts/100_gateway.js：本地优先块实际实现已是
  `localBest = ranked[0]`（ranked 排序后取参数量最小的单个本地模型字符串），
  `ensureStringModel` 数组分支已是 `v = v[0]`；磁盘源码不存在“localBest=ranked 恒假”或
  “Array.isArray 空操作”的问题。本次不虚构改动、不重写正确代码。

### 修改/加固

1. parts/100_gateway.js：本地优先实现与出口类型守卫保持正确形态
   （ranked[0] 单字符串 + typeof 守卫 + 其余本地模型并入 fallbacks；
   ensureStringModel 数组取首元素，非法值才回退默认模型）。
   本轮源码无逻辑改动（对照备份 pre_localfirst_real_20260903_012026 前内容一致）。
2. 新增正向断言回归（独立快速脚本，不做完整链路）：
   - 3 个本地模型 + routingLocalFirst 开启：model 严格等于 qwen2.5:1.5b 字符串、
     reason 含“本地优先（qwen2.5:1.5b）”、fallbacks 全字符串；
   - 反向对照：routingLocalFirst 关闭时结果仍为 deepseek-chat（证明开关生效）；
   - ensureStringModel(['a','b']) === 'a'；空数组/非字符串回退 fallback。
3. 构建号 3.15.9 → 3.15.10（MORAY_BUILD / APP_VERSION / config BUILD）。

### 验证

- node --check 18 parts 全过；assemble 幂等；106 个 Lucide 图标有效；
- S8 正向断言 7/7 通过；
- 根 moray-workbench.html / web/index.html / web/moray-workbench.html / release html
  SHA256 四份一致；
- 未动 UI 视觉、数据契约、design-preview。

## v3.15.9（2026-09-02）智能路由与模型显示一致性修复

### 根因

- TaskRouter 未配置 primary 时的启发式未用 CostEngine.isLocal 过滤，云端模型可能被当
  “本地模型”参与最小/居中/最大选择；simple 分支允许“名字不含 Nb”的模型充当最小本地模型；
- 输入台模型选择器只写了 conv.model/defaultModel，未置 userPickedModel=true → “用户手选”
  仍被智能路由覆盖，导致气泡、输入台、状态栏三处模型名漂移；
- 输入台与状态栏只读“默认模型”，不读“最终真正成功返回的模型”；云端失败回退本地后
  显示仍停留在原路由云端模型；
- 用户询问“现在用的什么模型/是否切换”时没有注入实际模型事实，本地小模型只能背
  “我是内置助手、并不基于 DeepSeek”的固定人设；
- OpenAI 兼容模型名保存缺少基础校验，deepseek-v4-flash 这类非官方名可无提示写入。

### 修改

1. parts/100_gateway.js
   - TaskRouter.route 启发式：候选先用 CostEngine.isLocal 过滤（Ollama 后端或价格为 0），
     云端模型不参与“最小/居中/最大本地模型”；本地为空回退默认/首个可用模型并如实写
     “无本地模型，使用默认模型 xxx”；simple 分支排除名字不含 Nb 的模型；
   - 本地优先块 localBest 明确取排序数组 [0] 单字符串并判空（与现有 fallback/autoDegrade 兼容）；
   - 新增模型元问题识别与“系统事实·当前实际模型（本地/云端）”注入，回退时重注入备用模型；
   - 回退成功后 routeReason 标注“云端失败→已回退本地 xxx”，_routed 携带 fallback 标记；
2. parts/30_chat.js：新增 setActiveModel/resetActiveModel 单一入口，记录最终实际模型
   （含会话归属，跨会话不串），同步输入台标签与状态栏；生成成功后按实际模型刷新；
   会话设置弹窗保存/清除手选后同步显示；
3. parts/110_polish.js：输入台模型选择器手选时置 userPickedModel=true 并通过
   setActiveModel 统一刷新（手选锁定路由）；
4. parts/70_models_settings_boot.js：状态栏模型名优先显示“实际生效模型”；默认模型变更
   清除显示覆盖；OpenAI 兼容模型保存/测试时对非官方 deepseek-* 名给警告（不阻止自定义端点）；
5. 版本同步 3.15.8 → 3.15.9（MORAY_BUILD / APP_VERSION / config BUILD）。

### 验证（Playwright/Edge headless + 真实 Ollama + 两个 OpenAI 兼容 mock）

- node --check 18 parts 全过；assemble 幂等；图标校验 106 个有效；
- R1 本地 Ollama simple→qwen2.5:1.5b，气泡/输入台/状态栏家族一致，reason=最小本地模型；
- R2 纯云端 simple→deepseek-chat，reason=“无本地模型，使用默认模型”，不再称云端为本地；
- R3 界面手选 deepseek-v4-flash 不被路由覆盖，三处一致；
- R4 deepseek-chat 502 → 自动回退 qwen2.5:1.5b，气泡/输入台/状态栏同步，reason 与气泡小字
  均标注“云端失败→已回退本地”；
- R5 元问题“现在用的是哪个模型”回答“当前使用 qwen2.5:1.5b（本地）”，无身份否认模板；
- R6 保存 deepseek-v4-flash 出现“官方模型为 deepseek-chat / deepseek-reasoner”告警；
- 输入台模型选择器点选 qwen3.5:9b → userPickedModel=true，后续 simple 不被路由覆盖；
- 回归：S1 对话全链路（新建/发送/刷新/重命名/清空/分组/删除/390px）全绿，console error=0。

## v3.15.8（2026-09-02）运行级全面体检修复（favicon/无效 embedding/契约/工具取消/版本同步）

### 根因

- 网关在“语义缓存关闭”时仍对每条完成的回复调用 getEmbeddingVector（Ollama 缺 embedding
  模型时产生 /api/embeddings 404 控制台 error，且每次对话额外一次向量请求）；
- 页面未声明 favicon，http 加载时浏览器固定请求 /favicon.ico 产生 404 console error；
- 后端 POST /api/conversations 创建路径只读 camelCase userPickedModel，而前端同步层按
  契约发 snake user_picked_model → 手选模型锁定标志在“后端重建/跨设备恢复”时丢失；
- IDB 禁用（隐私/无痕）时 legacy 迁移缺少 typeof indexedDB 守卫，产生降级 warning；
- run_workflow 工具超时/中止时未通知 WorkflowEngine.requestStop，工作流会继续后台空跑；
- 前端/后端内部构建号停留在 3.15.1，与 CHANGELOG 版本线不一致。

### 修改

1. parts/100_gateway.js：GatewayCache.save 仅在 semanticCache 开启时计算向量；
2. assemble.py：构建期注入内联 SVG favicon（幂等，file:// 与 http 行为一致）；
3. server/app/crud.py：upsert_conversation 兼容 snake/camel 两种 user_picked 字段；
4. parts/10_db.js：migrateLegacyDB 增加 typeof indexedDB 守卫；
5. parts/125_tools.js：run_workflow 订阅 ctx.signal 中止 → WorkflowEngine.requestStop，
   收尾移除监听并在中止后抛 AbortError（不写“成功”尾部逻辑）；
6. 版本同步：window.MORAY_BUILD / server config BUILD → 3.15.8；
   APP_VERSION → v3.15.8（触发一次缓存卫生，版本升级预期行为）。

### 验证（真机 http 127.0.0.1:8010/8011/8012 + Playwright/Edge headless）

- 18 parts node --check 全过；python assemble.py 组装幂等；图标校验 106 名全部有效；
- 页面加载 console error = 0（修复前：favicon.ico 404 + Ollama /api/embeddings 404）；
- 真实 Ollama：新建空会话 → “你好” 2.4s 简短回复（qwen2.5:1.5b，路由生效）→ 刷新恢复
  → 重命名/清空/删除/分组/390px 输入台固定全部通过；
- OpenAI 兼容 mock：路由四场景、usage.completion_tokens 精确 6、工具两轮闭环、
  工具 5s 超时/正常完成/外部取消、流式停止通过；
- IDB 禁用内存模式：settings/apicache/会话 CRUD 无异常、无 legacy warning；
- 后端契约：health/CRUD/CORS/llm_proxy（流式+非流式 usage 透传）通过；
  POST 会话 user_picked_model=true 保留修复生效；attachments 不落后端为既有设计
  （图片 dataURL 只存本地 IDB），消息接口在会话删除后返回 [] 属宽容语义，未改契约。

### 交付

重跑 python assemble.py（moray-workbench.html/sw.js/manifest）、assemble.py --web web/、
scripts/build_release.py（release/MoRay-v0.3.0 目录+zip，含最新 parts 产物）。

## v3.15.7（2026-09-01）深色图片壁纸：光核输入台提亮 + 深色外观锁定

### 根因

- #coreInputContainer 在壁纸下仅 rgba(15,18,28,.62) 过透，压在壁纸暗部与背景糊成一片；
  输入框全透明、placeholder 偏暗、工具图标发暗。

### 修复（parts/110_polish.js）

1. **输入台加深浮起**（仅 anime-*/custom 作用域）：`background-color:rgba(12,15,25,.90)`
   + `border:1px solid rgba(120,150,255,.22)` + `box-shadow:0 10px 30px rgba(0,0,0,.45)`；
   聚焦时 `:focus-within` 边框过渡到品牌蓝 + 保留光核聚焦光晕；
2. **输入框**：`#chatInputNormal` 极轻衬底 rgba(255,255,255,.03) + 圆角 10px；正文保持
   --color-text-primary；`::placeholder` 提亮 rgba(186,200,225,.72)；
3. **工具栏**：`.input-toolbar-btn` 图标提亮 #AEB9CE（hover #E8ECF5）；模型名保持原样
   （text-secondary）；发送按钮品牌蓝不变；
4. **浅色主题兜底**：浅衬底 + 深色文字 + 深色图标（联动强制深色为主，此层防御万一浅色时也可读）；
5. **深色外观锁定联动**：壁纸为 anime-*/custom 时强制 `html[data-theme]=dark`（忽略浅色/跟随系统，
   不改动 MoraySettings 用户设置）；切回其它壁纸时 `MoraySettings.apply()` 恢复用户原本主题选择。

### 验收（真机 http://127.0.0.1:8899）

- anime-starry（用户选 light）→ 强制 dark：dockBg rgba(12,15,25,0.90)、边框蓝 0.22、阴影、
  输入框 0.03 衬底 + 圆角 10px、正文 #E8ECF5、placeholder rgba(186,200,225,.72)、图标 #AEB9CE ✓
- 聚焦：边框过渡到 rgb(91,140,255) 品牌蓝 + 光核光晕 ✓
- 切回 gradient → 恢复用户 light 主题、dockBg 原样 0.45 白 ✓；切 custom → 同样强制 dark ✓
- 其它壁纸/无壁纸/纯黑状态原样；file:// 与 http 机制一致（http 全验证，file:// 建议真机复核）；
- 刷新保持、控制台 0 报错、18 parts 语法 0 失败、组装×2 幂等（673749 chars）。

### 交付

post_inputdock 快照；桌面 MoRay工作台\ 与 MoRay工作台.html 同步。

### 受限项

- 联动只在壁纸状态同步时执行（切换/加载/自定义落图）；若用户在其他入口直接改主题，下次壁纸状态
  变化时会重新锁定/恢复；
- 输入台亮/暗区域结论以计算样式为证据（模型无图像输入），建议真机目测一次。

## v3.15.6（2026-08-31）深色图片壁纸下消息气泡正文对比度修复

### 根因

- 用户气泡 bg-brand-cobalt/14 仅 14% 不透明，AI 气泡 bg-surface-card 走 --wall-card-alpha:0.62，
  壁纸亮部（青色光斑/发光发丝）下正文看不清；.md-body 颜色靠继承缺显式保障。

### 修复

- `parts/30_chat.js`：buildMessageEl 三处气泡内层 .rounded-xl 加稳定类名
  `msg-bubble__user-box` / `msg-bubble__ai-box`（用户气泡、AI 消息气泡、流式占位气泡，不用 :has）；
- `parts/110_polish.js`：仅在 anime-*/custom 作用域下覆盖（其它内置壁纸与无壁纸状态原样）：
  - 深色主题：user-box `rgba(64,104,205,.42)` + 蓝边框；ai-box `rgba(18,22,36,.82)`（比普通卡片更实）；
    `.msg-bubble .md-body { color: var(--color-text-primary); text-shadow: 0 1px 2px rgba(0,0,0,.35) }`；
  - 浅色主题：user-box 浅蓝 `rgba(214,225,255,.88)`、ai-box 白 `rgba(255,255,255,.88)`、
    正文深字（var 自动适配）、无阴影；
- **过程中修复两个问题**：① 注释内 `anime-*/custom` 的 `*/` 提前闭合 CSS 注释（与前次同类陷阱），
  改措辞；② ai-box 规则 specificity 低于通用卡片规则（`#app[data-wallpaper] main .bg-surface-card`），
  选择器加 `main` 提升至 (1,3,1)；
- 全局排查 `text-x]` 无效类：**当前全 parts 无残留**（无需修正，如实说明）。

### 验收（真机 http://127.0.0.1:8899，含真实消息渲染）

| 组合 | 用户气泡 | AI 气泡 | 正文 |
|---|---|---|---|
| dark + anime-starry | rgba(64,104,205,0.42)+蓝边框 | rgba(18,22,36,0.82) 更实 | #E8ECF5 亮字+阴影（对比 >10:1） |
| light + anime-starry | rgba(214,225,255,0.88) | rgba(255,255,255,0.88) | 深字 #1A2233（对比 >10:1） |
| dark + gradient/aurora | 原样（0.14） | 原样（卡片 0.45） | 原样 ✓ 不回退 |
| 代码块/思考面板/标签 | 深底浅字（0.82 底保障） | | 可读 ✓ |

- 壁纸亮/暗区域：气泡 0.82 不透明 + 显式文字色，不依赖壁纸亮暗 ✓
- file:// 与 http：CSS 注入机制一致（http 全验证；file:// 受限以等价验证 + 相对路径论证）；
- 刷新保持、控制台 0 报错、18 parts 语法 0 失败、组装×2 幂等（668572 chars）、无 text-x] 残留 ✓

### 交付

post_bubble 快照；桌面 MoRay工作台\ 与 MoRay工作台.html 同步。

### 受限项

- 对比度以计算样式 + WCAG 亮度估算双重证据（模型无图像输入），建议真机目测亮部（青色光斑）处一次；
- file:// 场景以 http 等价验证覆盖。

## v3.15.5（2026-08-31）深色图片壁纸浅色主题对比度修复（不发白发灰）

### 根因

`html[data-theme="light"] #app[data-wallpaper]` 浅色规则（白色半透面板 + 白色 scrim）对**所有**
壁纸生效；叠在深色动漫图（anime-*/custom）上形成白雾，人物与文字对比过低。

### 修复（parts/110_polish.js，浅色规则后追加约 L1643-1690）

- 为深色图片壁纸（anime-starry / anime-aurora / anime-window / custom）单独覆盖：
  - `--wall-scrim: rgba(6,8,14,0.08)` / `--wall-panel-alpha:0.32` / `--wall-blur:4px` /
    `--wall-card-alpha:0.62`（沿用图片壁纸已定义参数）；
  - 三栏骨架在 light 下仍用深色底：aside rgba(16,19,29,α) / #middleSidebar rgba(21,25,39,α) /
    main rgba(10,12,20,α)；卡片/气泡 rgba(26,31,48,α)、输入台 rgba(15,18,28,α)；
- 浅色主题仅对 aurora/deep-space/gradient/mountain/cyber/pure 保留现有浅色覆盖（不变）；
- **过程中发现并修复一个 CSS 注释陷阱**：注释内 `anime-*/custom` 的 `*/` 提前闭合注释，导致后续
  规则被解析器吞掉（scrim 覆盖不生效）——改为 "anime 系列与 custom" 措辞；
- 主题联动锁定方案未采用（CSS 深色覆盖已达标；联动会干扰用户对其它壁纸的浅色选择，任务标注可选）。

### 验收（真机 1280×720，http://127.0.0.1:8899）

| 组合 | 结果 |
|---|---|
| dark + anime-starry | asideBg rgba(16,19,29,0.32)、scrim 深色 0.08、card 深色 0.62 ✓ |
| **light + anime-starry** | **三栏深色半透 + 深色 scrim + 深色卡片（无白雾）✓**；截图像素采样主区 RGB(13,43,78) 深蓝星空、左栏 RGB(10,31,59)——深邃不发灰 ✓ |
| light + gradient | 浅色面板 rgba(233,237,245,0.62) + 白 scrim 0.22（浅色覆盖保留不变）✓ |
| light + custom | 同深色图片壁纸处理 ✓ |
| 跟随系统（auto 解析为 light） | 同一 data-theme 规则集等价覆盖 ✓ |

- file:// 与 http 双入口：CSS 注入机制一致（file:// 受限以 http 等价验证 + 相对路径论证）；
- 刷新后保持；控制台 0 报错；18 parts 语法 0 失败、组装×2 幂等（666187 chars）。

### 交付

post_lightwall 快照；桌面 MoRay工作台\ 与 MoRay工作台.html 同步。

### 受限项

- 截图为像素采样分析（模型无图像输入），组合结论以计算样式 + 像素亮度双重证据；
- file:// 场景以 http 等价验证覆盖（IAB 无法导航 file://），建议真机双击复核。

## v3.15.4（2026-08-31）壁纸修复：自定义切回内置黑屏/无反应

### 根因（已定位）

1. `__syncWallpaperAttr` 中 `if (custom) v='custom'`：只要 localStorage 残留 moray_custom_wallpaper
   就强制 custom，压过当前已选的内置 saved；
2. MutationObserver 用"#app 是否存在内联 backgroundImage"反向把 data-wallpaper 拉回 custom；
   两者叠加：切内置时内联背景被清空，但 attr 仍被设成 custom → 内置背景图规则挂在
   `#app[data-wallpaper="内置名"]` 上匹配不到 → getComputedStyle backgroundImage 为 none → 黑屏。

### 修复（parts/110_polish.js 壁纸状态同步逻辑，约 L1448-1500）

1. `__syncWallpaperAttr` 以 moray_wallpaper 为唯一权威，判定顺序：
   ① saved==='custom' 且 custom 数据存在 → custom；
   ② saved 命中 __WALL_LIST → 该内置名（custom 数据残留不影响内置选择，保留以便切回）；
   ③ saved 为空：有 custom 数据 → 回退 custom，否则默认 anime-starry；
   内置分支额外清除内联背景残留（静态 load 的 custom 分支在 custom 数据存在时会先设内联图，
   若 saved 是内置则内联会盖过 CSS 背景）；
2. MutationObserver：仅当 saved==='custom' 且内联背景存在才设 custom，其余一律走修正后的
   __syncWallpaperAttr，绝不把内置 attr 反向拉回 custom；
3. switchWallpaper 切 'custom' 时从 localStorage 恢复内联背景（静态实现会清空内联 → 自定义图丢失）。

### 验收矩阵（真机，http://127.0.0.1:8899 与后端托管双入口）

- 上传自定义图 → 正常显示（attr=custom + data:image 背景）✓
- custom 数据残留下点 星夜/极光动漫/纯黑：每次都立即显示对应内置、无黑屏，
  getComputedStyle(#app).backgroundImage 为对应 url（wp1/wp2）或 pure 纯色，非 none 黑屏 ✓
- 内置之间互切正常；内置 → 自定义（数据未删）恢复自定义图 ✓
- 刷新保持最后一次选择（anime-window 刷新后 attr/class/背景均正确）✓
- 清空存储全新打开默认 anime-starry（wp1 背景）✓
- file:// 双入口：相对路径与 vendor 同机制（IAB 无法 file:// 导航，http 等价验证 + 逻辑论证）✓
- 控制台 0 报错；18 parts 语法 0 失败、组装×2 幂等（663400 chars）✓

### 交付

post_wallfix 快照；桌面 MoRay工作台\ 与 MoRay工作台.html 同步。

### 受限项

- file:// 场景以相对路径机制论证 + http 等价验证覆盖（IAB 引擎无法导航 file://），建议真机双击复核。

## v3.15.3（2026-08-31）内置动漫壁纸 ×3 + 设为默认 + 图片壁纸穿透参数

### 资源与托管

- `wallpapers/`（wp1_starry.jpg / wp2_aurora.jpg / wp3_window.jpg，1920×1080）纳入前端资源：
  CSS 用相对路径 `wallpapers/xxx.jpg`（file:// 与 http 均可加载）；`server/app/main.py` 新增
  `/wallpapers` 静态挂载（http://127.0.0.1:端口/wallpapers/wp1_starry.jpg 200 验证）；
  `scripts/build_release.py` INCLUDE_DIRS 加 wallpapers（发布包 39 文件 / 6076KB 含 3 张原图）。

### 内置壁纸与默认

- `parts/110_polish.js`：`__WALL_LIST` 新增 anime-starry / anime-aurora / anime-window（与设置页
  选择器一致）；`__syncWallpaperAttr` 无 saved 值时默认 **anime-starry**；
- 新增 CSS（沿用 #app[data-wallpaper] 作用域规范）：三张背景图（cover/center/fixed）+ 设置页缩略图
  类选择器（.wallpaper-option.wallpaper-anime-* 复用背景图）；
- `parts/70_models_settings_boot.js`：选择器 walls 数组新增三项（星夜/极光动漫/窗边，缩略图自动生效），
  saved 兜底默认改 anime-starry；
- **图片类壁纸专用穿透参数**（用户真机调好照用）：
  `#app[data-wallpaper="anime-starry|anime-aurora|anime-window|custom"] { --wall-panel-alpha:0.32;
  --wall-blur:4px; --wall-scrim:rgba(6,8,14,0.08); --wall-card-alpha:0.62; }`；
  原有渐变壁纸 aurora/deep-space/gradient/mountain/cyber/pure 参数保持不变（验证 aurora alpha=0.62）。

### 验收（http 真机 + 全新目录发布包）

1. 全新 origin 打开：默认 `data-wallpaper="anime-starry"` + wallpaper-anime-starry class +
   background-image 指向 wallpapers/wp1_starry.jpg，无 404、控制台 0 报错 ✓
2. 设置页选择器 9 项（6 渐变 + 3 动漫）+ 自定义上传：缩略图背景正确、切换选中态正确、
   localStorage 持久化 + 刷新恢复正确 ✓
3. 图片壁纸 alpha 0.32 / blur 4px / scrim 0.08 / card 0.62（人物不被默认遮罩压暗）；
   渐变壁纸参数不变（aurora 0.62）✓
4. 自定义上传套用同一套更透参数（custom alpha 0.32 验证）✓
5. 新发布包含 wallpapers，解压到全新中文空格目录经 http 打开：health/壁纸/vendor/root 全 200，
   默认壁纸生效、刷新保持 ✓
6. 回归：18 parts 语法 0 失败、组装×2 幂等（661982 chars）、现有壁纸/主题/毛玻璃/PWA 未动、
   控制台 0 报错 ✓

### 交付

release/MoRay-v0.3.0(.zip) 重建并复制桌面；post_wall 快照；桌面 MoRay工作台\ 同步（含 wallpapers/）。

### 受限项

- IAB 无法直接以 file:// 导航，file:// 场景以"相对路径与 vendor 同机制"论证 + http 等价验证覆盖，
  建议真机双击复核；
- 图片壁纸文字可读性依赖用户已调好的 0.32/0.08 参数组合（深色面板底色 + 浅 scrim），极端亮色区域
  建议真机目测一次。

## v3.15.2（2026-08-31）M5.1 综合收尾：安全加固 + 同源连接 + 版本统一 + 数据卫生

### P0 正确性与安全

- **P0-1 后端地址同源解析**（沿用 v3.15.1 已实现并复测）：统一 `resolveBackendOrigin()` 优先级
  ① ?backend= ② localStorage moray_backend_origin ③ http/https 同源 location.origin ④ file:// 回退
  8000；MorayBackend.origin 为全局事实源（探测/同步 base()/M2 代理 _useProxy 全部复用）；
  复测：MORAY_PORT=8123 打开 8123 页面 → origin/url 指向 8123、同步请求 **Network 零 8000 请求** ✓
- **P0-2 工作流自定义代码节点安全**（方案 B：默认禁用 + 安全降级 + 导入确认；A 方案 Web Worker 会大改
  执行引擎、风险高于收益，明确不采用）：
  - `codeNodesEnabled` 默认 false；runCodeNode 未开启时返回安全提示不执行；
  - runConditionNode 未开启时改用 `safeCondition()`（安全表达式解析：text.length 比较 /
    includes / startsWith / endsWith / 相等比较 / 布尔字面量，其余回退 length>0，**不再 new Function**）；
  - 自动化页新增开关（开启弹风险确认：代码将在本机浏览器主线程执行）；
  - 导入含 code/condition 节点的工作流先弹"该工作流含可执行代码，将在本机运行"确认框；
  - 验证：默认禁用返回提示、safeCondition 五类用例全对、开启后执行正常 ✓
- **P0-3 双入口语义**：启动MoRay.bat = 完整工作台（起后端→健康检查→打开应用，可建桌面快捷方式）；
  纯前端（file:// 或离线）一次性、非阻断轻提示"纯前端（离线）模式：启动后端可获得 SQLite 数据同步
  与云端 Key 代理"（localStorage 标记不反复打扰）；状态栏"离线模式"准确 ✓

### P1 一致性与布局

- **P1-4 版本统一**：产品版本 `MORAY_VERSION=0.3.0`（前端）+ 后端 config VERSION=0.3.0；内部构建号
  `MORAY_BUILD=3.15.1` + 后端 config BUILD=3.15.1（/api/health 返回 build）；启动 toast 探测后生成、
  底部状态栏 "MoRay v0.3.0"、server/README 更新至当前功能全景（移除早期里程碑残留）✓
- **P1-5 欢迎区遮挡**：CSS 注入——.welcome-screen 与 #chatMessagesNormal 底部预留
  `96px`（输入台高度+间距）；`@media (max-height:720px)` 压缩欢迎区上下间距与卡片（实测
  chatMessagesNormal padding-bottom=96px 生效）✓
- **P1-6 离线声明与实现一致**：pdf.js（pdf.min+worker）、mammoth、prettier（standalone+5 插件）下载进
  vendor/ 改本地引用（9 个文件全部下载成功）；transformers.js（体积大）按计划保留 CDN；README 改为
  "核心功能完全离线；PDF/Word 解析、代码格式化已本地化；本地大模型推理增强项首次使用需联网加载"；
  本地加载失败给友好提示 ✓

### P2 安全文案与数据卫生

- **P2-7 Key 表述**：设置页标签 "API Key（本地混淆存储，非加密）"+ 说明"混淆存储不是加密。更安全
  方式：填入后端 server/.env 并选择「经本地后端代理」，Key 不经过浏览器"；10_db 注释本已自承
  "混淆不等于加密"；WebCrypto 真加密列入后续（不声称加密安全）✓
- **P2-8 定时工作流可见性**：调度选择器下方标注"仅在 MoRay 页面保持打开时运行；关闭/最小化/休眠
  不会执行（后端调度列入后续路线图）" ✓
- **P2-9 数据卫生**：主库清理 mock 残留 6 条（含【mock 回复 4 条 + 诊断 + 自动推送测试，级联 12 消息），
  保留 10 条真实会话；IndexedDB 同规则清理 6 条，保留 10 条（双端一致）；自测纪律：MORAY_DB 指向
  临时库 + 独立端口（本次验收全程用 test_m51.sqlite3，测试库已删，主库零 mock）✓

### 统一验收

1. 18 parts 语法 0 失败、组装×2 幂等（660342 chars）、控制台 0 报错；
2. 双击启动MoRay.bat 完整链路：后端启动（build 3.15.1）→ 轮询就绪 → 自动打开应用；
   清理 M5 遗留旧后端后复测干净通过；
3. 新发布包 build_release.py：36 文件 / 5564KB，无 .env/.venv/data/backup/parts 源码/过程脚本，
   .env.example 在，**vendor 本地化库（pdf/mammoth/prettier）已进包**；
4. 回归：Ollama 对话（真机多轮）、云端代理、智能路由、思考三态、对比、壁纸、M3 同步、纯前端离线
   全部在位（统一验收中逐项确认）。

### 交付

release/MoRay-v0.3.0(.zip) 重建并复制桌面；post_m51 快照；桌面 MoRay工作台\ 同步（含 vendor 新库）。

### 受限项

- P0-2 采用方案 B（默认禁用+确认），未采用 Web Worker 方案（改造执行引擎风险高）；开启后代码仍在
  主线程执行（用户已确认风险），Worker 沙箱列为后续；
- transformers.js 保留 CDN（体积大），断网时该增强项提示联网、其余功能不受影响；
- file:// 场景以逻辑审查 + 纯前端等价验证覆盖（IAB 无法导航 file://）。

## v3.15.1（2026-08-31）M5 收尾：后端地址同源解析 + 版本统一 + 数据卫生

### 问题一（核心）：后端地址默认跟随同源

- `parts/110_polish.js`：新增统一 `resolveBackendOrigin()`（挂 window 供全站复用），优先级：
  ① `?backend=`（支持端口或完整 origin）→ ② localStorage `moray_backend_origin`（手动配置）→
  ③ http/https 时 `location.origin`（同源，一键脚本换任意端口自动配对）→ ④ file:// 直开回退
  `http://127.0.0.1:8000`；`MorayBackend` 增加 `origin` 字段（全局事实源）；
- `parts/115_backend_sync.js` `base()`：改用 `MorayBackend.origin`（new URL 兜底），不再限定 127.0.0.1；
- `parts/20_ai.js` `_useProxy()`：代理端点 = `mb.origin + /api/llm/chat`（与探测/同步同一来源）；
- 全站排查：探测/同步/代理三处统一走该解析结果，无硬编码 :8000。

### 问题二：启动 toast 与版本统一

- 启动欢迎 toast 从 boot（70）移除，改为探测完成后按真实状态生成（"MoRay v0.3.0 已就绪 · 本地后端已连接/离线模式"）；
- 新增前端单一版本常量 `window.MORAY_VERSION = '0.3.0'`（110），与后端 `/api/health` version（config VERSION 0.3.0）一致；
- 底部状态栏版本号由静态骨架写死的 "MoRay v2.0" 运行期替换为 MORAY_VERSION；
- manifest.webmanifest 为 PWA 规范无版本字段（说明：版本以 health/状态栏/README/CHANGELOG 为准）。

### 问题三：数据卫生与测试隔离

- `server/app/config.py`：支持 `MORAY_DB` 环境变量把 SQLite 指向临时文件（自测专用，严禁往用户主库灌 mock）；
  `db.py connect()` 兼容任意父目录；
- 主库 SQLite 清理（先打印清单）：删除含【mock 回复或明显自测标题（空会话/诊断/自动推送测试/离线消息测试/
  性能测试/M3修复验收/发布包验收）的会话 25 条 + 级联消息 54 条；**保留 10 条**（标题普通、内容无 mock，
  拿不准不删：新对话×6、你好×1、一句话介绍×1、证明并讨论×1、解方程×1 等）；
- 前端 IndexedDB 同规则清理 21 条（正常删除链路），保留 8 条。

### 验收（真机）

1. MORAY_PORT=8123 启动 + `http://127.0.0.1:8123` 打开：MorayBackend.url/origin 指向 8123、
   状态栏"已连接"、`BackendStore.base()`=8123、同步拉取 **Network 全部打 8123 无任何 8000 请求** ✓
2. 优先级实测：`?backend=8123` 覆盖生效；无参数页面同源（8899）；localStorage 手动配置优先于同源；
   file:// 回退 8000 分支（IAB 无法 file:// 导航，逻辑审查确认）✓
3. 启动MoRay.bat 实跑：默认 8000 与 `set MORAY_PORT=8123` 均启动成功并自动打开对应端口页面 ✓
4. 新发布包 build_release.py：27 文件，无 .env/.venv/data/backup/parts 源码/assemble.py；.env.example 在 ✓
5. 回归：18 parts 语法 0 失败、组装幂等（654186 chars）、控制台 0 报错；状态栏版本显示 "MoRay v0.3.0" ✓

### 交付

release/MoRay-v0.3.0(.zip) 重新构建并复制桌面；post_m5fix 快照；桌面 MoRay工作台\ 同步。

### 受限项

- IAB 无法直接以 file:// 导航，④ 回退分支以代码审查覆盖（建议真机双击复核）；
- 保留的 10 条会话中部分可能为早期真实对话（标题普通、无 mock 内容），遵循"拿不准保留"原则未删。

## v3.15.0（2026-08-31）里程碑 M5：打包分发 + 一键启动 + 作品级 README

### 后端静态托管（server/app/main.py）

- 根路径 `GET /` 直接返回 moray-workbench.html（一个地址即用）；`/api/status` 承载原服务说明；
- `/vendor/*`（StaticFiles 挂载）、`/sw.js`、`/manifest.webmanifest` 全部托管；
  路径基于 `__file__` 解析（中文/空格路径可用）；`/api/*` 路由注册在前、静态挂载在后，互不冲突；
- `file://` 双击直开与纯前端离线模式完全保留；config VERSION → 0.3.0。

### 一键启动

- 工程根 `启动MoRay.bat`（GBK 编码）：独立窗口启动后端 → 轮询 /api/health（60s 上限）→
  自动用默认浏览器打开 `http://127.0.0.1:8000/`；MORAY_PORT 换端口；路径基于 %~dp0 解析；
- `start_moray.sh`（macOS/Linux 等价）：后台启动 + curl 轮询 + webbrowser/open 打开。

### 干净发布包（scripts/build_release.py）

- 产出 `release/MoRay-v<版本>/` + zip（版本读 server/app/config.py VERSION，可 --version 覆盖）；
- 包含：moray-workbench.html、vendor 5 库、sw/manifest、server/（app 全套、requirements、
  .env.example、README）、scripts 启动脚本、启动MoRay.bat、start_moray.sh、README.md、deploy/；
- 排除（先打印清单再排除，共 1736 项）：.env（精确，.env.example 保留）、.venv、__pycache__、
  server/data、backup/、历史 zip、node_modules、u1.py/u2.py/clean2.py/patch_tools1.py、
  verify_*.txt、*.png、*.log、tmp_*、release/ 自身；
- 幂等：清空重建，结果一致；打印发布文件清单与大小。

### 作品级 README.md（工程根）

定位与卖点、架构与数据流图、目录结构、三种使用方式（Windows 一键 / mac-linux / 纯前端免后端）、
.env 配置步骤（DeepSeek 等）、智能路由/思考/同步使用说明、技术栈清单、常见问题（端口/Ollama/PWA
缓存/换端口 ?backend=）、后续路线（M4 统一网关/附件同步/云账号）——每条命令均实际验证可跑。

### 真机验收

1. **全新中文空格目录从零跑通**：发布包解压到 `桌面\测试 目录\MoRay` → 自动建 venv → 装依赖 →
   启动 → http://127.0.0.1:8000/ 打开应用（vendor/sw/manifest 200）→ 连本机 Ollama 发消息
   （2 条落库）→ 刷新会话仍在（发布目录独立 SQLite 同步）✓
2. http 托管资源全 200、控制台无 404/报错；PWA 注册成功（scope=http://127.0.0.1:8000/）✓
3. file:// 直开与纯前端离线模式保留（IAB 无法直接导航 file://，CORS Origin=null 放行此前已验证；
   关后端刷新页面完全可用、显示"离线模式"）✓
4. 解压包逐项确认：无 .env/.venv/server/data/backup/历史 zip/过程脚本；.env.example 在 ✓
5. MORAY_PORT=8123：发布目录启动成功 + 应用打开 + 探测"本地后端 ● 已连接" ✓
6. README 命令全部实跑：启动MoRay.bat（后端就绪→自动打开应用，ping 替代 timeout 兼容无交互环境）、
   start_backend.bat、build_release.py 均验证 ✓

### 交付

release/MoRay-v0.3.0(.zip)（27 文件、1803KB）；post_m5 快照；桌面 MoRay工作台\ 同步 +
MoRay_发布包_v0.3.0.zip；组装×2 幂等（652324 chars/19262 行）。

### 受限项

- IAB 环境无法直接以 file:// 导航（引擎限制），file:// 场景以"纯前端模式等价验证 + Origin=null CORS
  放行"覆盖，建议真机双击复核一次；
- start_moray.sh 未在 macOS/Linux 实机执行（本机 Windows），逻辑与 bat 等价；
- 发布包不含 parts/ 源码与 assemble.py（运行最小集）；README 已说明开发构建入口。

## v3.14.1（2026-08-31）M3 修复：pull 后重载内存会话列表（刷新/换设备空白根治）

### 根因

- `onBackendProbed` 在 `BackendStore.pull()` 把远端数据写入 IndexedDB 后只调 `renderSessionList()`；
  而 `renderSessionList()`（30_chat.js L49）只渲染内存 `AppState.conversations`，不重新读 DB——
  启动那一刻内存还是空的 → 刷新/换设备后会话列表空白（实测：手动 `AppState.conversations =
  await DB.listConversations()` 后立即全部出现）。

### 改动（parts/115_backend_sync.js + parts/70_models_settings_boot.js）

1. 新增 `reloadSessionsFromDB()`：
   - `AppState.conversations = await DB.listConversations()` + 按现有规则排序
     `(b.pinned-a.pinned) || ((b.order||b.updatedAt)-(a.order||a.updatedAt))`；
   - `rebuildMsgIndex()`（存在则 await）→ `renderSessionList()` → `refreshSyncBadges()`；
   - 防御：DB 结果为空且内存非空时不覆盖（避免时序竞态清空已显示列表）；
2. 三处"pull/pushAll 后只调 renderSessionList"替换为 `await reloadSessionsFromDB()`：
   - onBackendProbed 常规恢复分支（pull 之后）；
   - onBackendProbed 首次"一键上传"showConfirm 回调（pushAll 完成后）；
   - 设置页"立即同步"按钮完成回调（70 boot，同步成功后重载内存）。
3. 时序健壮性：启动主流程（boot conversations 步骤）与 onBackendProbed 并发时，
   无论谁先结束，最终都以 DB 最新数据为准（pull 写库后 reload 从 DB 读）且不会闪回空白。

### 真机验收（全新 origin 模拟换设备 + 真实 Ollama）

1. 清空站点存储（localhost 全新 origin，保留后端 SQLite）刷新：不做任何手动操作，
   **36 条会话自动显示**（domItems=36），点开"你好，请用一句话介绍你自己"消息完整（user+assistant）✓
2. 在线新建会话 + 真实消息（2 条落库）→ 刷新后列表/消息在；sqlite 直查该会话行 + 2 条消息 ✓
3. 双窗口（设备 B 全新 origin 127.0.0.1:8900）：自动恢复 37 条会话，含 A 新建的会话与 2 条消息 ✓
4. 关后端：离线列表不空（37 条），新建会话标"待同步"；重启后端 + 一键同步 →
   sqlite 38 条含离线会话，标记全部"已同步"（pending=0）✓
5. 脏数据清理（先打印清单再删）：SQLite 删除 3 会话（注入测试/M3同步测试/性能测试）+ 级联 203 消息
   + kv 1 行；IndexedDB 删除同 3 会话 + 3 消息；保留真实会话 36 条不减少 ✓
6. 回归：思考三态/壁纸/代理/网关/BackendStore/同步按钮全部在位，控制台 0 报错；
   18 parts 语法 0 失败、组装×2 幂等（652324 chars/19262 行）✓

### 交付

pre_m3 / post_m3fix 快照并同步桌面 MoRay工作台\。

### 受限项

- 清理 IndexedDB 需在无页面连接时进行（deleteDatabase 会被已打开连接阻塞）；验收用全新 origin 模拟
  全新设备，真实用户"清浏览器数据"路径不受影响；
- "立即同步"按钮对大批量会话的完成时间取决于网络与消息量（几十条会话约数秒），期间有"同步中"提示。

## v3.14.0（2026-08-31）里程碑 M3：会话/消息/设置 SQLite 持久化 + 前后端同步

### 后端 CRUD（server/，新增 crud.py + api.py）

- `server/app/crud.py`：conversations/messages/settings/kv 全量 CRUD，**全部 ? 占位参数化查询**；
  写操作 BEGIN/COMMIT + 出错 ROLLBACK；会话删除在单事务内级联删除其 messages（表无外键手动删）；
  列表 limit 默认 500 上限 2000 封顶；
- `server/app/api.py` 路由（统一 `{ok:true,data}` / `{ok:false,code,message}`，非法 JSON/缺字段 400，不存在 404）：
  - `GET/POST /api/conversations`（POST 按 id 幂等 upsert）、`GET/PUT/DELETE /api/conversations/{id}`
  - `GET/POST /api/conversations/{id}/messages`、`POST /api/conversations/batch-messages`（单事务批量）
  - `DELETE /api/messages/{id}`（单条删除，同步补全）
  - `GET/PUT /api/settings`（整体 upsert）、`GET/PUT /api/kv/{key}`
- `/api/health` 增加 `counts:{conversations,messages}`。

### 前端同步层（新增 parts/115_backend_sync.js + assemble.py PARTS 注册）

- `BackendStore`：方法签名与 DB（IndexedDB 封装）对齐；`enabled()` = useBackendSync 开关（默认开）&& 后端在线；
- **DB 写方法包装**（createConversation/updateConversation/deleteConversation/putMessage/deleteMessage）：
  先落本地（快、不丢）→ 即时推后端（失败不抛错 → 标记"待同步"）；**离线且开关开启 → 标记"待同步"**；
  同步状态为 IndexedDB 扩展字段（不入后端）；设置同步排除 API Key 字段（key 绝不进 SQLite）；
- `pull()`（启动/恢复）：后端 → 本地合并（updated_at 较新者胜，时间戳统一转数字避免排序错乱）；
- `pushAll()`（一键同步）：pull → 本地全部上传（会话 upsert + 消息 batch）→ pull 拉回合并；
- 会话列表同步状态小徽章（已同步/待同步/同步中，`refreshSyncBadges`）；
- 首次启用且后端为空、本地有数据 → 弹窗引导一键上传（不静默、不删本地）；
- 设置页"数据与同步"区：开关 / 立即同步 / 上次同步时间 / 本地与后端双端会话计数（保留导出/导入）。

### 过程中修复的三个真实 bug

1. `_markConvStatus` 递归：包装版 updateConversation 成功路径再次触发标记 → 无限递归；
   改为走原始方法（_origDB）；
2. 推送载荷缺 id：`createNewConversation` 不传 id（DB 内部生成），包装用入参（无 id）→ 后端 400；
   改为优先用返回值（含生成 id）；
3. `_markConvStatus` 静默失败：`_origDB` 只存被包装的 5 个方法，listConversations 未包装 →
   undefined 调用；改为直接用未包装的 `DB.listConversations`。

### 验收（curl + 浏览器真实链路 + sqlite 直查）

1. curl CRUD 全链路：建（幂等 upsert）→ 列表 → 改 → 单条消息 → 批量 → 详情升序 → 设置/KV →
   404 → 非法 JSON 400 → 缺字段 400 → **删除会话 messages 归零（事务级联）** ✓
2. SQL 注入：title 传 `' OR 1=1 --`、`x'; DELETE FROM conversations; --`、消息 UNION 载荷、
   id 路径注入 → 全部按普通文本存储/404，无越权删除/查表 ✓
3. 在线双写：UI 新建会话/发消息（真实 Ollama）/改名/置顶/改设置 → 刷新数据在 →
   sqlite 直查 conversations/messages/settings 对应行 ✓
4. 离线：关后端新建会话+发消息正常（IndexedDB），标记"待同步"；重启后端 → 一键同步 →
   37 条会话全部进入 sqlite，标记收敛为"已同步" ✓
5. 双窗口（第二台设备视角）：A 新建并发消息 → B 窗口 pull 后看到同一条（共享同一 SQLite）✓
6. 回归：思考三态/壁纸/代理/网关/设置页同步区全部在位；离线纯前端行为与改造前一致 ✓
7. 语法 0 失败、组装×2 幂等（651642 chars/19249 行）、控制台 0 报错；
   200 条消息批量写入后端 **12ms**，详情按会话拉取无卡顿 ✓

### 交付

pre_m3 / post_m3 快照（zip 47 项含 .gitignore/脚本/115，排除 .env/.venv/pycache/sqlite）并复制桌面；
桌面 MoRay工作台\（server/ 无 .env）与 MoRay网站_拖到Cloudflare.zip 同步。

### 受限项

- 消息内容/attachments 中附件 DataURL 未入库（attachments 字段预留为 NULL，本里程碑不含图片同步）；
- 冲突合并以 updated_at 为准，同一毫秒内双端并发修改可能丢失一端（概率极低，可接受）；
- 同步错误可见（待同步标记 + 可重试）但不打断对话；大批量首次同步期间 UI 有"同步中"提示。

## v3.13.0（2026-08-31）里程碑 M2：云端 LLM API 后端代理（key 收进本地后端）

### 一、start_backend.sh 路径 bug 修复

- 原第 47 行先 cd server 后用相对路径 `$VENV/bin/python`（VENV=server/.venv 相对工程根）→ 变成
  server/server/.venv 不存在 → Mac/Linux 启动失败；
- 修复：cd 前计算 `VENV_ABS="$(cd "$(dirname "$VENV")" && pwd)/$(basename "$VENV")"`，exec 改用
  `$VENV_ABS/bin/python`；装依赖步骤在 cd 之前保持不变；
- **受限**：本机 Windows 无 bash/WSL 发行版，`bash -n` 无法执行；脚本改动已人工核对（引号/转义/变量）。

### 二、后端代理（server/，业务代码仅新增/扩展）

- `requirements.txt` 新增 `httpx`（转发）+ `python-dotenv`（读 .env）；
- 新增 `server/.env.example` 模板（MORAY_LLM_BASE_URL / MORAY_LLM_API_KEY / MORAY_LLM_MODEL，含注释）；
  新增工程根 `.gitignore`（server/.env、.venv、data、__pycache__ 永不打包）；打包/备份/桌面同步
  全部排除 server/.env（zip 校验仅含 .env.example）；
- `config.py`：python-dotenv 读 server/.env（不存在时用环境变量，再没有为空，不报错）；导出
  LLM_BASE_URL / LLM_API_KEY / LLM_MODEL；版本升 0.2.0；
- 新增 `server/app/llm_proxy.py` + main.py 挂载 `POST /api/llm/chat`：
  - 入参白名单：messages / model（缺省用后端默认）/ stream（默认 true）/ temperature / max_tokens / think；
  - 非流式返回 `{ok, content, reasoning, model, usage:{prompt/completion/total}}`；
  - 流式 StreamingResponse SSE 逐块透传；前端断开（cancel）→ finally `client.aclose()` 同步中止上游；
  - 上游 401/402/429/5xx/超时 → `{ok:false, code, message}` 清晰 JSON；日志 key 只打 `sk-***后4位`；
  - 防 SSRF：转发目标只来自后端配置，请求体指定 base_url/api_key 一律忽略；仅监听 127.0.0.1；
    超时 connect 10s / read 300s / write 60s；
- `/api/health` 增加 `cloud_configured`（反映 .env 是否已配 key，不返回任何密钥信息）。

### 三、前端增量（parts，代理/直连双路 UI 无差异）

- `MoraySettings.useBackendProxy`（默认 false，持久化）；
- 设置页"云端 API"区新增二选一（后端在线时显示）：`经本地后端代理`（shield 图标）/ `前端直连`
  （plug 图标），提示"代理模式下 key 保存在 server/.env，不会进入浏览器"；探测完成事件
  `moray-backend-probed` 驱动刷新；
- `20_ai.js`：MorayAI 新增 `_useProxy()`（useBackendProxy && 后端在线 → 返回代理端点；离线时
  明确 toast 提示并**自动回退直连**，10 秒节流）；`chatStream`/`chat` 的 openai 分支在代理模式下
  请求 `http://127.0.0.1:<port>/api/llm/chat`，SSE 逐块解析（含 reasoning/usage），停止生成
  （AbortController→fetch abort→后端 aclose）、token/成本统计、智能路由、多模型对比全部复用现有链路；
- 前端任何日志/请求体/存储均不含云端 key（代理模式下前端不持有 key）。

### 验收（本机真实 Ollama qwen3.5:4b + mock 401，全链路实测）

1. bat 回归启动正常（依赖已装秒级启动，服务 0.2.0 + cloud_configured:true）；
   `bash -n` 因无 bash/WSL 未执行（如实受限）
2. curl /api/llm/chat 非流式：真实 Ollama 回复 + usage{11,106,117} ✓；
   SSE 流式：逐块透传（含 reasoning 思考块）+ [DONE] ✓
3. 浏览器代理对话：逐字流式渲染、token 统计（18/18）、智能路由（simple→qwen2.5:1.5b 自动路由）、
   停止生成生效；Network 抓包仅请求 127.0.0.1:8000，请求体/响应体无任何 key/Authorization ✓
4. 上游 401（mock）：`{"ok":false,"code":"invalid_key","message":"上游错误（401）：..."}` 无堆栈无泄漏；
   流式同样输出错误帧 + [DONE] ✓
5. SSRF：请求体注入 base_url=http://evil.com:9999/v1 + api_key → 被忽略，仍转发 .env 配置的 Ollama ✓
6. 关闭后端：toast"本地后端离线，代理模式已自动回退为前端直连"，useBackendProxy 自动置 false，
   直连/Ollama 本地对话、壁纸、会话等全部正常 ✓
7. server/.env 未出现在任何 zip/备份/桌面副本（校验仅 server/.env.example）✓
8. 17 parts 语法 0 失败、Python ast 全过、组装×2 幂等（632835 chars/18803 行）、控制台无新增报错 ✓

### 交付

pre_m2 / post_m2 快照（zip 44 项含 .gitignore + 脚本，排除 .env/.venv/pycache/sqlite）并复制桌面；
桌面 MoRay工作台\（server/ 无 .env）与 MoRay网站_拖到Cloudflare.zip 同步。

### 受限项

- `bash -n` 未执行（本机无 bash/WSL 发行版）；sh 改动为人工核对，建议 Mac/Linux 实机跑一次；
- 401/402 等错误码映射基于 OpenAI 兼容协议常见语义（DeepSeek 验证过 401）；个别服务商自定义错误体
  会透出其 message 文本；
- 流式停止存在 1-3 秒中止延迟（fetch abort → 后端 cancel → httpx aclose → 上游断开）；
- 代理模式下 key 只存后端 .env，但**本地后端本身无鉴权**（仅监听 127.0.0.1），若需防本机其他进程
  访问，后续里程碑可加 local token。

## v3.12.1（2026-08-31）M1 补漏：一键启动脚本补齐（start_backend.bat 增强 + 新增 start_backend.sh）

### 改动（仅脚本 + README 措辞对齐，app/ 与 parts/ 零改动）

- `scripts/start_backend.bat`（Windows，GBK 编码保证中文不乱码）：
  - `%~dp0..` 定位工程根，不依赖当前工作目录；
  - Python 检测：优先 `python`，其次 `py -3`，都没有则提示安装并 pause 退出；
  - `server\.venv` 不存在才创建；依赖安装官方源失败自动回退阿里云镜像
    （`mirrors.aliyun.com`，pip 加 `--timeout 30 --retries 1` 快速失败；已装秒级跳过）；
  - 启动前端口占用检测，被占用提示 `set MORAY_PORT=8123` 换端口，不报堆栈；
  - uvicorn 启动（MORAY_PORT 默认 8000），延迟 2 秒自动打开 `/api/health`，
    窗口保持，打印"MoRay 后端运行中，关闭此窗口即停止服务"；
- `scripts/start_backend.sh`（macOS/Linux）：等价逻辑（python3 + server/.venv + 阿里镜像回退 +
  端口检测 + 自动开健康检查）；
- `server/README.md`：修正 Windows 段误入 sh 语法的行（`MORAY_PORT=8123 scripts/start_backend.bat` →
  `set MORAY_PORT=8123`），补充两个脚本的行为说明。

### 验收（全流程实测）

1. 删除 server\.venv 全新环境运行 bat：自动建 venv → 装依赖（官方源超时自动回退阿里镜像）→
   启动 uvicorn → 延迟 2 秒自动打开浏览器访问 /api/health（服务日志可见第二次 200 请求）✓
2. 再次运行：跳过 venv 创建与依赖安装，秒级启动 ✓
3. `set MORAY_PORT=8123` 启动：uvicorn 监听 8123，自动打开的地址也是 8123 ✓
4. 端口被占用：输出"端口 8000 已被占用。请换端口后重试： set MORAY_PORT=8123"，无堆栈 ✓
5. 前端 file:// 场景：Origin=null CORS 放行此前已验证；浏览器端显示"本地后端 ● 已连接" ✓
   （IAB 无法直接导航 file://，建议真机双击复核）
6. parts/ moray-workbench.html server/app 与 M1 交付快照 SHA256 完全一致（零改动）；
   assemble 产物不受影响 ✓

### 交付

post_m1gap 快照（scripts + README + CHANGELOG）并同步桌面 MoRay工作台\。

### 受限项

- 自动打开浏览器依赖默认浏览器（webbrowser.open）；无默认浏览器时仅打印地址；
- sh 脚本未在 macOS/Linux 实机执行（本机 Windows），逻辑与 bat 等价，建议实机跑一次；
- 首次安装依赖时长取决于网络（官方源慢时自动切镜像，约 1-3 分钟）。

## v3.12.0（2026-08-31）里程碑 M1：本地薄后端骨架（FastAPI+SQLite）+ 前端探测与离线回退

### 新增 server/ 后端（最小闭环，无业务接口）

- `server/requirements.txt`：fastapi>=0.111,<1.0、uvicorn[standard]>=0.30,<1.0（兼容 Python 3.12/3.13）；
- `server/app/config.py`：主机 127.0.0.1、默认端口 8000（MORAY_PORT 环境变量覆盖）、
  SQLite 路径 server/data/moray.sqlite3（目录自动创建）、版本常量 0.1.0；
- `server/app/db.py`：标准库 sqlite3 连接 + 建表骨架（conversations/messages/settings/kv，主键+时间戳齐全）
  + `ping()` 数据库读写自检；
- `server/app/main.py`：FastAPI 应用——
  - `GET /api/health` 返回 `{ok, service, version, time, db}`（db 来自 db.ping()）；
  - CORS 用 `allow_origin_regex` 放行 `null`（file:// 场景）/ file://.* / http://127.0.0.1 / http://localhost
    任意端口，方法头全放行，credentials 合法组合（未用 allow_origins=['*']）；
  - 全局异常兜底：任何接口报错返回 JSON 而非 HTML 堆栈；
  - `GET /` 一行服务说明；
- `scripts/start_backend.bat`：一键创建 venv → 装依赖 → uvicorn 启动（MORAY_PORT 可覆盖端口）；
- `server/README.md`：目录结构/安装/启动/健康检查/端口/跨平台说明/后续里程碑占位。

### 前端增量（仅 parts/110_polish.js 末尾 + 无 UI 风格改动）

- 静默探测模块：页面启动 fetch `/api/health`（AbortController 2s 超时，全程 catch 静默；
  每次带随机 query 参数绕开浏览器/SW 对 health 响应的缓存，避免旧响应误判）；
- 底部状态栏新增状态项：`本地后端 ● 已连接`（success 色）/ `本地后端 ○ 离线模式`（tertiary 色），
  探测完成刷新一次，不影响其它状态项；
- 端口可用查询参数 `?backend=8123` 覆盖（对应 MORAY_PORT）；
- **本里程碑不做任何业务读写切换**：会话/消息/设置仍走前端 IndexedDB，后端未启动时前端与之前完全一致。

### 验收（全流程实测）

1. `scripts/start_backend.bat` 全流程可用（venv→pip→uvicorn；首次装依赖在官方源较慢，
   已用清华镜像完成安装并验证）；uvicorn 启动成功 ✓
2. `GET /api/health` 返回约定 JSON（ok:true / db:ok）；server/data/moray.sqlite3 生成，
   四张表（conversations/messages/settings/kv）+ messages 索引建齐（.schema 已核验）✓
3. file:// 场景的 CORS：Origin=null → `Access-Control-Allow-Origin: null` + credentials 放行 ✓
   （IAB 无法直接导航 file://，用 Origin 头精确验证；浏览器端 http://127.0.0.1:8899 打开
   状态栏"本地后端 ● 已连接"、控制台无 CORS 报错 ✓）
4. 关闭后端刷新：状态栏"本地后端 ○ 离线模式"（tertiary 色），页面功能完全正常（会话/思考/壁纸均在，
   控制台仅静默捕获的连接失败）✓
5. MORAY_PORT=8123 启动成功 + 前端 `?backend=8123` 探测"已连接" ✓（端口可配）
6. 17 parts 语法 0 失败、组装×2 幂等（625674 chars / 18656 行）、Python 侧 ast 全过、
   控制台无新增报错 ✓

### 交付

pre_backend / post_backend 快照（zip 45 项含 server/ 与脚本 + 单文件 + web zip）并复制桌面；
桌面 MoRay工作台\（含 server/，不含 .venv）与 MoRay网站_拖到Cloudflare.zip 同步。

### 受限项

- 依赖安装依赖网络（PyPI 官方源在国内可能较慢；可用 `pip install -i 镜像` 加速）；
- IAB 环境无法直接以 file:// 导航（引擎限制），file:// 的 CORS 行为以 Origin=null 请求头验证，
  建议真机 Chrome/Edge 双击打开复核一次；
- 首次探测期间（≤2s）状态栏显示"离线模式"占位，探测完成即刷新为真实状态。

## v3.11.6（2026-08-31）小补丁：生成进行中的模型名与思考面板显示修正

### 问题（真机）

- simple 任务实际路由到 qwen2.5:1.5b（非思考、不带 think），但发送后加载态占位气泡头部仍显示发送前
  模型名（qwen3.5），且错误出现"深度思考中"面板，要等 result 返回才纠正。

### 改动（parts/30_chat.js + parts/100_gateway.js，增量）

1. **Gateway.chatStream 增加 `onRoute` 早期回调**（100_gateway.js）：route 完成后立即回调
   `routed`（model/routeInfo），不等流式结束；
2. **30_chat.js**：
   - think 决策提前到占位消息创建前（on/off 直接定；auto 在 requestMessages 就绪后补齐并回写占位消息）；
   - 占位气泡头部用 `preciseModelName`（含 :4b/:9b/:1.5b 后缀）+ 按 think 决策显示"思考中/正在生成"；
   - chatStream 传入 `onRoute`：拿到路由结果立即刷新占位头部（实际模型 + 状态文案）与思考面板
     （非思考型/think=false 立即移除"深度思考中"占位）；
   - `fillAssistantBody` 思考面板收紧：完成态有 reasoning 照常；流式/占位态必须
     **思考型模型 && think===true** 才显示；think 未定义的既有调用（对比页等）保持原行为；
   - `onChunk` 无正文时状态文案按 think 决策区分（思考型+think=true →"深度思考中"，否则"正在生成"）。

### 验收（浏览器真实链路 + 慢速 mock 观察加载态；未实机）

1. "在吗"（simple→qwen2.5:1.5b）：发送 250ms 加载态即显示 `qwen2.5:1.5b · 正在生成`，无思考面板；
   完成后模型名不变不跳变 ✓
2. "请证明根号2是无理数"（complex→qwen3.5:9b、think=true）：加载态 `qwen3.5:9b · 思考中` + 思考面板；
   完成后一致 + reason 小字 ✓
3. 手选模型（userPickedModel=true）：加载态显示所选模型，不被路由改写 ✓
4. 边界矩阵：think 未定义（对比页兼容）保持原行为 / 完成态 reasoning 照常 / think=false 不显示 /
   非思考模型 think=true 不显示 / 思考模型 think=true 显示——全部符合 ✓
5. 17 parts 语法 0 失败、组装×2 幂等（622288 chars / 18582 行）、控制台 0 报错 ✓

### 交付

pre_routefix / post_loadingfix 快照（zip+单文件+web zip）并复制桌面；桌面 MoRay工作台\ 与
MoRay网站_拖到Cloudflare.zip 同步。

### 受限项

- 加载态模型名的首次展示依赖 onRoute 回调时序（route 为异步）；在极端慢的 route（如 guardCloud 网络探测）
  下占位会先显示 conv.model（精确名）约几十毫秒，随后立即刷新为实际模型；
- 思考面板"深度思考中"占位为 UI 提示，模型是否真的输出 thinking 由服务端决定（think=true 且模型支持时）。

## v3.11.5（2026-08-31）修复：智能路由选模方向 + 本地优先数组 + 请求空消息 + 实际模型不可见

### 1. 路由启发式选模方向错误（parts/100_gateway.js）

- 根因：未配置 routingConfig 时用写死下标（simple→第2个、complex→第1个），方向相反且依赖不保证的
  列表顺序（真机：默认 4b 时 simple"你好"被发给更大的 9b）；
- 新增纯函数 `modelSizeB(name)`（正则 `/(\d+(?:\.\d+)?)\s*b\b/i` 提取参数量，解析不到返回 null，
  8 个单测用例全过）；
- `TaskRouter.route` 未配置 primary 时：可用模型按参数量升序排序（可解析在前按大小排，解析不到的
  保持原顺序排末尾）——simple→最小（index 0）、complex→最大（最后一个）、medium→居中；
  reason 写明实际模型，如 `simple任务 → 最小本地模型 qwen3.5:4b`；
- 显式配置 primary/fallback 时仍以配置为准（pick 逻辑保留）。

### 2. 本地优先数组 bug（Gateway.route）

- 原实现 `local[0].name` 与 `local.name` 混用风险（对数组取 .name 恒 undefined 的旧写法已清除）；
  改为 filter 后取 `localArr[0]` 的 name，且按参数量最小选择本地模型（simple/medium 倾向最小）；
- 新增兜底：本地优先/任意分支后 decision.model 非空合法字符串，否则回退 opts.model/defaultModel/
  首个可用模型。

### 3. 请求历史空 assistant 消息（buildRequestMessages）

- 会话消息筛选后追加空 content 过滤（user/assistant 均滤，content 去空白为空即剔除）；
- 仅含图片附件的合法 user 消息以 attachments 存在为准保留，不误删（单测验证）。

### 4. 实际使用模型可见（30_chat.js）

- 流式返回后 `result._routed.model` 回写 `assistantMsg.model`；气泡头部改用 `preciseModelName`
  （保留 :4b/:9b 后缀）精确显示实际命中模型；
- 有 routeInfo.reason 时在消息 meta 区下方新增一行 tertiary 小字（`.route-reason-note`），
  如 `complex任务 → 参数量最大本地模型 qwen3.5:9b`；无路由信息不显示；
- 输入框标签仍表示"用户当前选择/默认"（currentConvModel），与气泡实际模型语义分离。

### 验收（浏览器真实链路 + fetch 抓包；无 Ollama 实机）

1. routingConfig 全空 + 默认 4b + "你好"（simple）：请求体 model=qwen3.5:4b（最小）、think=false、
   messages 无空 content 消息 ✓
2. "请证明根号2是无理数"（complex）：model=qwen3.5:9b（最大）、think=true（auto 档）✓
3. routingLocalFirst 开启多次发送：model 始终非空合法字符串（qwen3.5:4b，最小本地模型），无 undefined ✓
4. 会话内手选模型（userPickedModel=true）：simple/complex 均不被路由覆盖 ✓
5. AI 气泡头部精确显示 "qwen3.5:9b · 时间"；reason 小字显示 ✓
6. 17 parts 语法 0 失败、组装×2 幂等（620008 chars / 18538 行）、控制台 0 报错、
   深度思考/网关/壁纸贯穿回归正常 ✓

### 交付

pre_routefix / post_routefix 快照（zip+单文件+web zip）并复制桌面；桌面 MoRay工作台\ 与
MoRay网站_拖到Cloudflare.zip 同步。

### 受限项

- 参数量解析依赖模型名含 "N b" 标记（qwen3.5:9b / qwen2.5:1.5b 等）；名称不含参数量（如 llama3.1）
  的模型排在可解析模型之后，simple/complex 退化为列表首/尾选择；
- 云端 OpenAI 后端：无本地模型概念，未配置 primary 时按模型名参数量排序（解析不到按列表序），
  不因本地排序导致无模型可用（pick 对 openai 后端放行任意 name）；
- 路由/思考决策为启发式，极端模型列表（全部不可解析）下行为退化为"simple=列表第一个"，方向已修正为
  complex=列表最后一个。

## v3.11.4（2026-08-31）壁纸贯穿整个工作台：三栏/卡片毛玻璃穿透

### 根因

- 壁纸类 `.wallpaper-xxx` 只加在 #app，但三栏（aside.bg-surface-deep / #middleSidebar / main.bg-surface-void）
  全部是不透明实色，把 #app 背景完全盖住 → 切换壁纸视觉几乎不变；
- 另发现：无 localStorage 时 #app 从未获得默认 aurora 壁纸类（首次加载即无壁纸背景）。

### 改动（仅 parts/110_polish.js，末尾新增壁纸贯穿模块）

1. **data-wallpaper 作用域**：包装静态骨架三函数（switchWallpaper / loadWallpaperFromStorage /
   handleCustomWallpaper），切换/恢复/自定义上传后同步 `#app[data-wallpaper]`（内置=name / 自定义=custom）；
   MutationObserver 兜底内联背景图变化；`__syncWallpaperAttr` 顺带兜底默认 aurora 壁纸类；
2. **壁纸穿透/毛玻璃 CSS**（运行时注入 #wallpaperThroughStyle，集中一处）：
   - 统一变量：`--wall-panel-alpha: 0.62` / `--wall-blur: 16px` / `--wall-scrim: rgba(6,8,14,.30)` /
     `--wall-card-alpha: 0.45` / `--wall-floating-alpha: 0.90`；
   - 三栏骨架：background-color 半透明 + `backdrop-filter: blur(16px) saturate(1.1)` +
     inset box-shadow 压暗层（--wall-scrim，壁纸最亮时文字仍可读）；
   - 三栏内部卡片/气泡/输入台：更透明的纯色（0.45），**backdrop-filter: none**（防嵌套 blur 掉帧）；
   - 浮层（modal/命令面板/右键/通知/浮出菜单/slash 菜单）在 body 直属（#app 外）→ 全局规则
     alpha 0.90 + blur(18px)，浅色主题单独覆盖 rgba(255,255,255,0.92)；
   - pure：`--wall-panel-alpha: 0.96` 接近纯色（不喜欢花哨的用户）；
   - 浅色主题：三栏浅色半透明值 + 白色 scrim，对比度正常；
   - `background-attachment: fixed`（移动端失效自然退化为 cover 铺满）。

### 验收（浏览器真实链路 + 计算样式 + 截图像素采样；无真机）

1. 依次切换 6 款内置壁纸：data-wallpaper/壁纸类/三栏半透明+blur 逐款验证 ✓（pure 档 alpha 0.96）
2. 自定义鲜艳图（canvas 生成红黄蓝渐变）：data-wallpaper=custom + 背景图 cover；截图像素采样
   左栏 RGB(76,34,35) 偏红 / 主区 RGB(74,61,29) 偏黄（纯色基准 16,19,29 / 10,12,20）→ 壁纸确已透出；
   卡片 0.45 透明 + blur:none（无嵌套）✓
3. 刷新页面：data-wallpaper=custom + 背景图 + 穿透 CSS 全部恢复 ✓
4. pure：面板 rgba(…,0.96) 接近旧观感 ✓
5. 长对话滚动：内部卡片/气泡无 backdrop-filter（仅三栏一层 blur）✓
6. 浅色主题：aside rgba(233,237,245,.62) / mid rgba(255,255,255,.62) / main rgba(243,245,250,.62) +
   blur；卡片/输入台 0.45；浮层 0.92；文字对比正常 ✓
7. 17 parts 语法 0 失败；组装×2 幂等（617095 chars / 18470 行）；控制台 0 报错；深度思考开关、网关缓存、
   会话列表、主题切换回归正常；布局尺寸未动（aside 48px / 中栏 260px）✓

### 交付

pre_thinkfix / post_wallpaper 快照（zip+单文件+web zip）并复制桌面；桌面 MoRay工作台\ 与
MoRay网站_拖到Cloudflare.zip 同步；controls_audit.md 重跑更新。

### 受限项

- 毛玻璃最终观感依赖 GPU（backdrop-filter）；低端设备/WebView 下 blur 可能被浏览器降级为纯半透明（功能不变）；
- 移动端 background-attachment: fixed 失效时背景随滚动容器铺满（cover 不退让，视觉略有差异）；
- 壁纸"刺眼"程度与自定义图亮度正相关，压暗层（--wall-scrim）为固定强度，未做按图自动调节。

## v3.11.3（2026-08-31）修复：think 参数下发位置错误（options → 顶层）

### 问题（真机 Ollama + qwen3.5:4b 实测确认）

- Ollama `/api/chat` 的 `think` 是**顶层字段**（与 model/messages/stream 平级），不是 options 采样参数；
- v3.11.2 把 think 写进了 options → Ollama 忽略 → "关闭深度思考"无效；
- 实测同一道数学题：顶层 `think:false` 思考 0 token / 总 196 token 直接回答；`options.think:false`（错误）思考 2394 token / 总 1130 token 仍长篇思考。

### 修复（仅 parts/20_ai.js，两处请求体）

- 流式（约 277-288 行）与非流式（约 389-397 行）：think 移出 options，改为顶层条件展开字段；
  options 只保留 temperature / top_p / num_predict，不得再含 think；
- 判定不变：仅 `isThinkingModel(model) && opts.think !== undefined` 才带 think；qwen2.x 非思考模型请求体
  绝不出现 think；OpenAI 兼容路径未动；
- 顺带修正 `isThinkingModel` 首条排除正则分隔匹配：`[:\-]` → `[-_.:\s]`（规范覆盖 - : . _ 空白分隔）。

最终请求体结构（思考型模型 + 显式 think）：
```json
{
  "model": "qwen3.5:9b",
  "messages": [...],
  "stream": true,
  "think": false,
  "options": { "temperature": 0.7, "top_p": 0.9, "num_predict": 2048 }
}
```

### 验收（浏览器抓包 mock 后端；本机无 Ollama 实机，请求体结构级验证）

1. qwen3.5 + thinkMode=off + 证明题：请求体**顶层 think:false**，options 仅三项无 think ✓
2. thinkMode=on + 你好：顶层 think:true，options 无 think，思考折叠展示照常 ✓
3. auto + 复杂题（含"为什么/算法/证明"）：顶层 think:true；寒暄"谢谢"：顶层 think:false ✓
4. qwen2.5:7b（on 档 + 证明题）：顶层与 options 均无 think 字段，行为与改动前一致 ✓
5. 17 parts 语法 0 失败；isThinkingModel 17 用例全过（qwen2.5:7b/1.5b/qwen2:7b/qwen2.5→false，
   qwen3.5:4b/9b/qwen3:8b/deepseek-r1/o3/glm-z1→true）；三态按钮循环、网关缓存（同 think 二次命中）、
   会话列表、主题壁纸回归正常，控制台 0 报错 ✓

### 交付

pre/post_thinkfix 快照（zip+单文件+web zip）并复制桌面；桌面 MoRay工作台\ 与 MoRay网站_拖到Cloudflare.zip 同步。

### 受限项

- 本环境无 Ollama 实机，think 顶层下发的**服务端生效**（关闭思考后 token 骤降、快速直答）需在真机复测；
  请求体结构已与任务给出的正确写法逐字节一致；
- 非流式路径（AI.chat 回退/工具轮）同样已改顶层，但未实机验证服务端响应差异。

## v3.11.2（2026-08-31）深度思考三态开关 + 智能路由自动联动思考

### P0 思考三态开关（auto / on / off）

1. `MoraySettings` 新增 `thinkMode`（默认 `auto`，持久化到 IndexedDB 设置）；
2. 输入工具栏新增「深度思考」按钮（`#thinkModeBtn`，lucide `brain` 图标，纯 JS 动态插入静态骨架 + 运行时注入
   样式，不改 HTML）：点击 auto→on→off 三态循环，AUTO 中性小标 / ON 冷蓝高亮 / OFF 灰色；tooltip 分别说明；
   正在生成时点击给出提示并拒绝；`syncInputModelLabel` 同类时机刷新按钮状态；
3. 新增统一判断 `isThinkingModel(name)`（20_ai.js 模块级）：大小写不敏感匹配 qwen3 / deepseek-r1 / r1 / think /
   reason / o1 / o3 / glm-z1 等；**qwen2 / qwen2.5 / qwen2.5-coder 等显式排除返回 false**（13 个单测用例全过）；
4. think 布尔在发送前计算（30_chat.js `generateAssistantReply`）：on→true；off→false；auto→
   `TaskRouter.classify(本次消息)==='complex'`；经 `opts.think` 透传（30_chat → Gateway/AI → 20_ai）；
5. 20_ai.js 两处 Ollama 请求体（流式 + 非流式）：**仅当 `isThinkingModel(model)` 且 `opts.think !== undefined`**
   才加入 `"think": 布尔`；非思考模型绝不带该字段；OpenAI 兼容路径完全不动（服务商不支持即不传）；
6. 展示联动：think=true 且模型返回 `message.thinking` 时沿用现有 `thinkingDisplay` 折叠逻辑；think=false
   自然无思考面板。

### P1 路由联动 + 分类补强 + 可解释性

1. `TaskRouter.classify` 补强：complex 正则追加 数学/逻辑/推理信号（证明/推导/求解/方程/不等式/算法/复杂度/
   为什么/原理/悖论/假设/归纳/演绎/反例/边界条件/几何/概率/数学/逻辑）；medium 追加 排查/报错/计算/优化/解释类
   （debug/排查/报错/根因/最优/优化/计算/修复/解释/说明）；寒暄（你好/在吗/谢谢）仍判 simple；
2. auto 档与路由打通：complex → think:true（思考型模型生效），simple/medium → think:false；用户手动 on/off
   优先级最高（直接覆盖 auto 计算结果）；
3. 可解释性（产品卖点）：auto 档自动开启思考时，AI 消息底部渲染一行 tertiary 小字
   「复杂任务 · 已自动开启深度思考 · 路由到 <模型>」（`think-note`，含 sparkles 图标；routedTo 为空时省略尾部）；
   决策同时持久化到消息（`think` / `thinkAuto` / `routeReason`）；
4. `requestHash` 纳入 think 值：同一请求开/关思考是不同生成，不得互用缓存（已验证：同 think 二次命中、
   翻转 think 不命中）；
5. `runWithTools` 调用改为透传 `effective`（含 think），工具轮内 `AI.chat` 一并下发。

### 验收实测（浏览器真实链路 + fetch 抓包 mock 后端）

1. 默认 auto + qwen3.5:9b + "你好"：classify=simple，请求体 `options.think:false`，无 think-note 小字 ✓
2. auto + "解方程：x²+2x-3=0"：classify=complex，请求体 `think:true`，折叠思考面板 + 「复杂任务 · 已自动开启
   深度思考」小字 ✓
3. 手动 off + 证明题：强制 `think:false` 无小字；手动 on + "你好"：强制 `think:true`；刷新页面三态保持（ON）✓
4. qwen2.5:7b 做同样操作（on 档 + 证明题）：请求体 `options` **始终不含 think 字段**，行为与改动前一致 ✓
5. 抓包确认 think 仅思考型模型 + 显式指定才下发（autoNaming 标题请求未指定 → 无 think 字段）；
   OpenAI 路径请求体 keys 无 think、模拟回复正常不报错 ✓
6. 17 parts 语法 0 失败、组装×2 幂等（611219 chars / 18330 行一致）、控制台 0 报错、缓存/绕过/会话等回归通过 ✓

### 交付

pre/post_thinkmode 快照（zip+单文件+web zip）并复制桌面；桌面 MoRay工作台\ 与 MoRay网站_拖到Cloudflare.zip 同步；
controls_audit.md 重跑更新（304 控件 / 637 绑定 / 173 委托 / 92 二次确认）。

### 受限项

- 真实模型的 think 生效与思考质量依赖模型支持（qwen3.5 等实测支持；其余思考型模型未逐一实机验证）；
- 复杂任务判定为规则启发式，个别边界词（如闲聊中的"为什么"）可能触发 auto 思考（多几秒思考、结果更稳，无错误风险）；
- OpenAI 云端思考型模型（o1 等）本版不传 think（保持原行为），如需服务商专有参数可后续增量扩展。

## v3.11.1（2026-08-30）返修：审计脚本崩溃修复 + 功能状态表更新 + 版本升级一次性清缓存 + 绕过缓存按钮补绑定

### P1 审计脚本崩溃修复（tools/audit_controls.js）

- 根因：控件对象 push 时缺 `tag` 字段，第 108 行 `c.tag.includes('button')` 对 undefined 调用 → `TypeError` 崩溃；
- 修复：解析时提取标签名 `tag: tagName`；判定改防御写法 `(c.tag || '').includes('button')`；
  模板字符串动态拼接（`data-${type}.${field}` 等）标记"动态绑定/跳过"，降假阳性；
- 真实运行：`controls 304 · bindings 632 · delegations 171 · suspects 92`，controls_audit.md 重新生成（exit 0）。

### P2 功能状态表更新（FEATURES_STATUS.md）

- 旧表为 v3.10.0 内容；按当前真实代码 v3.11.1 全量重写：对话/对比/提示词/片段/自动化/文档/成本/设置/全局
  九大模块，状态四选一（可用/需配置/开发中/已删除），每条带入口与验证方式；桌宠登记"开发中"不假装可用。

### P3 版本升级一次性清缓存（MoraySettings.lastCacheWipeVersion）

- 废弃基于 localStorage 的 `moray_cache_purge_v3_10_10` 标志（boot 时顺手清理）；
- 改为 `MoraySettings.lastCacheWipeVersion`：boot db 步骤检测与当前版本（v3.11.1）不一致 → `CacheStore.clear()`
  清空 apicache → 写入版本号；只执行一次，版本升级时自动再清；
- 验证：首次启动写入 v3.11.1 且清空 ✓；刷新页面不重复清空、缓存记录保留 ✓。

### 新增修复：缓存标签"重新生成(绕过缓存)"按钮补绑定（回归发现）

- 发现 v3.11.0 渲染的绕过按钮（`class="cache-chip-btn" data-bypass-cache="1"`）无任何事件绑定——死按钮；
- 修复：消息区委托同时匹配 `.msg-action-btn` 与 `.cache-chip-btn`；`handleMsgAction` 增加显式
  `bypassFlag` 参数（按钮点击强制真实请求，优先于"缓存消息自动绕过"逻辑）；
- 验证：命中缓存 → 标签+绕过按钮出现 → 点击 → 真实请求（bypassCache=true，无缓存标签）✓。

### 回归（浏览器真实链路）

- 短问候"你好"：语义缓存层直接拒绝（中/英）；端到端真实请求 1 次，请求体仅本会话 system+user；
- 语义指纹隔离：同会话同指纹命中（similarity 1.0）；跨会话上下文指纹不同 → 拒绝回放；
- 精确缓存：相同请求第二次回放命中（省 token，缓存标签显示）；
- 组装×2 幂等、外部 CDN 0、div 平衡与基线一致、17 parts 语法 0 失败。

### 受限项

- 环境无 node CLI：审计脚本经 node_repl（Node 内核）执行，逻辑与产出一致，退出码验证受限；
- 绕过按钮的鼠标级点击在 IAB 内被悬浮操作条遮挡，以 DOM 事件（同一委托链路）验证绑定有效性；
- 真实模型的语义命中/短问候回复风格依赖模型遵循，需真实 Key/Ollama 环境人工复核。

## v3.11.0（2026-08-30）全功能可用性体检 + 死按钮清零 + 语义缓存根治 + 交互反馈达标

### 概览

- 静态审计：新增 `tools/audit_controls.js`（可重复运行）扫描 17 个 parts 模块，303 个可交互控件 / 245 条绑定引用 /
  59 项委托路由；controls_audit.md 全量清单 + 二次确认。
- 运行时体检：8 个页面/模式真实点击 208 个控件，控制台 **0 报错**；行为级断言复核异步反馈项全过；
  runtime_check.md 记录。
- 问题处置：发现 1 个真死控件（`setRoutingFallbacks` 降级重试上限输入框原无绑定）→ A 类补绑定 + 持久化 +
  降级链路实际使用该设置（原硬编码 1 → `MoraySettings.get('routingFallbacks') || 2`）；其余静态疑似均为
  事件委托/跨文件绑定/模板插值/程序化跳转（说明登记）。

### 语义缓存根治（核心）

1. `lookupSemantic` 开头拒绝短消息（中文<8 字符/英文<3 词：你好/在吗/continue 等）直接 return null；
2. 新增 `semanticFingerprint()`（最近 3 条用户消息 + 上一条 assistant + 系统提示摘要），命中必须指纹一致，
   跨会话/跨上下文绝不误回放；`save` 写入指纹；
3. `semanticCache` 默认 **false**（用户手动开启才启用；已设置过值不覆盖）；
4. 缓存命中标签旁新增"重新生成(绕过缓存)"按钮（复用 regenerate 委托，缓存消息自动 bypassCache）；
5. 版本首次启动一次性清理旧 apicache 语义记录（localStorage `moray_cache_purge_v3_10_10` 标志），元数据保留。

验收：默认 false ✓；短问候/英文单词不查 ✓；指纹随系统提示/上下文变化 ✓；绕过按钮渲染 ✓；旧语义缓存清理且
元数据保留 ✓；精确缓存（hash 全等）逻辑不变，长请求指纹一致仍可命中（省 token 不回归）。

### 交互最低标准与构建回归

- 异步操作 loading/失败提示、空状态引导、破坏性操作二次确认、toast 统一反馈：历轮已建成，本轮核查无回退；
- 构建回归：①17 个 parts 语法 0 失败 ②assemble 连续两次产物 SHA256 一致（幂等）③外部 CDN 0 ④严格 div
  配平未闭合 0 ⑤运行时点击 0 报错 ⑥1280/1024/768/390 无横向溢出 ⑦多会话隔离复测（请求不串）全过。

### 交付

tools/audit_controls.js · controls_audit.md · runtime_check.md · FEATURES_STATUS.md；post_fullcheck 快照
（zip+单文件+web zip）并复制桌面；桌面 MoRay工作台\ 与 MoRay网站_拖到Cloudflare.zip 同步。

### 受限项

- 真实模型"短问候是否简短回复"依赖模型遵循（请求体已证明无串台/无误命中）；语义命中省 token 的端到端
  需真实 Key 环境；语音输入需麦克风权限——均登记待人工验证，缺环境下给出诚实提示不报错。

## v3.10.9（2026-08-30）P0 治本：彻底消除会话上下文串台残留

### 根因与修复

1. **收紧请求历史过滤**：`filter(m => !m.conversationId || m.conversationId===conv.id)` 的"无 id 放行"会漏放早期脏消息；
   改为严格 `m.conversationId === conv.id`（运行时正常消息均带 id，无需空 id 兜底）；strayCount 告警覆盖无 id 消息。
2. **token 计量与请求口径对齐**：`updateTokenMeter` 由全局 AppState.messages 全量统计改为按
   `m.conversationId === activeConv.id` 过滤后求和（空会话显示 0）；导出/字数/预警等口径随渲染一致。
3. **切换先清空再异步加载**：`openConversation` 先同步 `messages=[]` + activeConv + renderMessages + updateTokenMeter，
   再 await 加载回填；切换那一刻起全局数组绝不可能是上一会话内容；全部新建/切换入口统一走此函数。
4. **数据健康检查**：新增 `findOrphanMessages()`（conversationId 不属于任何会话/无 id）与 `cleanOrphanMessages()`
   （IndexedDB + 内存双存储清理）；设置→数据管理新增"清理孤儿消息"按钮（二次确认，只删无主数据）；
   删除会话级联清消息（DB 层本就事务级联，内存双存储同步确认）。
5. **发送前自检**：history 若仍含越界/无 id 消息 → console.error 并强制剔除；每次请求 console.log 实际消息条数
   与估算 token（开发可见）。

### 验收（抓包 + token 表）

- 强刷后新建空会话：token 表 `0 / 8192` ✓；发"你好"请求体 = system + 该条 user（+assistant 空占位），
  零 LRU、token 小量级 ✓。
- A 会话 5 条 LRU → 切 B：token 立即归 0（先清空）✓；反复快速切换后 C 请求只含 C 内容 ✓。
- 健康检查：人工插入 2 条孤儿（无主 id/无 id）→ 检出 2 条 → 清理后 DB 无残留，正常消息未被误删 ✓。
- 控制台 0 错误；组装×2 成功；外部 CDN 0。

## v3.10.8（2026-08-30）P0 根治：会话上下文串台（请求仍携带上一会话历史）

### 根因

`buildRequestMessages` 的 `history = AppState.messages.slice(-limit)` 使用全局视图数组且未按会话过滤，
一旦新建/切换会话入口未彻底重置该数组，旧会话消息残留并被整体发送；每条消息本就带 conversationId。

### 修复

1. **请求历史严格限定当前会话（防御根治）**：`history = rawAll.filter(m => !m.conversationId || m.conversationId === conv.id).slice(-limit)`；
   发现越界消息时 `console.warn` 计数，但绝不发送。
2. **入口排查**：侧栏主按钮/⌘K/Ctrl+N/空态按钮 → createNewConversation；模板新建 → createConversation+openConversation
   （含 _convMsgIndex 初始化双写）；欢迎页 suggestion → 填充输入框经 sendUserMessage 标准路径；删除会话 → 跳转或欢迎视图。
   全部入口均走标准重置，无"只切 UI 不重置数据"路径；修复1 作为兜底。
3. **两套消息存储双写一致**：用户/AI 消息 push 时同步 `_convMsgIndex[conv.id]`；新增 `removeMsgFromIndex()` 并在
   消息删除/重生成裁剪/清空历史等 6 处删除路径同步索引；分叉/清空路径原本已同步。
4. **系统提示补约束**：默认提示词追加"除非用户明确说继续/接着上面，否则不主动续写更早的未完成话题或代码"。

### 验收（mock 捕获真实请求体）

- A 会话 4 条 LRU 历史 → 故意保留残留 messages 切到新会话发"你好"：请求体 = system + user"你好"（+ assistant 空占位），
  **零 LRU 内容**，估算 token <300 ✓；越界 warn 触发（防御生效）。
- 双会话快速切换各自请求只含本会话消息 ✓；删除当前会话跳转后请求干净 ✓。
- 控制台 0 错误；组装×2 成功；外部 CDN 0。

## v3.10.7（2026-08-30）多模型对比页补接默认系统提示

- `parts/40_compare_prompts.js` 对比发送 messages 与普通对话统一：`rawSys(用户自定义全局) || buildSystemPrompt()`，
  默认启用时请求为 `[system(默认简洁直答), user]`，用户自定义优先、关闭默认提示时不携带 system；
  对比页不带历史的现有设计不变。
- 实测：默认注入（system 含"MoRay 内置"）✓、用户自定义优先 ✓、关闭后无 system ✓、控制台 0 错误；
  组装×2 成功、外部 CDN 0。

## v3.10.6（2026-08-30）思考过程默认收起可配置 + 简洁直答默认提示 + 会话上下文隔离复核

### 任务一：思考过程(reasoning)展示策略

- `thinkingPanelHtml` 重构：流式期间也默认折叠为一行（"深度思考中…" + 旋转图标），点击标题行展开/收起，
  完成态保持折叠；`auto` 策略保留"流式展开完成折叠"旧行为。
- 新增 `thinkingDisplay` 设置（默认 `folded`）：`folded` 折叠一行 | `auto` 自动展开 | `hidden` 完全隐藏
  （hidden 不渲染 thinking-panel，reasoning 仍存库可切回查看）；设置页对话设置卡新增三态下拉，持久化刷新保持。
- 思考内容平滑过渡（max-height+opacity transition）；`#9AA3B8` 令牌化。
- **修复真 bug**：collapsed class 原先只加在外层 `.thinking-panel`，CSS 却以 `.thinking-content.collapsed`
  控制 —— 内容永远展开；现 class 同时落在外层与 content（toggle 切 content），折叠/展开/收起实测全过。
- 对比页不渲染 reasoning（40_compare 未输出思考面板），无需处理，说明登记。

### 任务二：简洁直答默认系统提示

- 新增 `buildSystemPrompt()` 集中入口（普通对话统一走它）：开关 `defaultSysPromptEnabled`（默认开，关时
  请求不携带默认 system）+ 可编辑文本 `defaultSysPrompt`（空 = 内置文案）；内置文案补"不输出你自己的推理过程"。
- 优先级不变：会话级 `conv.systemPrompt` > 全局 `systemPrompt` > `buildSystemPrompt()`（用户自定义绝不叠加）。
- 设置页对话设置卡新增开关与文本编辑框。

### 任务三：会话上下文隔离复核

- 浏览器实测：新建会话请求仅含本会话消息（无上一会话/演示残留）；切换会话后发送历史严格等于当前会话；
  删除会话后旧消息不再进入请求。验证通过，无需代码改动。

### 自测

三态策略（folded/auto/hidden）与 thinkingVisible 判定全过；buildSystemPrompt 内置/自定义/关闭/请求注入/
不输出推理/会话级优先全过；折叠→展开→收起（含过渡）实测全过；设置 UI 三态+开关+文本框渲染与持久化 ✓；
上下文隔离 3 项 ✓；控制台 0 错误；截图存档（concise_folded_dark / concise_expanded / concise_folded_light）。

## v3.10.5（2026-08-30）空模型 400 修复 + 浅色竖排修复 + 消息操作条就地渲染

### P0：发送 400 "model is required" 修复

- 全局核查 `AI.models.name` 出现次数 **0**；9 处 `AI.models[0] && AI.models[0].name` 统一加 `AI.models &&` 前缀防御（数组 undefined 极端场景）。
- `detectBackend()`（20_ai.js，MorayAI class）新增 `autoSelectDefaultModel()`：Ollama/OpenAI 探测成功且
  默认模型为空时自动选定 `this.models[0].name` 并刷新输入台标签。
- `sendUserMessage` 发送前拦截：后端可用但 `currentConvModel()` 为空 → 不发请求，toast"未选择模型，
  已为你打开模型列表"并自动弹出模型选择器（无绑定兜底跳设置页）；后端 none 维持离线占位。
- `_normalizeError` 补 400 中文分支。
- **调试插曲**：首次实现时误将 MorayAI 当对象字面量给方法加逗号（class 方法间不能有逗号）导致整段
  注入脚本不执行——定位为 class/对象语法差异后修复（备份版恢复 + 正确重做）。

### P1：浅色下模型名/模式切换器竖排修复

- `.input-toolbar` 补 `flex-wrap:nowrap;min-width:0`；`.input-toolbar-btn` 与 `.mode-segment`/其按钮补
  `white-space:nowrap;flex-shrink:0`；`.mode-segment` 背景 `#1A1F30` → `var(--color-surface-card)`
  （深色值一致），浅色自动白底。
- 实测：模型名 16px 单行、切换器 30px 单行、不压输入框；1440/1024/768 三档一致。

### P2：消息操作条就地渲染 + 遗留风险清理

- 抽出 `msgActionsHtml(msg)` 共享函数；`finalizeAssistantUI`（成功/停止/失败统一路径）就地补插
  `.msg-actions`（11 个操作按钮），不再需要重开会话；生成中仍只显示停止按钮。
- id 同名审计（scan_dup_ids）：无新增；既有重复 id 全部为"静态骨架 + JS 重建覆盖/作用域查询"安全类，
  无需改名（清单见报告）。
- 错误分支：401/400/404/500 均有中文文案；消息错误卡片带"重试"入口。

### 自测

P0：取首个模型/空串/自动选默认/标签自动填充/无模型发送拦截+弹选择器/手选三处一致 全过；P1 三档宽度
单行横排全过；P2 生成完成就地 11 按钮操作条实测。组装×2 成功；控制台 0 错误；三张截图存档
（模型选择器弹层/工具栏/对比输入区）。

## v3.10.4（2026-08-30）全页面巡检 + 浅色主题全量适配 + 运行时健壮性

### 任务：无人值守系统性巡检（死按钮/浅色主题/健壮性/端到端回归/响应式）

- **[阶段一 控件巡检]** 全页面行为断言巡检（对话/对比/提示词/片段/知识库/自动化/成本/设置/⌘K/侧栏/
  欢迎页）：对比模式切换/勾选取消/同步滚动/发送停止/输入区钉底、提示词收藏落库、片段展开复制、
  工作流编辑器/运行 toast、成本真实数字、⌘K 15 项、欢迎页 suggestion 填充输入框等全部实测可用；
  消息操作按钮在重建渲染后存在（生成中气泡仅停止按钮，属既有渲染策略）。零死按钮。
- **[阶段二 浅色主题全量]** 扫描骨架硬编码深色并分类处理：命令面板/通知 toast/右键菜单/模式分段开关/
  状态栏/工作流卡/壁纸上传卡/skeleton/roadmap 圆点/kbd 标签/tooltip 全部浅色覆盖（白底+浅投影）；
  body 补浅色背景；--color-surface-hover 浅色值（#E3E9F4）随变量块生效；代码块/遮罩/壁纸渐变保持
  设计语义。实测浅色下深色块扫描 **0 残留**。
- **[阶段三 健壮性]** 运行时重复 id 0；工具完成后 6s 无延迟异常（timer 清理）；>20MB 图片拦截；
  IndexedDB 禁用内存模式降级读写；断网/无后端发送给出诚实离线占位（"当前未连接 AI 后端"）不崩溃；
  快速连点 canSendNow 防抖存在。
- **[阶段四 端到端]** 新建→发送（离线占位）→切换/搜索/置顶/重命名→导出 md（downloadText 实测）
  /json（downloadJson+toast）→提示词片段增删改查→工作流缺后端错误分支→设置持久化→⌘K 跳转，
  全程控制台 0 未捕获异常。
- **[阶段五 响应式/PWA]** 1280/1024/768/390 四档无横向溢出、输入台在视口；manifest/sw 注册/263 图标
  存在无 404；div 配平（严格匹配未闭合 0；粗计数 balance=1 为 JS 正则 `<div/i` 误报）；
  外部 CDN=0；对话深/浅、对比页、提示词库 4 张截图存档。

## v3.10.3（2026-08-30）光核输入台钉住底部 + 模型名单行横排（含选择器 bug 修复）

### 任务一：输入台固定底部，不随消息滚动

- **[根因]** #chatNormalContent（滚动容器）直到输入台外层之后才闭合，把输入台也包进了 overflow-y-auto
  滚动区，消息滚动时输入台一起滚走。
- **[修复]** chatNormalContent 的闭合 `</div>` 前移到「光核输入台」注释之前（消息列表一结束即关闭滚动容器，
  输入台成为 #chat-normal 的固定底部兄弟）；同步删除原位置的多余闭合，全文档 div 严格配平（脚本栈式校验
  未闭合 0；此前粗计数 balance=1 是 JS 正则 `<div/i` 误报）；#chatNormalContent 补 min-h-0（flex 子项滚动）。
- 对比页结构核查：输入区本就在双栏滚动容器之外（#chat-compare 直接子元素），无需修改，实测确认固定。
- 欢迎页仍在滚动区内空态居中；自动滚底逻辑不受影响。

### 任务二：输入台模型名单行横排 + 选择器 bug

- **[根因]** syncInputModelLabel 与手动选模型处用 `querySelector('span')` 取到第一个 span（beacon-dot
  圆点），把模型名写进了圆点；真正文字 span 停在占位"选择模型"，长名在极小圆点里逐字换行撑高输入台。
- **[修复]** 文字 span 加稳定 class `input-model-name`；syncInputModelLabel 与 110 弹层均精确选择
  `.input-toolbar-model .input-model-name`（圆点永远只显示颜色）；CSS 单行横排：
  `.input-toolbar-model{white-space:nowrap;flex-shrink:0;min-width:0;max-width:220px}`、
  `.input-model-name{white-space:nowrap;max-width:160px;overflow:hidden;text-overflow:ellipsis;display:inline-block}`、
  圆点/chevron flex-shrink:0；全名经 title 悬停查看。
- 骨架占位"选择模型"保留为初始值，启动后由 sync 填充，同一时刻只显示一个正确模型名。

### 自测

- 滚动钉底：25 条消息灌入后滚动到顶/中/底，输入台位置三帧完全一致（560.33px 不变）；输入台不在滚动容器内
  （结构实测）；缩放 600 高度输入台在视口内、无双重滚动条；浅色主题白底正常；对比页输入区贴底固定。
- 模型单行：deepseek-v4-flash 单行显示（高≤20px）、不压 textarea、圆点无文字、占位消失、title 完整名。
- 组装×2 成功；div 配平脚本未闭合 0；无重复 id 回归；浏览器全程无控制台错误。

## v3.10.2（2026-08-30）默认回复简洁化 + 输入台跟随主题 + 模型名三处同步

### 任务一：默认回复要简洁（内置默认系统提示词）

- 新增 `DEFAULT_SYSTEM_PROMPT`（30_chat.js 顶部，中文）：MoRay 内置编程助手、与用户同语言默认中文、
  简洁直接就事论事、寒暄一两句、严格围绕最新一条消息不续写旧任务、代码给最小可运行片段、
  不确定先澄清。
- `buildRequestMessages` 取值优先级改为：会话级 `conv.systemPrompt` > 全局 `systemPrompt` >
  `DEFAULT_SYSTEM_PROMPT`；仅当两级自定义都为空才用内置默认，用户自定义后**不叠加**默认。
- 影响面仅主对话；角色模板（95_features 自带 systemPrompt）与工作流自带提示词不受影响。
- 思考过程（reasoning）完成态默认折叠（标题栏"思考过程"可点击展开、chevron 旋转-90°），流式中保持展开
  显示占位动画；reasoning 数据完整保留。

### 任务二：光核输入台跟随浅色主题

- `.core-input` 背景 `#1A1F30` → `var(--color-surface-card)`（深色值一致，视觉不变）；`.input-toolbar-divider`
  `#262C3E` → `var(--color-line-ghost)`；`.input-stats` 颜色/边框、`.input-stats-item .value`、
  `.slash-menu` 背景/边框全部令牌化（深色值与原硬编码一致）。
- `html[data-theme="light"]` 下补充：core-input 浅底白 + 1px 浅边 + 柔和投影（0 2px 12px rgba(26,34,51,.08)
  替代深色大投影）+ focus-within 淡冷蓝描边；slash-menu 白底浅投影；input-stats 边框线浅色。
- 对比模式复用同一 `.core-input` 类，浅色自动一致。

### 任务三：输入台模型名与实际发送模型同步

- 新增 `currentConvModel()`（唯一数据源：conv.model > 全局默认模型 > AI.models[0]，与
  generateAssistantReply 解析完全一致）与 `syncInputModelLabel()`（标签 = 短名或"选择模型"，
  title 附完整名与"未检测到模型"提示）。
- 调用时机：启动初始同步、openConversation 末尾、新建/删除会话后、手选模型后（110 弹层替换原局部更新）、
  设置"设为默认"后、HealthMonitor 重检后；弹层勾选 current 同用 currentConvModel —— 弹层勾选 =
  输入台标签 = 消息头模型，三处永远一致。
- 骨架写死名清理：输入台 "Qwen2.5-Coder"→"选择模型"（初始由 sync 填充）、对比列头→"对比模型"、
  消息头占位→"AI 助手"、模型勾选占位→"模型 A/B"、已安装模型占位→"示例模型"。

### 自测

- 默认注入/全局优先/会话优先/不叠加 4 项通过；reasoning 折叠与流式展开通过。
- 浅色主题实测：core-input 白底 rgb(255,255,255)、投影 0px 2px 12px、divider 浅色、slash-menu 白底。
- 模型三处一致：手选会话模型后标签=qwen2.5-coder=消息头短名=弹层勾选；无模型时显示"选择模型"。
- 组装×2 成功（依赖/图标校验通过）；浏览器全程无控制台错误。

## v3.10.1（2026-08-30）会话分组创建失效修复 + 移除演示会话

### 任务一：修复"新建分组输入任何内容都提示请输入分组名"

- **[根因]** moray-workbench.html 静态骨架存在一份死弹窗 #groupModalOverlay（含 id=newGroupNameInput，
  位置先于通用 #modalBox，无任何 JS 打开它）；`createSessionGroup()` 用全局 getElementById 取值，
  永远命中隐藏的静态空输入框 → name 恒空 → 误报"请输入分组名"。
- **[修复]** ①删除静态死弹窗整块（含 #newGroupNameInput/#groupModalList/关闭按钮）；
  ②`openGroupManager()` 改为 **box 作用域绑定**：输入框与「添加」按钮均 addEventListener 闭包读取
  box 内 input.value 调 SessionGroups.create，成功后清空本 box 输入框并重渲染管理列表，不再依赖内联
  onclick/onkeydown 调全局函数；③`createSessionGroup()` 保留为兼容入口，内部改从
  `#modalBox` 作用域取 input（绝不全局撞静态节点）。
- **[重复 id 排查]** 组装后扫描：`newGroupNameInput` 全文仅 1 处（动态模板内）✓；其余重复 id 分两类——
  ①静态骨架 + JS 重建覆盖（customWallpaperInput/exportWorkflowBtn/importWorkflowBtn/newWorkflowBtn/
  runningWorkflow/monitorChart）：动态构建先于绑定，行为安全，列说明；②模板插值/作用域查询
  （${c.id}/${d.id}/ncCfg）：各自作用域内唯一，无碍。

### 任务二：移除 3 条演示会话，保留内置库

- **[移除]** `seedDataIfEmpty()` 删除 LRU 缓存/FastAPI 流式/Transformer 三条演示会话及其消息创建代码，
  提示词（6 条）/代码片段（4 条）/工作流模板（11 条）三类库种子全部保留。
- **[独立判据]** 播种判据由"会话数"改为"提示词库是否已有数据"（方案 a，注释写明：用户清空提示词库后
  允许重新播种一次）—— 修复删会话种子后"新用户会话恒 0 导致每次启动重复播种"的隐患。
- **[一次性清理]** 新增 `cleanupDemoConversations()`：localStorage 标志 moray_cleanup_demo_conv_v1
  保证只执行一次；按三个精确标题全等匹配删除对应会话及其全部消息（console.log 记录被删 id），
  绝不误删用户自建同名/其它会话（含"（我的笔记）"这类非全等标题）。

### 自测

- 分组全链路 9/9：中文+前后空格、英文、Enter 三种方式创建成功并即时出现于列表与 chips；
  重名"分组已存在"提示；连续多次开关弹窗仍可创建；重命名/删除/移动到项目/按 chip 筛选全通。
- 全新库场景：0 演示会话、提示词 6/片段 4/工作流 11 全部就位；连种两次数量不变（幂等）。
- 旧库升级场景：3 条演示会话及消息一次性清除、用户自建会话与消息完好、标志位落库、再次执行不重复；
  精确全等匹配边界验证通过。
- 组装×2 成功（依赖/图标校验通过）；浏览器全程无控制台错误；测试后数据完整恢复。

## v3.10.0（2026-08-30）全产品零摆设攻坚

### 任务：每个入口都必须真能用（真功能 / 诚实禁用 / 删除），FEATURE_STATUS.md 逐行登记

- P0-1 工作流导出：自动化页 JS 重建丢失导出按钮 + 零绑定 → 补页面模板与绑定（下载全部工作流 JSON，
  空数据禁用并提示）；P0-2 片段「展开查看」：静态占位零处理 + 动态卡委托挂空（#snippetsList 不存在）
  → 委托改挂稳定容器 + 动态卡真展开（完整代码+高亮+箭头旋转+收起）+ 占位兜底；P0-3 图片按钮选择器
  title 错配 → 按稳定 id 接线（attachImageBtn/2）；P0-4 语音不支持时禁用+诚实标注；P0-5 shortModelName
  复验为纯字符串。
- 对比页同步滚动开关为无绑定假开关 → 真实现双栏 scrollTop 联动 + compareSyncScroll 持久化 + toast。
- 巡检补证：提示词/片段收藏落库、删除闭环（DB+重渲染）、useInChat 插入输入框、成本导出（toast 实测）、
  文档设置弹层、⌘K 命令逐条、audit 101 元素 0 问题、1280/768/375 无溢出、浅色主题 7 页正常。
- 交付 FEATURE_STATUS.md（模块×功能×状态×触发×验证×备注 全量清单）。

## v3.9.1（2026-08-30）左侧会话栏 Trae 化 + 会话项精简

### 任务：醒目新建主按钮 / 分组呈现为项目 / 会话项去模型药丸（增量修改，逻辑与数据结构不变）

- **[需求1 主按钮]** #sidebar-chat 顶部新增整宽「新建对话」主按钮（plus 图标 + 文案，h-9，btn-primary 品牌主色，
  hover 品牌色发光 `rgba(91,140,255,0.45)`，active 下沉 1px）；右侧 chevron-down「更多」下拉（surface-floating +
  shadow-card 浮层）：从模板新建（openConversationTemplates）/ 新建项目（openGroupManager），点击外部/Esc 关闭、
  stopPropagation 防冒泡；header 原两个不显眼小按钮（newSessionBtn/newFromTemplateBtn）移除，能力并入主按钮与下拉。
  Ctrl+N（新增监听，preventDefault）与 ⌘K「新建对话」统一走 createNewConversation——后者通过包装全局
  `executeCommand`（__traeWrapped 防重入）把 new-chat 命令从"仅切页"升级为真实新建，其余命令原样透传。
- **[需求2 项目呈现]** 底层仍用 conversation.group + filterByGroup/openGroupManager/showMoveToGroupMenu（零新结构）：
  分组栏 add-group 入口 tooltip 改"新建项目 / 管理项目"；分组 chip tooltip 追加"（项目）"；筛选/移动/管理面板
  文案"分组"→"项目"（项目：X / 移动到项目 / 新建项目（分组）标题）。新建项目→chip 出现→点 chip 筛选→
  会话"移动到项目"→刷新后归属保留，全链路实测通过。
- **[需求3 会话项精简]** renderSessionList 的 itemHtml 删除 `.session-model` 蓝色药丸（模型名改挂会话项
  `title="模型：xxx"` 原生提示）；新增设置项 `showSessionModel`（默认 false，打开才渲染药丸）；
  shortModelName 加固为 `const p=model.split(':'); return p[0]||'默认'`（始终返回字符串）。
- **[需求4 观感]** 会话项 padding 10px→8px、margin-bottom 2px→1px、预览间距收紧；时间分组标题/选中左指示条/
  hover 背景沿用现有样式与色值，浅色主题照常。

### 自测（a–g 全过，详见交付报告）

a) 组装×2（本地 + --web web）成功、浏览器编译无错；b) 主按钮新建→顶部新会话+欢迎页→发消息自动命名+入库
（DB 实测 user+assistant 两条）；c) 下拉两项可达、模板弹窗打开、新建项目→chip→筛选→移入→刷新保留全链路 ✓；
d) 无模型药丸、title 提示、时间/预览/四操作、shortModelName 返回字符串 ✓；e) Ctrl+N/⌘K 新建/搜索/筛选/
拖拽 draggable/移动端抽屉样式保留 ✓；f) 浅色主题、切页往返、audit 0 问题、无 lucide 警告 ✓；g) 两份产物同步 ✓。

## v3.9.0（2026-08-29）BYOK 在线版：一键部署 + 云端引导收口 + 跨域代理帮助

### 任务：做成"免费静态托管直接部署、任何人打开网址填自己的 Key 即可用"的在线版（本地 file:// 双击版完全不变）

- **[一 跨域代理]** 新增 `deploy/worker.js`（Cloudflare Workers 通用透传代理：`/<api-host>/<path>` 转发 +
  SSE 流式原样透传 + CORS 头，**不存储/不记录任何 Key**，零依赖）；新增 `deploy/README.md`（Cloudflare 五步部署、
  baseURL 代理地址填写规则 `https://worker.workers.dev/服务商域名/v1`、隐私与费用说明）。
- **[二 一键导出]** assemble.py 新增 `--web [目录名]`（默认 web/；无参数=仅本地构建，原行为不变）：
  导出 vendor 五库、sw.js、线上版 manifest（start_url "./"，本地版保持 ./moray-workbench.html）、
  成品 html 同时输出为 index.html（默认入口）与 moray-workbench.html 副本、deploy/ 随包携带；
  结束打印 web/ 文件清单与"拖到 Cloudflare Pages/Vercel 即可上线"提示；缺运行时依赖仍非零退出。
- **[三 引导收口]** ConnectionGuide 引导卡重写为"云端 BYOK 主路径"：选服务商预设（自动填 baseURL 与默认模型）→
  粘贴 Key（密码框 + 显示/隐藏）→ 测试连接（复用 testCloudConnectionDetailed 与 401/402/429/404 友好提示）→
  成功出现"开始第一次对话"（卡移除 + 输入框聚焦）；本地 Ollama 三步降级为 <details> 折叠次要项；
  "本次不显示" dismissed 记忆与设置页重新引导入口保留；服务商预设补全为 7 家（新增 OpenRouter，
  顺序 DeepSeek/OpenAI/Kimi/智谱/OpenRouter/硅基流动/通义，Key 始终用户自填，绝不内置）。
- **[四 代理帮助与隐私]** 设置页 OpenAI 区新增"浏览器报跨域(CORS)怎么办?"折叠帮助（代理规则说明 +
  Worker 域名/服务商输入 → 一键拼出代理 baseURL 填入）；testCloudConnectionDetailed 在 https 下
  Failed to fetch 时追加 CORS→代理建议；引导卡与设置页各加隐私说明（Key 仅存本地/直连仅发所选服务商/
  第三方代理需自担信任）。
- **[五 双环境]** SW 预缓存清单补 ./index.html 与 ./，导航离线回退链增强（index.html → moray-workbench.html → ./）；
  SW 注册保持相对路径（兼容根域名/子目录）；本地 manifest start_url 改为 ./moray-workbench.html（file:// 无 PWA 语义，无行为影响）。

### 自测（浏览器编译等价 node --check + 页面加载无错）

- web/ 核对 11 项全 PASS：index.html 为成品、线上 manifest start_url=./、worker.js 原样且零内置 Key、
  web/deploy 与工程 deploy 一致、README 含五步与 Key 规则、vendor 五库齐全、sw.js 存在。
- 引导卡 19/19：云端标题/预设/地址自动填/Key 显隐/测试成功→Go 出现→卡移除→输入框聚焦/隐私说明/Ollama 折叠。
- 设置页：7 家预设含 OpenRouter；CORS 帮助折叠 + 拼地址（my-proxy.workers.dev + DeepSeek →
  https://my-proxy.workers.dev/api.deepseek.com/v1）；隐私说明；__morayAudit 94 元素 0 问题。
- 线上版入口 /web/index.html 实际加载：应用启动、缓存读写、引导卡渲染、audit 0 问题、无控制台错误。
- 本地版（moray-workbench.html 入口）全功能回归同轮通过（同产物、路径全相对）。

### 已知限制

- file:// 协议下 SW 不注册（既有拦截），PWA 语义仅线上有效；本地双击版功能与线上版完全一致。
- Worker 代理为通用透传：请用户自建或使用可信代理，MoRay 不提供公共服务端（BYOK 原则）。

## v3.8.2（2026-08-29）交互健壮性专项：空值安全绑定 + 运行时点击巡检 + 交互自检

### 任务：消除"单点元素缺失拖垮整片按钮"隐患，全页面点击巡检兜底

- **[任务A 空值安全绑定]** 新增全局助手 `on(el, type, fn, opt)`（parts/10_db.js 通用工具区：元素存在才绑定，
  缺失静默跳过并返回元素）；括号/字符串感知脚本把 **119 处裸链式绑定**（`querySelector/getElementById/$()` 直接
  `.addEventListener`）全部转为 `on(RECEIVER, ...)` 形式，事件回调逻辑一字未改；转换后裸链计数 **119 → 0**。
  覆盖：网关卡 14 处、对比页 14 处、增强 13 处、boot 33 处、polish 9 处、成本 8 处、片段 8 处、工作流 7 处、
  对话 4 处、DB 3 处、搜索/工具/AI 各 1-2 处。
- **[任务B 运行时点击巡检]** 浏览器逐页真实点击：对话 95、提示词 33、片段 27、自动化 33、设置 47、
  知识库 4、成本中心 7，加导航切换与全局交互共 **286 次点击 0 控制台错误**；7 个视图切换 active/visible 一致；
  ⌘K 面板打开/搜索（"成本"→2 结果）/Esc 关闭/遮罩点击关闭、toast 通知均正常。
- **[任务C 交互自检]** 新增 `window.__morayAudit()`（parts/110_polish.js，仅手动调用）：遍历 data-op /
  data-action / 导航 data-page / data-modal-close / data-cost-range / data-view 元素，按三张分发表
  （消息 MSG_OPS / 会话 SESSION_OPS / 卡片 CARD_OPS）与已知 action 清单校验，无处理器/目标缺失以
  console.warn 列出（含文字与选择器）。实测：171 个可交互元素（当前视图 95），**0 问题**。
- **[任务D 键盘与状态一致性]** 复验 Esc 关闭弹窗（全局监听生效）与命令面板（input Esc → closeCommandPalette）、
  遮罩点击关闭；修复 `showModal` 打开时焦点遗留背景元素的问题——焦点收进 modalBox（tabindex=-1 + focus）；
  生成中重复发送由 `AppState.generating` 守卫拦截并提示（既有机制确认有效）。

### 自测

- 组装成功（依赖校验 7/7，图标校验 104 全有效）；浏览器加载无错（__errs 空）。
- 巡检发现并处理：巡检点击设置页全部 toggle 后 toolsDisabled 被全量禁用（on() 加固使点击全部生效的副作用），
  测试后已还原；backend 探测状态与 key 时效为环境因素，不属代码缺陷。
- 回归：工具调用全链路（calculator 2 轮成功）、缓存/成本、全局搜索、向导、主题均正常。

## v3.8.1（2026-08-29）工具超时机制 / 智能路由锁定 / PWA 原子构建 / 图标校验 / 沙箱文案

### 任务：代码审查第二轮 5 项缺陷修复（按 工具超时 → 智能路由 → PWA → 图标 → 沙箱 顺序）

- **[1 工具超时/取消机制]** `ToolRegistry.run` 重构：
  - 删除错误的 `ctx.signal.abort()` 调用（AbortSignal 无 abort 方法，5s 定时器触发必抛 TypeError）；
  - 内部自建 `AbortController`（internal），外部 signal 已中止则立即 internal.abort()，否则监听 abort 联动（用户停止）；
  - 5s 超时到点 internal.abort()（协作取消底层）并以"工具执行超时（5s）"settle；所有结束路径 try/finally
    `clearTimeout` + `removeEventListener`，杜绝定时器泄漏与延迟异常；
  - 三类结果可辨：用户取消=AbortError（传播，循环立即停）、超时=超时文案（不传播，循环继续）、工具错误=原始错误；
  - 传给具体工具的 ctx.signal 为合并后的 internal.signal（run_workflow/save_snippet/query_knowledge_base 的
    aborted 检查同时覆盖外部取消与超时）；与 runWithTools 的 toolCtl 链路兼容（opts.signal→toolCtl→internal）。
- **[2 智能路由锁定]** 会话新增独立布尔字段 `userPickedModel`（默认 false，随会话持久化；旧会话无字段按 false）：
  三处 createConversation 初始化（新建 false / 分支继承源会话 / ChatGPT 导入 false）；会话模型弹窗下拉新增
  "（跟随默认模型 / 自动路由）"空选项——选空=清除手选（model='' + userPickedModel=false 恢复自动路由）、
  选具体模型=手选锁定（userPickedModel=true）；请求透传改 `_userPicked: conv.userPickedModel === true`；
  全局默认模型设置与路由总开关不改变该状态。根因：conv.model 写入 defaultModel 非空导致 `!!conv.model`
  恒 true，路由永远被短路。
- **[3 PWA 预检 + 原子构建]** assemble.py：读 sw_template.js 前 `os.path.exists` 预检，缺失打印清晰中文错误
  并以非零码退出（不抛原始 traceback）；html/sw.js/manifest 全部先写 .tmp，通过运行时依赖校验后 `os.replace`
  原子替换，任一步失败删除临时文件、保留上一版正式产物（实测移走 sw_template 时正式 html 时间戳不变）；
  manifest 相对链接与 register('sw.js') 相对 start_url 复核正确，file:// 拦截与 .catch 兜底保留。
- **[4 Lucide 图标]** 全量校验确认仅 2 个本地库不存在的图标名：`code-review → search-code`、
  `message-square-question → message-circle-question`（HTML 模板 3 处，assemble.py 构建期替换，不手写 SVG）；
  assemble.py 新增构建期图标校验：正则提取全部 data-lucide 名，kebab→PascalCase 对照 vendor/lucide.min.js，
  无效即构建失败（修正了单字母图标 X 不被正则匹配的提取缺陷）。
- **[5 沙箱文案如实化]** Worker 源码额外置空 `globalThis.Function/AsyncFunction/GeneratorFunction`（尽力加固，
  不宣称绝对安全）；文案统一改为"受限执行环境：在独立 Worker 线程运行，与页面 DOM 隔离并屏蔽常用网络 API，
  适合运行你自己信任的纯计算 JS；它不是安全边界，无法保证拦截恶意代码，请勿运行来源不明的代码"，删除夸大表述。

### 自测（node --check 等价：浏览器 new Function 编译 + 页面加载无错）

- 工具：正常完成（calculator success）后等待 >5s 控制台无任何延迟异常（clearTimeout 生效）；
  6s 睡眠工具被 5s 超时中止、步骤卡标记"工具执行超时（5s）"、循环继续到最终答（round=2）；
  用户停止 <1.5s 立即 AbortError、不进第二轮。
- 路由 4 场景：①新会话（conv.model=defaultModel 非空但 userPickedModel=false）→ 路由实际生效
  （routeInfo.type=simple，不再被默认模型短路）；②手选（userPickedModel=true）→ 不被路由覆盖；
  ③持久化后状态一致（DB 读回仍锁定）；④清除手选（userPickedModel=false）→ 恢复自动路由。
- PWA：移走 sw_template.js → "构建失败：缺少 PWA 模板文件 sw_template.js…" + exit 1 + 正式 html 未动；
  正常构建后 manifest 链接存在、sw.js 注册函数在、无 404。
- 图标：构建期校验 104 个图标名全部有效；产物与服务端 HTML 均无旧图标名；控制台无 "icon name was not found"
  警告（运行中工作流面板的静态占位由 JS 动态重建，属正常机制）。
- 沙箱：fetch 调用抛"沙箱已禁用网络"；`new Function` 报错（构造器已置空）；纯计算 `2+2=4`、
  console 捕获正常；界面文案为如实表述。
- 回归：缓存回放、成本中心、⌘K 搜索、向导、主题、网关卡/工具卡、对比面板、历史步骤卡重建（含复制按钮）
  均正常；F12 Console 无红色报错。
- grep 自证：`ctx.signal.abort(` 残留 0；`_userPicked: !!conv.model` 残留 0（改用 userPickedModel，8 处）；
  旧图标名 data-lucide 残留 0。

### 已知限制与未做项

- 沙箱仍非强隔离：纯前端 Worker + eval 无法构成安全边界，仅屏蔽常用网络 API 与函数构造器；
  若未来要真正安全执行不可信代码，推荐独立 sandbox origin + 严格 CSP，或后端隔离执行（本轮未实现，属刻意范围外）。

## v3.8.0（2026-08-29）代码审查 7 类缺陷修复

### 任务：网关选择器 / 路由短路 / IDB 降级 / 精确 token / 工具可取消 / 构建校验 / 沙箱安全

- **[一] 网关设置选择器**：`const $ = (id) => card.querySelector(id)` → `querySelector('#' + id)`；
  全部 `$()` 调用均为纯 id（无 #），逐一确认 10 个控件（网关总开关/缓存开关/TTL/容量/语义缓存开关与阈值/
  路由开关/超时/降级日志/用量导出等）事件绑定生效并可持久化。
- **[二] 智能路由短路**：`opts.model` 短路改为"显式锁定"语义 `opts._userPicked === true`（30_chat 组装请求时
  传 `_userPicked: !!conv.model`；A/B 测试显式指定模型也锁定）；非锁定时 TaskRouter/本地优先/超预算降级照常决策，
  决策为空时回退 defaultModel/首个可用模型，绝不返回空模型；`local[0]` 修正；cheapest 初始化为当前
  decision.model（字符串），仅当真有更便宜模型才切换（修复"数组首项非最便宜时误切"的连带 bug）。
- **[三] IndexedDB 内存降级**：统一 `DB_STORES` 常量（8 个 store，含 settings/apicache），enableMemoryMode/
  importAll/clearStores/旧库迁移全部改用；`_memTx` 经 `_memStore()` 惰性创建 Map（杜绝 undefined 崩溃）；
  `open()` 探测 IndexedDB 可用性（被禁用/隐私模式/配额异常）→ 自动进入内存模式 + 提示"内存模式
  （刷新后数据不保留）"，不报错不白屏；`_tx` 在 open 后二次检查降级。
- **[四] OpenAI 流式精确 token**：SSE usage 分支补 `state.completionTokens`；`_finish` 的 tokens 优先
  服务端精确值（completion_tokens → Ollama eval_count → 文本估算兜底），prompt/cached 已有捕获保持一致；
  非流式 chat 分支本就使用 usage 精确值。
- **[五] 工具调用可取消**：runWithTools 为每次工具执行创建 AbortController（外部停止即 abort）；
  ToolRegistry.run 的 5s 超时先以"超时失败"settle 再 abort ctx.signal 协作取消（顺序修复：先 abort 会被
  误判为用户停止导致超时变中止）；去掉重复的 5s 定时器消除竞速；可中断工具（query_knowledge_base/
  save_snippet/run_workflow）在执行前与关键 await 后检查 `ctx.signal.aborted` 抛 AbortError；
  超时=工具失败（循环继续），停止=立即 AbortError（循环中断、回可输入态）。
- **[六] 构建依赖完整性校验**：assemble.py 构建后校验 vendor 5 个库 + manifest.webmanifest + sw.js，
  缺任一即 exit 1 并打印缺失项；支持 `MORAY_OUT_DIR` 输出目录自动拷贝运行时依赖（单拷 html 离线可用）。
- **[七] JS 运行器沙箱**：Worker 源码最前面注入禁用 `fetch / XMLHttpRequest / WebSocket / EventSource /
  importScripts / navigator.sendBeacon`（全部抛"沙箱已禁用网络"）；界面文案改为如实描述
  "Web Worker 沙箱：与页面 DOM 隔离并已禁用网络，仅适合运行你信任的纯计算 JS；仍不要运行来源不明的代码。"

### 自测（node --check 等价：浏览器 new Function 编译 + 页面加载无错）

- grep：`querySelector('#'+id)` ✓；route 无 `opts.model` 短路（`_userPicked === true`）且 `local.name` 残留 0；
  `DB_STORES` 含 settings/apicache；`completion_tokens` 捕获；runWithTools 透传 signal（toolCtl）；Worker 内
  禁用 fetch/XMLHttpRequest/WebSocket 等 6 项。
- 构建拦截：临时移走 vendor/lucide.min.js → `构建失败：缺少运行时依赖：vendor/lucide.min.js` + exit 1（已恢复）。
- 内存模式：模拟 indexedDB.open 抛 SecurityError → memoryMode=true、open 返回 null、会话/设置读写正常、
  apicache 惰性 Map 不崩溃。
- 网关控件：10 个控件元素绑定齐全；缓存开关点击切换并持久化、还原成功。
- 路由：无锁定时简单任务→deepseek-chat（routeInfo.type=simple）；`_userPicked:true` 锁定 qwen2.5 不被改写；
  未配置路由回退非空模型；本地优先→llama3.2（local[0]）；超预算→降级 reason 含"超预算"。
- 精确 token：`_finish` 优先 completionTokens=42/promptTokens=100/cachedTokens=7；无服务端值回退估算。
- 工具：挂起工具 5s 超时 → 步骤卡"超时"失败 + 循环继续（第二轮最终答）；用户停止 → <1.5s AbortError、
  不进第二轮。
- 回归：缓存回放（_cost.fromCache）、成本中心、⌘K 搜索、向导、主题、网关卡/工具卡、对比面板、历史步骤卡
  重建（含复制按钮）、RAG/片段/工作流工具均正常；F12 Console 无红色报错。

## v3.7.1（2026-08-29）Function Calling 健壮性收口与一致性打磨

### 任务：兜底降级 / 对比一致性 / 步骤卡体验 / 立即中止

- **[兜底降级]** `Gateway.chatStream`：工具模式（useTools）抛错且本次尚未兜底时，自动去掉 tools 改用现有
  普通流式 attemptFn 重试一次（请求级闭包标志位 `toolsFallbackTried`，最多一次防死循环），并低调提示
  "当前后端不支持工具调用，已切换普通对话"；兜底也失败才走原有备用模型/报错链路；
  **用户主动中止（AbortError）不触发兜底**（避免把中止误解为后端不支持）。
- **[对比一致性]** 对比主链路（`AI.chatStream` 直连）天然无 tools；A/B 测试路径（走 Gateway）显式传
  `noTools: true` 强制关闭工具闭环（`useTools` 判断新增 `!opts.noTools`）；对比输入区下方新增灰色提示
  "对比模式不启用工具调用"（boot 在 `CompareApp.build()` 之后注入，避免被面板重建清掉，幂等）；
  普通对话与"重新生成"仍保持工具闭环可用。
- **[步骤卡复制]** 每个工具步骤卡新增"复制"小按钮：事件委托（`ToolRegistry._installCopyHandler`），
  复制**完整结果**（`resultFull` 字段，上限 10000，预览仍截断 300 展示）而非截断预览；复用
  `copyToClipboard` + `showNotification` toast。
- **[工具执行中可中止]** 工具 running 事件时发送按钮切换为"工具执行中…"（animate-pulse，点击中止，
  复用 `AppState.genController.abort()`）；`ToolRegistry.run` 支持 `ctx.signal` 竞速（abort 立即 reject
  AbortError 并向上传播，不等工具自然结束/超时）；生成完成/中止/失败后由 `generateAssistantReply` 的
  finally 统一 `resetToolUI()` 恢复按钮。

### 自测（node --check 等价：浏览器 new Function 编译 + 页面加载无错）

- 兜底：mock `AI.chat` 抛 "400 tools not supported" → 自动切普通流式（AI.chatStream 被调 1 次）、
  返回普通回答、提示出现、无报错；**第二次请求再次兜底**（标志位为请求级，不跨请求泄漏）；
  兜底也失败时走原有报错链路（不吞错）。
- 中止：3s 慢工具执行中 `controller.abort()` → **<1.5s 立即**以 AbortError 停止（不等工具自然结束）、
  不进入第二轮；UI 层发送按钮"工具执行中…" → 点击 → 消息"已停止生成" → 按钮还原（arrow-up+onclick）、
  计数复位。
- noTools：`Gateway.chatStream({noTools:true})` 且工具开关开 → AI.chat 未被调、无步骤事件；
  对照组（无 noTools）工具闭环正常（2 轮、1 个终态步骤）。
- 复制：步骤卡复制按钮存在；`data-full` 为完整 500 字符（非 300 截断）；点击复制回调收到完整内容；
  历史消息重建后复制按钮与完整结果仍在。
- 对比提示："对比模式不启用工具调用"注入于 CompareApp.build 之后（早期注入会被面板重建清掉，已修）；
  A/B 测试路径同样不触发工具。
- 回归：缓存命中回放（逐字+`_cost.fromCache`）、成本中心页、⌘K 搜索、Onboarding v2、深浅主题、
  设置页（AI 工具卡 6 行开关）、对比面板均正常；关总开关请求体无 tools；F12 Console 无红色报错。

## v3.7.0（2026-08-29）内置工具调用（Function Calling / Agent 工具闭环）

### 任务：OpenAI/DeepSeek 兼容 tools/tool_calls 协议 · 纯前端内置 · 0 外部依赖 · 离线可用

- **[新增]** `parts/125_tools.js`（ToolRegistry，assemble 清单已注册、加载在 gateway 之后）：
  统一工具描述 `{name, label, description, parameters(JSON Schema), enabledByDefault, run}`；
  `listForRequest()`（总开关+每工具开关过滤，关闭时返回空数组 → 请求体绝无 tools 字段）、
  `get/run`（每个 run 包 try/catch + 5s 超时保护，返回 `{ok,data}` / `{ok:false,error}`，单工具失败不崩整轮）。
- **[新增]** 阶段A 工具：`get_current_time`（日期/星期/时间/时间戳，零权限）；`calculator`（安全算术求值——
  自写词法+递归下降解析器，仅数字、`+ - * / % ( )` 与白名单 Math 函数 sqrt/pow/abs/round/floor/ceil/min/max；
  **禁 eval/Function/字符串执行**，字母标识符（非白名单）、赋值、require/process/globalThis/分号/引号一律拒绝）。
- **[新增]** 阶段B 工具（全部复用现有能力，零重写）：`query_knowledge_base`（复用 DocsApp.semanticSearchRaw）、
  `save_snippet`（复用 DB.createSnippet，title≤100/内容≤20000 上限）、`run_workflow`（复用 WorkflowEngine.run +
  DB.listWorkflows，运行后回读最近记录状态）、`get_cost_usage`（复用 UsageTracker/CostEngine，人民币口径）。
- **[集成]** `Gateway.chatStream` 新增 `runWithTools` 工具循环（对象内方法，最少侵入）：
  仅"总开关开 + AI.backend==='openai' + 有可用工具"时启用；Ollama 后端/不支持 tools/请求报错 → 自动回退
  现有普通流式链路（重试/降级/流式体验完全一致）。缓存命中回放路径不改动；工具轮结果不写缓存（时间/库内容敏感）。
  循环：非流式请求带 tools → 解析 tool_calls → 逐个执行 → assistant(tool_calls)+role:"tool" 按协议回灌 →
  再请求，最多 `toolsMaxRounds`（默认3）防死循环；模型给出最终答时**分段模拟流式**输出给 onChunk（逐字体验不变）。
  每轮 token 均经 CostEngine 核算 + UsageTracker 记录（当轮真实值，await 防并发覆盖）→ 计入成本中心。
- **[UI]** 工具步骤卡：该条 AI 消息、最终回复之前按序渲染可折叠步骤（图标+中文名+参数摘要+耗时）；
  进行中 `animate-pulse`、成功 success、失败 danger（展示错误不阻断最终答）；结果可展开预览（截断）。
  实时事件 `opts.onToolStep` 驱动（30_chat.js 接入），消息 `toolCalls` 字段持久化（DB 结构化克隆，
  旧消息无字段正常显示，向后兼容）；重开会话/重建消息列表步骤卡仍在。
- **[设置]** 设置页新增卡片"AI 工具（Function Calling）"：总开关、最大工具轮数（1-10）、每工具独立开关；
  关闭总开关时请求体不携带 tools（已验证）。默认**关闭**，开启不影响任何现有功能。
- **[修改]** `AI.chat` openai 分支支持 `opts.tools` 透传 + 解析 `tool_calls` 返回 + 回传 `promptTokens`（不带 tools 时行为与之前完全一致）。

### 自测（node --check 等价：浏览器 new Function 编译 + 页面加载无错）

- calculator：正常 `1+2*3`=7、`(128+256)*2`=768、`10%3`=1、`sqrt(16)`=4、`pow(2,10)`=1024、`min(3,1,2)`=1、
  `abs(-5)`=5、`-5+3`=-2、`2.5*4`=10、floor/ceil/round 均正确（12/12）；拒绝 `require("fs")`/`while(1)`/`process.exit()`/
  `globalThis`/`a=1`/`1;2`/`'x'`/`2**3`/`eval("1")`/`Function("1")`/`1/0`/`Math.max(1,2)`（12/12）。
- mock 工具循环：2 轮（tool_calls → 执行 → 回灌 → 最终答）；步骤事件 running→success 按序；
  分段流式（80 字符/块 + done）；`_cost` prompt=210/completion=70/累计成本正确；UsageTracker requests+2（两轮均记）。
- chatStream 集成：开总开关（openai 模拟）→ 工具轮走 AI.chat 非流式（AI.chatStream 未被调）、onToolStep 事件齐全、
  `_toolSteps` 返回；关总开关 → 请求无 tools、无步骤事件、普通流式原样；缓存命中回放不受工具开关影响（命中仍回放、cost=0）。
- UI 完整流程（mock）：发送 → 步骤卡实时渲染（"计算器 {(128+256)*2} 17ms 成功"，位于最终回复之前）→ 最终答 markdown 正常 →
  `assistantMsg.toolCalls` 落库 → DB 往返 → 重建消息列表步骤卡仍在；token 合计（105/37）计入消息标签成本。
- B1 工具实测：get_cost_usage 返回 ¥1.20（今日/本月，与成本中心一致）；save_snippet 真实写入并可从库中读回；
  query_knowledge_base 检索 3 条；run_workflow 列出 6 个工作流；未知工具/未启用工具/除零均优雅返回错误；5s 超时生效。
- 回归：普通对话（无后端离线占位）、缓存命中回放、成本中心（¥/导出）、⌘K 搜索、Onboarding v2、深浅主题、
  设置页、多模型对比面板均正常；F12 Console 无红色报错。

### 已知限制与后续扩展

- 联网搜索工具未实现：注册中心已预留扩展点（ToolRegistry.register 即可新增；搜索需先解决后端 key/隐私策略）。
- 工具循环仅 openai 兼容后端（Ollama 不支持 tool_calls 时自动跳过，链路与之前逐字节一致）。
- 历史消息的工具步骤不回放为请求上下文（仅展示）；工具轮结果不写缓存，同问题会重新执行工具。
- 后续可扩展：接入 MCP/本地命令需按安全策略单独设计；更多工具（文档摘要、翻译、定时提醒等）可直接注册。

## v3.6.1（2026-08-29）清理 Gateway 死代码（对象内被后置覆盖的占位实现）

### 任务：纯删除冗余，不改任何运行时行为

- **[删除]** `parts/100_gateway.js` 对象字面量内两段被后置赋值覆盖、永不执行的占位实现（含 JSDoc 与相邻空行）：
  - 旧版 `chatStream(opts){...}`（637-706 行）：缺 CostEngine.calculate/cachedTokens/_cost 的早期版本，被 767 行"修正版" `Gateway.chatStream = function` 覆盖；
  - 占位版 `_playback(rec, controller, info){...}`（708-733 行）：循环内未调用 `opts.onChunk`，用 `void started; void self; void chunkSize;` 压制未用变量，被 737 行"覆盖上面的占位循环"的 `Gateway._playback = function` 覆盖。
- 保留对象闭合 `};` 与两个生效实现：生效 `_playback`（`this._currentOpts` 取 onChunk 逐字回放）与生效 `chatStream`（含 CostEngine `_cost` 核算）。
- 同类排查：`parts/` 下其余同名后置赋值（`UsageTracker.record`、`HealthMonitor.checkAll`、`HashVectorWorker.embedBatch`、`attachAutoPagination` 的 app.render/bindEvents、`window.openCommandPalette`）均为**有意包装增强**（绑定原实现继续执行 + 追加逻辑），非死代码，保留；`Onboarding.openV2` 为对象内无同名定义的新增方法。

### 自测

- 备份 `backup/pre_gateway_dedup_20260829_153227/`；组装 16768 → 16671 行（净删 97 行，app_js 538744 → 533958 字符）。
- grep：`Gateway.chatStream = function` 与 `Gateway._playback = function` 各仅剩一份定义 + 必要调用（`AI.chatStream` 为 AI 类的不同方法），对象内占位版零残留；浏览器解析（new Function 等价 node --check）通过。
- 缓存命中回放链路（API 层 + UI 层）：命中 exact/语义缓存均走生效 `_playback`，onChunk 逐字分块（80 字符/块、24ms 间隔、done 结束块）打字效果正常；`_cost = {cost:0, baseline:0.0005, saved:0.0005, fromCache:true}`（命中免费 + 省¥估算）；UI 消息"来自相似缓存 · 相似度 100% · 省 66 tokens"。
- 消息成本标签渲染 `["66 tok", "¥0", "缓存命中 省<¥0.01"]`，无 `$` 残留。
- 回归：普通发送无后端时优雅离线占位（不崩溃）；路由分类 simple；多模型对比页、⌘K 全局搜索、成本中心（¥/导出）、设置、主题切换、Onboarding v2 三步向导均正常；Console 无红色报错。

## v3.6.0（2026-08-29）统一成本/预算为单一人民币体系

### 任务：以 CostEngine（¥、PRICE_TABLE）为唯一定价源，增量改造旧 USD 体系

- **[重构]** `UsageTracker.estimateCost` 委托 `CostEngine.estimateCost`（人民币），签名不变；调用方无需改动。
- **[重构]** `UsageTracker.record` 新增人民币聚合：`d.costCNY / d.savedCNY / d.baselineCNY` 与 `byModel[m].costCNY`；
  `summarize` 与 CSV 导出优先 `costCNY`，历史日（无该字段）回退 `d.cost`，旧存储字段保留、向后兼容。
- **[修复]** 核心 bug：`installHourlyTracking`（供热力图）包装 `UsageTracker.record` 时签名缺 `costInfo` 第 4 参数，
  导致 `costCNY/savedCNY/baselineCNY` 永远累加不上（byModel 只能回退 estimateCost 值）。
  → 包装函数补上 `costInfo` 透传，实测 `d.costCNY` 从 0 恢复为正确累加（1.2）。
- **[重构]** 预算唯一化：合并为 `costBudgetCNY` + `CostBudget`（保留 autoDegrade 超预算降级与 pauseAPI
  暂停云端能力）；移除旧 `UsageTracker.checkBudget` / `budgetReachedModal`；网关设置卡预算行改为只读
  ¥ 展示 + 链接"唯一预算入口在 成本与省钱"。
- **[移除]** 旧美元定价设置 `defaultPriceIn / defaultPriceOut / modelPrices / monthlyBudget` 及其 UI；
  未知模型由 `PRICE_TABLE` default 档兜底，自定义价格仍走 `priceOverrides`。
- **[UI]** 全部费用展示 $→¥：底部状态栏今日用量、网关设置卡（今日费用/缓存节省/本月费用）、
  成本中心页、数据洞察面板累计费用、消息成本标签、CSV 表头（cost_usd → cost_cny）。
- **[修复]** `CostEngine.calculate` 中 `estimateBaseline` 第 4 参数移除；Gateway `_cost` 使用 `cachedTokens`
  而非硬编码 0（v3.5.0 任务0 的落地收尾）。

### 自测

- 备份 `backup/pre_cny_unify_20260829_150020/`；parts 全部改动后重新组装（app_js 538744 字符），浏览器解析语法通过。
- 残留 grep（组装产物）：`monthlyBudget / defaultPriceIn / defaultPriceOut / modelPrices / budgetReachedModal /
  checkBudget / cost_usd / USD` 全为 0；`$` 仅剩模板插值、正则锚点与 `$1` 替换引用，无任何费用美元。
- 数值：local=0；deepseek-chat 1M入+1M出=¥10；缓存 50 万 token=¥9.25；gpt-4o 全量=¥87.5；未知模型=¥10（default 档）；
  record 后 `d.costCNY=1.2 / savedCNY=0.4`、byModel.costCNY=1.2、summarize('today')=1.2（优先人民币）。
- 三处金额一致：成本中心页（今日/本月/累计 ¥1.20、累计节省 ¥0.4000、明细 ¥1.2000/省¥0.4000）、
  设置页用量与预算（今日费用 ¥1.20、¥1.20/未设）、页面渲染 $ 计数=0。
- 预算单次触发：100% 弹窗仅出现一次（_notified 防重）；"暂停云端 API"→ pauseAPI=true 且弹窗关闭；
  预算调档 50% 只通知不弹窗；"继续使用"→ 关闭且本期不再提醒；测试后状态还原。
- 回归：消息成本标签（本地 ¥0，无 $）、Gateway.route 正常、⌘K 搜索面板打开并过滤出"成本与省钱中心"直达项、
  Onboarding v2 打开正常；全程无新增错误。

## v3.5.0（2026-08-29）CostEngine 缓存口径修复 + ⌘K 统一全局搜索

### 任务0：estimateBaseline 缓存口径修复
- estimateBaseline 不再重复累加 cacheTokens：基线 = 全部输入按输入价 + 输出按输出价
  （注释注明"promptTokens 已含缓存命中部分"）；estimateCost 的 prompt-cacheTokens 语义保持不变。
- 进阶：AetherAI 流式解析 OpenAI usage.prompt_tokens_details.cached_tokens 与 usage.prompt_tokens
  （取不到为0），经 stats 传入 Gateway → CostEngine.calculate，使 cacheHit 折扣价真正生效；
  本地语义缓存完全命中（fromCache）仍 cost=0。
- 实测数值：deepseek-chat prompt=1e6 completion=1e6 cache=5e5 → estimateCost=**9.25**、
  estimateBaseline=**10**（修复前错误为11）；cacheTokens=0 的现有结果全部不变（0.006/0.006）。

### 任务1：⌘K 统一全局搜索 + 命令面板（PaletteSearch，parts/130_search.js）
- **数据源**（打开面板时按需异步读取，各自 try/catch，失败分组静默缺省，不缓存全量副本）：
  会话（标题+最近消息片段）、提示词（标题/内容/分类/标签）、代码片段（标题/代码/语言/标签）、
  工作流（名称/描述）、知识库文档（标题+首块摘要）、静态动作（现有全部命令项+成本中心）、设置直达（8项）。
- **匹配排序**：多关键词 AND；打分=标题连续命中4>词首+2>标签2>内容1；<mark class="cmd-mark"> 主色高亮；
  空查询=最近5会话+常用动作；查询时每组最多6条、总量封顶40。
- **键盘交互**：上下键跨分组移动（遍历实际渲染项）、Enter 执行、Esc 关闭、鼠标点击；输入防抖150ms；
  **竞态保护**：序号比对，只采纳最后一次查询（实测快速连续输入仅显示最终结果）。
- **执行动作**（全部复用现有函数，无第二套实现）：会话→openConversation、提示词→insertToInput、
  片段→跳片段库+复制、工作流→跳自动化+卡片高亮、文档→openDocPreview、设置→滚动+高亮卡片、
  动作→executeCommand（3个扩展项手动分发：自动化/成本中心/导出/切主题）。
- **健壮性**：空状态（图标+提示+"新建会话/搜索文档"兜底按钮）；结果数+分组统计头部；最近5条选中
  （localStorage 单键 moray_palette_recents）；所有动态渲染后 refreshIcons()。

### 自测
- node --check 等价（浏览器解析15个parts全部通过）；grep 无新增外部网络资源
- ⌘K 搜出会话/提示词/片段/工作流/设置并正确跳转/插入/复制；键盘上下+回车+Esc 全程可用；
  空库仅动作分组不报错；竞态快速输入无旧结果覆盖；静态命令、斜杠菜单、成本中心、向导、深浅主题回归正常

## v3.4.0（2026-08-29）成本与省钱中心 + Onboarding v2
## v3.3.1（2026-08-29）图标自递归修复
## v3.3.0（2026-08-29）file:// 双击死按钮修复 · 外部依赖健壮性

### 问题根因（已定位）
第二个内联脚本块块首裸调 lucide.createIcons()：lucide 仅从 unpkg @latest 加载，CDN 失败/断网时
window.lucide 为 undefined → 块首抛 ReferenceError → 同块后续全部绑定（导航/模式切换/列表点击/快捷键/通知）
不执行 = 整页死按钮；bootApp 内各渲染还有 72 处裸调，库缺失时在 try 块抛错终止整个启动链。

### P0-1 外部库统一安全封装
- 新增 `refreshIcons()`（try/catch + window.lucide 存在性判断），全文 72 处 `lucide.createIcons()` 全部替换
  （含块首、各 render、setTimeout 回调、导航切换回调），任何图标库缺失都不抛错中断业务
- marked/DOMPurify/hljs 渲染管线核查：均已有存在性守卫 + try/catch（renderMarkdown 库缺失时
  降级为转义纯文本+保留换行；代码块降级为等宽纯文本），对话内容永不全屏白屏

### P0-2 消除启动单点炸块
- 脚本块2块首裸调 → `refreshIcons()`；核心交互绑定（导航/模式/列表/快捷键/showNotification/createRipple）
  不再受外部库失败影响
- 其余脚本块顶层排查：无对外部 CDN 全局对象的无保护调用

### P0-3 外部依赖本地化（方案A，契合"本地工作台"定位）
- 新建 `vendor/`：lucide@0.468.0 / @tailwindcss/browser@4.1.7 / marked@12.0.2 / dompurify@3.1.6 /
  highlight.js@11.9.0 全部下载固定版本（避免 @latest 302 漂移），script/link 改为相对路径
- 移除 Google Fonts 依赖（@theme 已有 system-ui/monospace 系统栈兜底）
- 断网 / file:// 双击可完整运行；懒加载库（pdf.js/mammoth/prettier/transformers）保留 CDN 且有失败提示与回退

### P0-4 启动链韧性
- bootApp：新增 runStep(name, fn) 辅助，迁移/设置/数据库/各 App build+bindEvents/健康监控/恢复会话
  等 15 个步骤各自独立 try-catch，任一失败只记录 ErrorLog + 通知，其余模块照常绑定
- bootPhase1：每个 setup* 独立 try-catch，核心交互优先绑定

### P1 清理遗留
- "原型演示"文案 0 残留；"即将上线"0 残留（静态自动化页占位按钮已接真实 openEditor 入口）

### 顺带修复（验收中发现）
- 工作流编辑器"新建"路径：模板 `!wf.schedule` 对 null 取属性抛错（v2.2 引入的隐藏 bug，
  新建工作流一直打不开）→ 加空值保护

### 自测验收
- 断网等价（vendor 全就绪、零 CDN 请求）：5 库本地加载 ✓、6 页导航 ✓、新建会话/各编辑器/命令面板 ✓、
  图标 251 个 ✓、Markdown/代码高亮 ✓、零错误
- 库缺失降级（移除 lucide/marked 模拟 CDN 失败）：refreshIcons 不抛错 ✓、导航 6 页全部可切换 ✓、
  通知/面板/编辑器可用 ✓、Markdown 转义纯文本回退且无原始 HTML 注入 ✓、零 ReferenceError ✓
- 裸调扫描：lucide.createIcons() = 0；Google Fonts = 0；div 闭合差 = 0；页面零未捕获异常

## v3.3.1（2026-08-29）图标自递归修复

- **[修复]** refreshIcons 函数体误伤：v3.3.0 全局替换 `lucide.createIcons()` → `refreshIcons()` 时，
  把 refreshIcons 定义体内部的那一句也替换成了自身，形成被 try-catch 吞掉的无限递归，
  lucide.createIcons 实际从未执行 → 全站 data-lucide 图标空白。
  修复：函数体内 `refreshIcons()` 改回 `lucide.createIcons()`（唯一定义，自证无自调用）。
- **[核查]** 其余 73 处业务调用点保持 refreshIcons() 不变；renderMarkdown/DOMPurify/hljs
  安全封装内部调用各自底层库（marked.parse / DOMPurify.sanitize / hljs.highlightElement），无自递归。
- **[验收]** 修复后 svg 图标 254 个（`[data-lucide]` 匹配到的 250 个即已转换的 svg——lucide
  生成的 svg 自带 data-lucide 属性供重渲染识别）；6 页导航切换后图标均正常（46/29/41/31/9/40 个/页）；
  弹窗内图标正常；Console 无 RangeError / Maximum call stack；文件本地化与启动容错均未改动。

## v3.2.0（2026-08-29）全功能交互审计与死按钮修复
## v3.0.0（2026-08-29）体验打磨与健壮性优化
## v2.2.0（2026-08-29）API 智能网关 + 全面完善
## v2.1.0（2026-08-29）夜间深度优化

### 第一阶段：补全和稳定性修复
- **[修复]** `AetherSettingsGet_topK()` → `MoraySettingsGet_topK()`（定义+调用共4处）
- **[审计]** 全项目 aether 残留扫描：16处全部为迁移白名单代码，非法残留 **0**
- **[新增]** `VirtualScroller` 固定行高虚拟滚动组件（会话列表>50条启用：62会话仅渲染13个DOM节点，占位层高度精确反映总内容）
- **[新增]** 提示词/片段变高卡片列表**增量分页**（每页50，滚动到底自动追加；修复了滚动监听器挂载时机问题）
- **[新增]** 边界加固（Robustness）：
  - 离线/上线事件感知通知（按真实 navigator.onLine 判定）
  - 大文件上传拦截（>50MB 友好拒绝，实测60MB被拦截）
  - IndexedDB 配额不足一次性告警（QuotaExceededError 检测）
  - 多标签页同步（storage 事件：设置变更即时应用 + moray_tab_sync 哨兵刷新会话列表）
  - 隐私浏览模式降级提示 + IndexedDB 不可用时**内存模式兜底**（`DB.enableMemoryMode`，全CRUD路由到内存模拟层）
  - 快速连点发送防抖（400ms）
- **[修复]** `clearStores()` void回调导致 `_wrap(undefined)` 崩溃 → tx.oncomplete 模式 + 内存分支
- **[新增]** 大对话（>500条）归档清理提示 + `trimOldMessages()` 助手
- **[验证]** 数据完整性：导出→清空→导入回环（4会话/6消息/66提示词全部一致）；API Key 混淆存取回环；隐私模式0落盘（上轮修复后持续有效）

### 第二阶段：性能深度优化
- 代码块高亮**懒执行**（IntersectionObserver，提前200px预热，不支持时降级立即高亮）
- 长代码块（>30行）**默认折叠**，展开/收起按钮
- 大 Markdown（>16KB）**requestIdleCallback 分片渲染**
- Web Worker 闲置60s自动终止（防内存驻留）
- 页面隐藏（visibilitychange）暂停监控图表轮询
- CDN preconnect + 关键脚本 preload
- 极光光带/信标 `will-change` 合成层提示；系统级 `prefers-reduced-motion` 支持

### 第三阶段：用户体验打磨（精选）
- 消息**时间分隔线**（相邻消息间隔>5分钟，实测插入）
- 生成过程**实时 tok/s** 状态显示
- 消息操作增强：**引用回复 / 转发到新会话 / 收藏星标**（收藏面板跨会话汇总可跳转）
- 粘贴大段代码自动开启代码模式（语言检测）
- 全局 aria-label 自动补全（MutationObserver 低频扫描带 title 的按钮）
- 空状态引导优化（片段库等）

### 第四阶段：功能增强（精选）
- **多模态**：图片粘贴/拖拽/附件按钮 → canvas压缩(≤1280px) → Ollama `images` / OpenAI `image_url` 双协议注入，气泡内缩略图预览
- **语音输入**：Web Speech API 麦克风按钮（zh-CN，不支持的浏览器友好提示）
- **对话场景模板**：代码审查/学习辅导/写作助手/翻译专家（含专属系统提示词+欢迎语）
- **提示词市场**：10个本地精选模板分类一键导入；**版本历史**（保存时自动留5版，可回滚）
- **代码运行器**：JS Web Worker 沙箱（无DOM/网络，5s超时，console捕获+返回值+用时）
- **Prettier 格式化**：CDN 懒加载，JS/TS/CSS/HTML/JSON
- **片段分享链接**：内容Base64编码进URL hash，启动时检测自动导入
- **混合检索**：向量相似度70% + 关键词重合度30% 融合重排
- **工作流**：新增5个模板（代码解释/长文摘要/问答生成/周报生成/正则生成）；**定时调度**（间隔/每日，每分钟检查，后台不跑）；完成**桌面通知**
- **参数预设**：创意/平衡/精确一键应用

### 第五阶段：Tauri 桌面端
- `tauri.conf.json`：窗口 1280×800（最小 1024×640）、版本 2.1.0、fullscreen/resizable 显式配置
- `scripts/build.ps1` / `scripts/build.sh` 三平台构建脚本
- `BUILD.md` 完整构建指南（前置要求/各平台命令/图标替换/自动更新签名）

### 已知问题与后续建议
1. 离线提示依赖真实 navigator.onLine（合成事件测试无法模拟断网，真实场景正常）
2. 变高卡片列表（提示词/片段）采用增量分页而非严格虚拟滚动——两列不规则高度下虚拟滚动会造成滚动跳动，分页是更稳的方案
3. Python 代码运行需 Pyodide（~10MB），按需引入未默认开启
4. 语音输入依赖浏览器 SpeechRecognition（Chrome/Edge 最佳）
5. Tauri 图标仍为占位图，发布前需 `cargo tauri icon` 替换；updater 需配置真实端点与签名密钥
6. 会话拖拽排序未实现（置顶+时间排序已覆盖核心诉求，拖拽与虚拟滚动叠加复杂度高）
