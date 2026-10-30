# MoRay × ZCode Docker 隔离方案（engines/docker/）

Day 11a 产出的**配置层**：把 `docs/zcode_adapter_design.md`（Day 8）b 节隔离设计和
`docs/zcode_docker_isolation.md`（Day 11a，a–g 全章节）落成可构建、可验收的文件。
**本目录不含任何 .py**；适配器接线（zcode_adapter.py 从 subprocess 直调切到 docker 链路）
属后续天任务，以本目录验收全绿为前提。

## 文件清单

| 文件 | 作用 |
|---|---|
| `Dockerfile` | 多阶段构建：node:24.14.0-slim + ZCode 源码快照 → `moray/zcode-agent:0.16.9`。镜像内零密钥、非 root、CLI 入口 `zcode` 进 PATH |
| `docker-compose.yml` | `zcode-runner` 一次性任务容器模板：沙箱挂载、internal 网络（默认拒绝出站）、CPU/内存/pids/tmpfs 限制、`ZCODE_PERSONAL_PROVIDER_CONFIG_FILE` 等 env 注入 |
| `verify_isolation.sh` / `verify_isolation.bat` | 启动前实测验收（硬闸门，语义等价的 Bash / Windows 原生两版，**改任何一项检查须两边同步**）。任何一项不过 → exit 1 → 禁止受理任务 |
| `.dockerignore` | 构建上下文裁剪：排除 node_modules/.git、**排除 provider/（含网关凭据，绝不进镜像层）** |
| `zcode-src/`（Day 11b 放置，今晚不存在） | ZCode 源码快照（干净树，排除 node_modules），Dockerfile 的构建输入 |
| `provider/zcode-provider.json`（Day 11b 生成，今晚不存在） | 运行期只读挂载的认证配置（baseUrl+apiKey+model）。**只允许放 MoRay 网关本地凭据**，上游厂商 Key 绝不出宿主 `.env` |

## 前置条件（宿主）

1. Docker Desktop（WSL2 后端）已安装并启动 —— 今晚红线不装，Day 11b 前由用户就绪；
2. MoRay 后端在宿主 `:8000` 运行（`GET /api/health` 可通）；
3. `D:\MoRay\workspace\zcode_sandbox\` 存在（验收脚本会代建；正式接线后由适配器管理 tasks/<id>/ 结构）；
4. Windows 防火墙放行 Docker 子网 → 宿主 8000 入站（B 项验收最常见失败原因）。

## 构建镜像（Day 11b 第一步）

```bat
REM 1) 放置源码快照：把 Day 9 实测过的源码树复制进来（复制，不动 D:\ZCode_Test 原件），
REM    排除 node_modules 与一切 .git；确认 apps\zcode-cli\package.json 版本 = 0.16.9
xcopy /E /I D:\ZCode_Test\zcode-repo D:\MoRay\engines\docker\zcode-src
rmdir /s /q D:\MoRay\engines\docker\zcode-src\node_modules

REM 2) 构建（在 engines\docker 目录下）
cd /d D:\MoRay\engines\docker
docker compose build
REM 若之后取得官方仓库 commit，重建并补钉：
REM   docker compose build --build-arg ZCODE_COMMIT=<hash>

REM 3) 冒烟：CLI 入口与构建身份
docker compose run --rm zcode-runner zcode --help
docker compose run --rm zcode-runner cat /opt/zcode/BUILD_INFO
```

构建失败的两个已知嫌疑（Dockerfile 内有断言/注释）：`pnpm build:zcode` 不产出
`dist/zcode.cjs`（备选 `pnpm --dir apps/zcode-cli cli:build`）；依赖需额外原生编译
（builder 已带 build-essential + python3）。

## 生成 provider 配置（Day 11b 第二步）

结构以 Day 9 实测报告（`D:\ZCode_Test\ZCode_CLI_Flag实测报告.md`）确认的
`ZCODE_PERSONAL_PROVIDER_CONFIG_FILE` 格式为准，要点只有三条：

- `baseUrl` = `http://host.docker.internal:8000`（指向 MoRay 网关，**不许**指上游厂商）；
- `apiKey` = 网关本地凭据（宿主生成；容器内这份被外带的泄露面 = 网关，受 Day 1 熔断 + MCP 白名单约束）；
- `model` = 钉死一个模型（CLI 不能按次切换，换模型 = 改配置重跑）。

