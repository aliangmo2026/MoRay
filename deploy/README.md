# MoRay 云端部署（BYOK 在线版）

MoRay 是纯静态单文件应用：任何人打开网址即可使用，**API Key 由用户自己填写、仅保存在用户自己的浏览器本地**，部署者不承担任何 token 费用。

## 一、部署静态网站（二选一）

先用 `python assemble.py --web` 生成 `web/` 目录（含 `index.html`、`vendor/`、`sw.js`、`manifest.webmanifest`），然后：

- **Cloudflare Pages**：登录 dash.cloudflare.com → Workers & Pages → Create → Pages → Upload assets → 把 `web/` 目录拖进去 → Deploy，得到 `https://xxx.pages.dev`。
- **Vercel / Netlify / GitHub Pages**：把 `web/` 目录内容推到仓库或直接拖拽上传即可，无需任何构建配置。

## 二、部署跨域代理（推荐，一次五步）

浏览器直连大部分 AI 服务商会遇到 CORS 跨域限制。部署一个 Cloudflare Worker 透传代理即可解决（免费额度足够个人使用）：

1. 登录 Cloudflare → **Workers & Pages** → **Create application** → **Create Worker**；
2. 给 Worker 起个名字（如 `moray-proxy`），点击 **Deploy**；
3. 点击 **Edit code**，把本目录 `worker.js` 的全部内容粘贴进去，替换默认代码；
4. 点击 **Deploy**，得到代理地址：`https://你的worker名.你的子域.workers.dev`；
5. 完成。该代理只做请求转发与流式透传，**不存储、不记录任何 Key**。

> 也可以不部署代理：部分服务商（如 OpenRouter、硅基流动等）本身允许浏览器跨域直连，直接填官方地址测试即可；报跨域时再按上述步骤部署代理。

## 三、前端"接口地址"填写规则

在 MoRay 设置页（或首次引导卡）中：

- **直连**：填服务商官方地址，如 `https://api.deepseek.com/v1`
- **经 Worker 代理**：把官方地址的 `https://` 换成 `https://你的worker.workers.dev/`，即

```
https://你的worker.workers.dev/api.deepseek.com/v1
```

其它厂商同理，只需替换代理地址后面的域名部分（如 `/api.openai.com/v1`、`/api.moonshot.cn/v1`、`/open.bigmodel.cn/api/paas/v4`）。

## 四、隐私与费用

- 你的 API Key 仅保存在你自己的浏览器本地（IndexedDB，混淆存储），不会上传到 MoRay；
- 直连时请求仅发往你选择的服务商；若填写了第三方代理地址，请求会经该代理转发，请使用官方或自建代理；
- 所有 token 费用按各服务商定价计入**用户自己的账户**，与部署者无关。

## 五、本地版与在线版

- `moray-workbench.html` 双击即可本地使用（file:// 协议，功能完全相同）；
- 在线版与本地版共用同一份代码构建产物，区别仅在于入口文件名（`index.html`）与 PWA `start_url`。
