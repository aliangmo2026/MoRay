# MoRay 8000 网关：多云端 Provider 接入指南

> 结论先说：**换云端厂商今天就能做（只改 `server/.env`）；同时挂多个厂商需要小改网关（未实现，见方案 C）。**

## 0. 网关现在到底支持什么

`server/app/llm_proxy.py` 不是"多厂商注册表"，而是**三个固定槽位**，靠 `X-Moray-Provider` 头
（或请求体 `provider`）选择，头 > 体 > 全局 `MORAY_LLM_PROVIDER`：

| 槽位 | 怎么选中 | 上游地址 | 凭据 | 闸门 |
|---|---|---|---|---|
| `ollama` | 默认 / `X-Moray-Provider: ollama` | `MORAY_OLLAMA_BASE_URL` + `/v1` | 无 | 无 |
| `custom_27b` | `X-Moray-Provider: custom_27b` | `MORAY_27B_BASE_URL` | `MORAY_27B_API_KEY` | 无 |
| `cloud` | `X-Moray-Provider: cloud` / `body.provider=cloud` | `MORAY_LLM_BASE_URL` | `MORAY_LLM_API_KEY` | `ENABLE_CLOUD_API=true` + 日/单次预算 |

三个槽位**都是 OpenAI 兼容协议**（一律请求 `<base>/chat/completions`），所以任何一个槽位都能指向
智谱 GLM / Kimi / 通义千问 / DeepSeek —— 只要该厂商提供 OpenAI 兼容端点。

`config.py` 里**没有**按厂商划分的配置项（没有 `GLM_API_KEY` 之类的名字），只有上面这几个槽位变量。

---

## 1. 方案 A：换掉云端槽位（今天可用，一次一家）

以智谱 GLM 为例，编辑 `D:\MoRay\server\.env`：

```dotenv
# --- 云端槽位：OpenAI 兼容地址（注意带 /v1 或厂商对应的兼容前缀）---
MORAY_LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
MORAY_LLM_API_KEY=你的key

# --- 模型名：默认名 + 上游真正接受的名单（不在名单里的名字会被静默改写成 MORAY_LLM_MODEL）---
MORAY_LLM_MODEL=glm-4.6
MORAY_LLM_MODELS=glm-4.6,glm-4-flash

# --- 总闸：不开这个，所有 cloud 调用直接 503 cloud_api_disabled ---
ENABLE_CLOUD_API=true
```

其他厂商的 `MORAY_LLM_BASE_URL` 常见值：

| 厂商 | base URL | 模型名示例 |
|---|---|---|
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` | `glm-4.6` / `glm-4-flash` |
| Kimi（Moonshot） | `https://api.moonshot.cn/v1` | `kimi-k2-0905-preview` |
| 通义千问（DashScope 兼容模式） | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-max` / `qwen-plus` |
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` / `deepseek-reasoner` |

> 模型名随厂商改版会变，以厂商文档为准；不确定就先 `curl $BASE/models -H "Authorization: Bearer $KEY"`。

**界面步骤（让它出现在模型选择器里）**：编辑
`D:\moray-home\profiles\web\cordis.patch.yml`，在 `llm-pi-ai.providers.moray-cloud.models` 里加：

```yaml
          - id: glm-4.6
            name: GLM-4.6 · MoRay 代理
            contextWindow: 131072
            maxTokens: 8192
```

重启壳（或托盘"重启 Harness"）后，选择器里就能看到并选中它——请求仍然走
`http://127.0.0.1:8000/v1`，身份底座 / 记忆 / Twin RAG / 预算闸门都还在链路上。

**自检**：

```powershell
# 看目录是否暴露了云端模型（ENABLE_CLOUD_API=true 才有）
curl http://127.0.0.1:8000/v1/models

# 直接打一次（注意用 UTF-8 文件传 body，命令行里带中文会被 GBK 破坏）
curl -s -X POST http://127.0.0.1:8000/v1/chat/completions ^
  -H "Content-Type: application/json; charset=utf-8" ^
  -H "Authorization: Bearer moray-local" ^
  -H "X-Moray-Provider: cloud" ^
  --data-binary "@body.json"
```

---

## 2. 方案 B：今天就想同时挂两家 —— 借 `custom_27b` 槽位

`custom_27b` 名字虽然叫 27B，本质也是"任意 OpenAI 兼容上游 + 独立 key"，且不受
`ENABLE_CLOUD_API` 约束：

```dotenv
# 云端槽位 = 智谱
MORAY_LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
MORAY_LLM_API_KEY=glm-key
MORAY_LLM_MODEL=glm-4.6
MORAY_LLM_MODELS=glm-4.6
ENABLE_CLOUD_API=true

# 第二个槽位 = Kimi
MORAY_27B_BASE_URL=https://api.moonshot.cn/v1
MORAY_27B_API_KEY=moonshot-key
MORAY_27B_MODEL_NAME=kimi-k2-0905-preview
```