放到 `engines\docker\provider\zcode-provider.json`（compose 默认路径），或用环境变量
`MORAY_ZCODE_PROVIDER_CONFIG_HOST` 指到别处。**开放问题（Day 11b 核对）**：网关现有
`/api/llm/*` 是否满足 ZCode 期望的 OpenAI 兼容端点；不兼容则需网关补路由（.py 改动，走后续天红线清单）。

## 隔离验收（硬闸门，构建后必跑）

```bat
cd /d D:\MoRay\engines\docker
verify_isolation.bat        REM Git-Bash/WSL 用户可跑 verify_isolation.sh，语义等价
```

三项检查全绿（exit 0）才允许受理任务；任何一项失败（exit 1）→ 按设计文档 g 节降级，
**绝不裸跑**。验收容器全部 `--rm` 一次性，不留残余。重跑时机：镜像重建、Docker Desktop
重启/升级、宿主网络或防火墙变更、compose/网络配置改动。

## Day 11b 实测步骤（留给明天，按序执行）

| # | 步骤 | 验收标准 |
|---|---|---|
| 1 | 前置条件 1–4 就绪；放置 `zcode-src/` 快照 | 快照内 `apps/zcode-cli/package.json` version=0.16.9；无 node_modules |
| 2 | `docker compose build` | 构建成功；`BUILD_INFO` 有 bundle_sha256；镜像 `moray/zcode-agent:0.16.9` 存在 |
| 3 | 冒烟 `zcode --help`（容器内） | strict 解析器输出主帮助，exit 0；顺手核对 Day 9 flag 词表无漂移 |
| 4 | 生成 provider 配置 + 核对网关兼容端点（开放问题） | 配置只含网关凭据；baseUrl 指 host.docker.internal:8000 |
| 5 | **跑 `verify_isolation.bat`** | A（公网必失败）/B（网关必成功）/C1–C6 全 PASS，exit 0；截图留档 |
| 6 | 挂载审计：起一个长命令容器，`docker inspect` 看 Mounts | 恰好两条 bind（/workspace rw、provider ro）+ tmpfs /tmp，rootfs ReadonlyRootfs=true，多一条即不通过 |
| 7 | 真任务冒烟：`docker compose run --rm -e ZCODE_TASK_ID=smoke01 zcode-runner zcode -p - --mode edit --cwd /workspace --disallowed-tools Bash,WebFetch --json`，stdin 喂一句"写 hello.md 到当前目录" | **重点验证 `-p -` stdin 形态**（Day 9 遗留未实测项；若 CLI 把 `-` 当字面提示词，按设计文档 c.2 退化为 inbox/prompt.md 路径参数）；exit 0；`--json` 单块输出含 sessionId/usage；沙箱里出现 hello.md |
| 8 | 资源限制抽查：容器内 `nproc`、`cat /sys/fs/cgroup/memory.max` | ≈2 核 / 2g，与 compose 声明一致 |
| 9 | 验收记录落 `docs/`（通过项、失败项、平台差异实况） | 全绿后才开"适配器 .py 接线"任务（is_isolated=True 升格、run_zcode→zcode_run 词表统一） |

## 故障排查速查

| 症状 | 最可能原因 | 处置 |
|---|---|---|
| B 项失败（网关不可达） | 后端没起 / 防火墙拦 Docker 子网 / internal+host-gateway 平台不兼容 | 按脚本内打印的 ①②③ 顺序排查；③ 命中则走设计文档 d 节退级预案，退级后仍以实测为准 |
| A 项竟然通过（公网可达） | 网络不是 internal（被人 `docker network rm` 后用普通 bridge 重建过） | 删网络重建 `--internal`；重跑验收；**期间禁止受理任务** |
| 构建在 pnpm install 卡死/失败 | 构建期网络问题或 lockfile 与 pnpm 版本不匹配 | 确认 corepack 激活的是 pnpm@10.33.2（packageManager 字段）；构建期允许联网是设计内行为 |
| `docker compose run` 报 provider 文件不存在 | Day 11b 第 4 步没做 | 生成配置或设 `MORAY_ZCODE_PROVIDER_CONFIG_HOST` |
| CLI 报未知 flag、exit 1 | 0.16.9 strict 解析器拒绝未注册 flag | 对照 Day 9 词表；`--output-format` 是隐藏 flag，升级 CLI 前必须重测 |

## 红线备忘（本目录的边界）

- 这里只有配置与脚本，**没有 .py**；MoRay 后端一行未改；
- 镜像与配置**不含任何上游厂商 API Key**；
- `D:\ZCode_Test` 只读引用（快照是复制），不改动；
- 验收未全绿之前，ZCodeAdapter 维持 Day 10 骨架的诚实申报（is_isolated=False，仅内部联调）。
