/* MoRay Service Worker —— 静态资源缓存（离线可用，AI 请求除外）
   策略：HTML/导航请求 -> 网络优先（失败回退缓存）；CDN 与静态资源 -> 缓存优先（后台更新）
   例外：/api/* 一律不拦截（见下方注释） */
const CACHE_NAME = 'moray-3.22.0';
const SHELL_URLS = [
  './moray-workbench.html',
  './index.html',
  './',
  './manifest.webmanifest'
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE_NAME).then(cache =>
    cache.addAll(SHELL_URLS).catch(() => {})
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