对应 provider（`cordis.patch.yml`）：

```yaml
      kimi-via-moray:
        apiKeyEnv: MORAY_LOCAL_KEY
        displayName: Kimi（经 MoRay 网关·27B 槽）
        api: openai-completions
        baseURL: http://127.0.0.1:8000/v1
        headers:
          X-Moray-Provider: custom_27b
        compat:
          supportsDeveloperRole: false
          maxTokensField: max_tokens
          supportsUsageInStreaming: true
        models:
          - id: kimi-k2-0905-preview
            name: Kimi K2 · MoRay 代理
```

限制（要知道）：这个槽位**只有一个模型名**、没有模型白名单改写、**没有云端预算闸门**
（`DAILY_BUDGET_USD` 管不到它）。

---

## 3. 方案 C：真·多厂商并存（设计稿，尚未实现）

目标：`MORAY_CLOUD_PROVIDERS=glm,kimi,qwen` 一次配齐，界面里同时出现三家模型。

**约定**

```dotenv
MORAY_CLOUD_PROVIDERS=glm,kimi
GLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
GLM_API_KEY=...
GLM_MODELS=glm-4.6,glm-4-flash
KIMI_BASE_URL=https://api.moonshot.cn/v1
KIMI_API_KEY=...
KIMI_MODELS=kimi-k2-0905-preview
```

**路由规则（建议）**
- dsh 侧每个厂商一个 provider，`headers: { X-Moray-Provider: glm }`；
- 或模型名前缀 `glm/glm-4.6`，网关按前缀拆路由（对 OpenAI 客户端更通用）；
- `/v1/models` 输出带前缀的 id，避免不同厂商同名模型互相覆盖。

**改动落点（三处 + 测试）**
1. `server/app/config.py`：解析 `MORAY_CLOUD_PROVIDERS` 与 `*_BASE_URL/_API_KEY/_MODELS`，生成注册表；
2. `server/app/llm_proxy.py`
   - `_HEADER_PROVIDER_ALLOWLIST` 从固定三元组改为"内置三槽 + 注册表里的厂商名"；
   - `_resolve_body_provider` 之后解析出 `(base_url, api_key, models)`；
   - `/v1/models` 聚合注册表，按前缀输出；
3. `cordis.patch.yml`：按厂商各写一个 provider（模板见文件内注释）。
4. 预算/审计按厂商分账（`cloud_budget_guard` 增加 provider 维度）。

> 为什么不建议"每家单独直连"：绕过 8000 就丢掉身份底座、长期记忆、Twin RAG、预算闸门
> 和审计——这正是本项目当初收编云端的理由。

---

## 4. BYOK 透传（脚本 / 临时调试用）

`POST /api/llm/chat` 的请求体可以成对带 `base_url` + `api_key`，网关会临时改投该上游。约束（`_validate_upstream_base`）：

- 只允许 `http/https`；
- 域名解析出的**每个 IP 都必须是公网地址**——环回 / 私有 / 链路本地 / 保留 / 组播一律拒绝。

也就是说 BYOK **不能**指向 `127.0.0.1` 的本地服务；本地请用 `ollama` / `custom_27b` 槽位。

---

## 5. 那个 404 的原生 `deepseek` 选项

`llm-pi-ai` 的 provider 如果没有 `api:` 字段，pi-ai 会**按 provider id 猜协议**——`deepseek`
会走 DeepSeek 原生协议，即使 `baseURL: http://127.0.0.1:8000/v1` 也会打到 8000 不存在的路径，
表现为 404。已从 `cordis.patch.yml` 移除（原位留了说明 + 模板）。

要用 DeepSeek：把 `.env` 的 `MORAY_LLM_*` 指到 DeepSeek，然后选 `moray-cloud` 里的模型。
自己新增 provider 时，`api: openai-completions` 是**必填**。

---

## 6. 排查清单

| 现象 | 先查什么 |
|---|---|
| 503 `cloud_api_disabled` | `ENABLE_CLOUD_API=true` 了吗，改完重启 8000 了吗 |
| 400 / 模型不存在 | 模型名不在 `MORAY_LLM_MODELS`，被改写成了 `MORAY_LLM_MODEL` |
| 401 | `MORAY_LLM_API_KEY` 与 base URL 是否同一家 |
| 答非所问/换了模型 | 同上，白名单改写是静默的（日志有 `模型 %r 不在上游支持列表`） |
| 长任务中途断流 | `streamIdleTimeoutMs`（provider 级）与网关 `_TIMEOUT` |
| 404 | provider 少了 `api: openai-completions`，或 base URL 少了 `/v1` |

**安全**：key 只放 `server/.env`（已 gitignore），不要写进 `cordis.patch.yml`、前端或仓库。
