// MoRay 通用 AI 接口透传代理（BYOK：不存储/不记录任何 Key）
// 前端把"接口地址(baseURL)"填成：https://你的worker.workers.dev/api.deepseek.com/v1
const CORS = (req) => ({
  'Access-Control-Allow-Origin': req.headers.get('origin') || '*',
  'Access-Control-Allow-Methods': 'GET,POST,PUT,DELETE,OPTIONS',
  'Access-Control-Allow-Headers': 'Authorization,Content-Type',
  'Access-Control-Max-Age': '86400'
});
export default {
  async fetch(request) {
    if (request.method === 'OPTIONS') return new Response(null, { headers: CORS(request) });
    const u = new URL(request.url);
    const m = u.pathname.match(/^\/([^/]+)\/(.*)$/); // 形如 /api.deepseek.com/v1/chat/completions
    if (!m) return new Response('MoRay proxy: /<api-host>/<path>', { status: 400, headers: CORS(request) });
    const target = 'https://' + m[1] + '/' + m[2] + u.search;
    const h = new Headers();
    const auth = request.headers.get('authorization');
    if (auth) h.set('authorization', auth);
    h.set('content-type', request.headers.get('content-type') || 'application/json');
    const init = { method: request.method, headers: h };
    if (request.method !== 'GET' && request.method !== 'HEAD') init.body = request.body;
    const up = await fetch(target, init);
    const resp = new Response(up.body, { status: up.status, statusText: up.statusText, headers: up.headers });
    Object.entries(CORS(request)).forEach(([k, v]) => resp.headers.set(k, v)); // SSE 流式原样透传
    return resp;
  }
};
