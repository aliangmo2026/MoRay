# v64 快照草稿（2026-10-22）——桥三 stage2 叙事版闭环

> 供并入 `docs/ARCHITECT_MEMORY.md`。该文件对 ZCode 明令只读（L326），故落此草稿，
> 由大肥鱼过目后自行合并（或授权破例）。commit = 本批五件（见文末）。

## 已锁定决策（v64 增量）

310. **桥三 stage2 叙事版落地**：产物定案 `kind='self_narrative'`（与 10-14 写入矩阵
    `"self_narrative": {"self_narrative"}` 对齐）；冷却探测改直查 kind，
    废除旧口径（kind='self' AND tags LIKE——对真实产物永远盲，冷却失效地雷）。
311. **生成链照抄 reflection 范式**：`_llm_narrate` 复用 `REFLECTION_*` config
    （API/BASE_URL/MODEL/TIMEOUT/NUM_CTX）零新增配置面；写入唯一走
    `insert_memory`（矩阵校验+判重+FTS 双写白得）。
312. **prompt 编号裁定**：观察用 `[n]` 串行编号而非 uuid 直写（100×32 字符 uuid
    ≈3.2k 字符会烧 8192 ctx 预算）；n→真实 memory_id 映射进 tags 溯源；
    总字符钳 12000（reflection 实证值）。
313. **⚠ ctx 墙定案**：~1 万字符中文 prompt + qwen3.5:9b（推理模型）思考链耗尽
    8192 ctx → **Ollama 200 OK 但 message.content=""** → llm_failed
    "推理服务应答为空"（config.py 2026-10-04 注释记载同病，4096→8192 那次）。
    **生产 .env 已固化 `MORAY_REFLECTION_NUM_CTX=16384`**（TIMEOUT=600 原有，
    16k 实测生成 ~260s）；load_dotenv 链路探针验证 16384/600 可读。
    备份 `backup\pre_env_reflection_20261022_125420`。
314. **⚠ WARNING 生产静默丢弃定案**：`install_error_handlers` 把 ring+FileHandler
    （均 level=ERROR）挂 "moray" logger → 链上有 handler 即抑制 lastResort →
    logger.warning 既不进 err.log 也不进 moray_errors.log。**排障只认 DB 行 +
    端点 fail-open reason**（中文 reason 经 GBK 控制台必烤糊，须落 UTF-8 文件读）。
315. **重启后起手 tick 必抢跑**：自检循环起手块立即调 self_narrative_maybe，
    POST /generate 在重启后 ~5 分钟内必回 `generation_already_in_progress`（实测×3）；
    等 ~4-5 分钟后 POST 回 `not_ready: cooldown`。7 天冷却端到端验活
    （首触发后 149s 再 POST 正确拦截）。

## 代码现状（10-22 更新，本批 hash）

- `self_narrative.py`：`9DCA02BA`（stage2 全量：生成+双端点+冷却修复+async 门面）
- `self_check.py`：`5F07A3AF`（双钩点 await self_narrative_maybe）
- `parts/146_introspect.js`：`E4A3A472`（v1.3 第九卡+MEM_KIND_ORDER 单列）
- `moray-workbench.html`：`DA17CFC2`（assemble 重建，图标/依赖闸过）
- `docs/stage2_narrative_panel.png`：`87AFBDBD`（自省面板成品截图）
- `server/.env`：+`MORAY_REFLECTION_NUM_CTX=16384`（TIMEOUT=600 原有）
- **首篇叙事**：`memory_bank` id=`b677ec349ca84b09a24a11c094bc7fad`
  （690 字，kind=source='self_narrative'，tags=原料 obs id，created_at 12:44:06）

## 未决 / 待做（对 v58 未决表的更新）

1. ~~桥三 stage2 等素材~~ → **已闭环（v64）**；素材 146 obs/12 insight。
2. ~~桥三产物 kind 路线未定案~~ → **已定案 self_narrative（310）**。
3. **桥三仍零单测**：`build_narrative_messages`/`_strip_code_fence` 纯函数可补测
   （test_self_card 4/4 是域内唯一回归）。
4. ~~四处 warning 被吞~~ → **机制已定案（314）**；是否给 moray logger 补
   WARNING 级通道（或把关键告警升 ERROR）待裁。
5. 安全层其余三方向、上下文预算 B/C、Laya 决策层、monitor 误报、CHANGELOG 3.24.1
   → 原样挂起。
6. **工作区另挂未入库批次**（agent_* 模块删除、LICENSE、crud/db/main、web 副本、
   .env.example 等）待各自成批；根目录有三个 stray 文件
   （名为 `$($l.TargetPath)`、`$null`、`HTTP`——某次 PowerShell 重定向事故产物）
   可清理。
