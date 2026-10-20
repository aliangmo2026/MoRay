/* MoRay Service Worker —— 静态资源缓存（离线可用，AI 请求除外）
   策略：HTML/导航请求 -> 网络优先（失败回退缓存）；CDN 与静态资源 -> 缓存优先（后台更新）
   例外：/api/* 一律不拦截（见下方注释） */
const CACHE_NAME = 'moray-v2.0.1';
// [3.24.1] 预缓存清单 = 页面外壳 + 首屏同步加载的渲染关键 vendor。
// 此前清单只有 4 个 HTML/manifest 项，vendor（含 tailwind-browser-4.js）全靠"首次成功访问时
// 机会性入缓存"；一旦 vendor 缓存未命中 + 后端未就绪，HTML 能渲染但 Tailwind 拿不到 → 全站白条
// （周期性复现的根因）。pdf/mammoth/prettier 是按需懒加载（首次用到才请求），保持机会性缓存，
// 不进预缓存清单。
// 注意：./index.html 不列入 —— 工程根没有该文件、本地后端也没有这条路由，列入必 404。
const SHELL_URLS = [
  './moray-workbench.html',
  './',
  './manifest.webmanifest',
  './vendor/tailwind-browser-4.js',
  './vendor/lucide.min.js',
  './vendor/marked.min.js',
  './vendor/purify.min.js',
  './vendor/highlight.min.js'
];

self.addEventListener('install', (e) => {
  // [3.24.1] 不再用 cache.addAll：它按清单整体失败（原子性），此前清单里的 ./index.html 必然
  // 404，导致整个预缓存（含 4 个 HTML 项）从未真正成功，且 catch(() => {}) 把失败静默吞掉。
  // 改为逐项 cache.add：单项失败只跳过该项并记录，不拖垮其余项。
  e.waitUntil(caches.open(CACHE_NAME).then(cache =>
    Promise.allSettled(SHELL_URLS.map(u => cache.add(u))).then(results => {
      results.forEach((r, i) => {
        if (r.status === 'rejected') {
          console.warn('[MoRay SW] 预缓存单项失败（已跳过）:', SHELL_URLS[i], r.reason);
        }
      });
    })
  ));
  self.skipWaiting();
});

self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then(keys =>
    Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
  ));
  self.clients.claim();
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET') return;
  // AI 请求（Ollama / OpenAI）绝不缓存
  if (url.port === '11434' || /openai|anthropic/i.test(url.hostname)) return;
  // [3.21.0] 本地后端 API 一样绝不拦截：/api/* 是动态数据，走下面的"缓存优先"会把**固定 URL** 的
  // 响应（如 /api/agent/runs?limit=50）当成静态资源返回旧值 —— 页面的 cache:'no-store' 也挡不住
  // （SW 在 HTTP 层之前就应答了）。真机现象：运行中刷新页面，重新对齐运行态读到的是"没有运行记录"
  // 的旧响应，于是把仍在跑的任务误标成失败。API 请求一律交给网络，不进缓存。
  if (url.pathname.startsWith('/api/')) return;
  // HTML / 导航请求：网络优先，离线时回退缓存
  if (e.request.mode === 'navigate' || url.pathname.endsWith('.html')) {
    e.respondWith(
      fetch(e.request).then(res => {
        if (res && res.status === 200) {
          const clone = res.clone();
          caches.open(CACHE_NAME).then(c => c.put(e.request, clone));
        }
        return res;
      }).catch(() => caches.match(e.request).then(c =>
        c || caches.match('./index.html') || caches.match('./moray-workbench.html') || caches.match('./')
      ))
    );
    return;
  }
  // 其他资源：缓存优先，后台更新
  e.respondWith(
    caches.match(e.request).then(cached => {
      const fetched = fetch(e.request).then(res => {
        if (res && (res.status === 200 || res.type === 'opaque')) {
          const clone = res.clone();
          caches.open(CACHE_NAME).then(c => c.put(e.request, clone));
        }
        return res;
      }).catch(() => cached);
      return cached || fetched;
    })
  );
});
