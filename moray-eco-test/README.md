# moray-eco-test — MoRay 生态能力吃狗粮测试

> 创建时间：2026-10-27 · 由 MoRay 深度 Dogfooding 任务生成
> 目的：验证 MoRay 本地工作区能力（文件读写 / ZCode 沙箱 / 产物预览）能否支撑真实生态开发。

## 这个文件夹是怎么来的

本目录由 dogfood 会话直接写入 `D:\MoRay\` 工作区根目录，用于回答一个关键问题：

**MoRay 能不能像 Cursor / Codex 那样，让 Agent 的产物直接落到用户指定的工作区？**

答案（本次实测）：**部分能**。

| 能力 | 实测结果 |
| --- | --- |
| ZCode 沙箱写文件 | ✅ 通过。任务「写一个 Python 二分查找脚本」29.8s 完成，产物 `binary_search.py` 自测通过 |
| 产物落盘位置 | ⚠️ 落在 `workspace/zcode_sandbox/day24b_runs/<task_uuid>/`，**不是**用户说的"当前工作区"根目录 |
| 产物卡交互 | ✅ 本次已修：整卡可点 → 应用内预览（行号/复制/语法高亮） |
| 跨工作区产物 | ❌ 卡片只显示文件名，用户无法知道真实路径，也无法一键"在资源管理器中显示" |
| API 配置记忆 | ✅ 本次已修：服务商预设下拉落库 + 刷新回填（此前每次刷新都要重选智谱） |

## 痛点清单（本次体验发现，已修 3 项 / 待修 3 项）

1. **[已修] 服务商下拉不记忆** —— `providerPresetSel` 从不落库，程序化填地址不触发 change，
   刷新后下拉回到「— 选择服务商 —」。修复：`MoraySettings.set('providerPreset')` + 模板
   `selected` 回填 + 预设选择时连带落库 baseURL/model。
2. **[已修] 产物卡"假交互"** —— 只有 8px 小按钮可点，整卡无 cursor/hover/点击。
   修复：卡本体挂 `data-preview-path`，复用 125 的 document 级委托整卡唤起预览。
3. **[已修] 静默保存** —— 改接口地址/模型/Key 零反馈，用户不知道存没存上。
   修复：三处 change 各补 1.5s toast。
4. **[待修] ZCode 引擎芯片与 Agent 档断层** —— 输入台明选 ZCode、顶栏「Agent 关」时，
   代码任务静默降级普通聊天，全程零提示。建议：Agent 关 + 代码意图 → 显式提示
   「ZCode 未启用，已按普通聊天处理」或直接弹一次性引导。
5. **[待修] 冷加载报错撒谎** —— 模型冷加载 >10s 时后端 connect 超时 ×3，前端报
   「本地 Ollama 服务不可用」，但状态栏同时绿着「Ollama 已连接」。
   建议：区分 ConnectError 与 TimeoutException，文案改为「模型加载中，预计还需 Xs」。
6. **[待修] 启动外呼** —— 每次启动/刷新都请求 `open.bigmodel.cn/api/paas/v4/models`
   （无 Key 必 401）。建议：无 Key 时跳过探测。

## 小插件骨架（生态工具雏形）

见 [`plugin-skeleton.js`](./plugin-skeleton.js) —— 一个可独立运行的「产物卡增强器」骨架，
模拟生态插件给 MoRay 产物卡追加「复制路径 / 资源管理器打开」两个动作。
运行：`node plugin-skeleton.js`（零依赖，自带 6 条断言自测）。

## 如何复验本次修复

1. 打开 `http://127.0.0.1:8000` → 设置 → OpenAI 兼容 API → 服务商预设选「智谱 GLM」→ 刷新页面 → 下拉应仍为「智谱 GLM」，文档链接可见。
2. 发一条 ZCode 任务（顶栏切「Agent 自动」）→ 等完成 → **点击产物卡任意位置**（非按钮）→ 应弹出预览。
3. 修改接口地址后失焦 → 应弹「已保存 · 接口地址已更新」toast。
