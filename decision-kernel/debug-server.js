'use strict';
/**
 * debug-server.js —— MoRay 决策内核 v1.0 可视化调试服务（纯新增，零侵入核心）
 *
 * 定位：本地可视化调试面板的后端，完全复用现有核心函数：
 *   - encodeWorldState（文件2：Layer 0 世界状态编码器）
 *   - makeDecision（文件3：Layer 1 Laya 快速决策层）
 * 本文件不包含任何决策逻辑，仅做「接口装配 + 全链路调用 + 静态托管」。
 *
 * 接口：
 *   POST /api/decide   请求体 JSON { query, permission, options?, agent_pool? }
 *                      → 全链路 L0 编码 → L1 决策 → 返回标准 DecisionResult JSON
 *   GET  /api/decide?query=...&permission=...   （同上，便于浏览器直接调试）
 *   GET  /            → debug-panel.html
 *   GET  *.html       → 静态托管同目录下的 html 页面
 *
 * 启动：node debug-server.js   （端口 18080）
 */

const http = require('http');
const fs = require('fs');
const path = require('path');

// —— 复用核心模块（只调用，不修改、不复制实现）——
const { encodeWorldState } = require('./2-Layer0-状态编码器.js');
const { makeDecision } = require('./3-Layer1-Laya决策层.js');

const PORT = 18080;
const HOST = '127.0.0.1';
const ROOT = __dirname;

// 模拟 Agent 池（与 POC / 批量测试脚本保持一致的基准最小样例）
const DEFAULT_AGENT_POOL = [
  { agent_id: 'code_agent', status: 'healthy' },
  { agent_id: 'confirm_agent', status: 'healthy' },
  { agent_id: 'audit_agent', status: 'healthy' },
];

/** 统一 JSON 响应（含跨域头，方便本地任意页面调试） */
function sendJson(res, statusCode, obj) {
  res.writeHead(statusCode, {
    'Content-Type': 'application/json; charset=utf-8',
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
  });
  res.end(JSON.stringify(obj));
}

/**
 * 全链路决策：query + permission → L0 编码 → L1 决策 → DecisionResult
 * @returns {{ decision: object, worldState: object }}
 */
function runFullChain(query, permission, options, agentPool) {
  const pool = Array.isArray(agentPool) && agentPool.length > 0 ? agentPool : DEFAULT_AGENT_POOL;
  const worldState = encodeWorldState(query, pool, permission); // 复用核心函数①
  const decision = makeDecision(query, worldState, options);    // 复用核心函数②
  return { decision, worldState };
}

/** 处理 /api/decide：解析参数 → 全链路 → 返回标准 DecisionResult */
function handleDecide(res, payload) {
  const query = String(payload.query || '').trim();
  if (!query) {
    sendJson(res, 400, { error: '缺少必填参数 query（用户请求文本）' });
    return;
  }
  const permission = String(payload.permission || 'read');
  const options = payload.options && typeof payload.options === 'object' ? payload.options : {};
  try {
    const { decision, worldState } = runFullChain(query, permission, options, payload.agent_pool);
    // 附带世界状态，便于面板展示 L0 编码结果（标准契约字段不受影响）
    decision.debug_world_state = worldState;
    console.log(
      `[决策] query="${query}" 权限=${permission} → 风险=${decision.quality.risk_level}` +
      ` 升级=${decision.upgrade.should_upgrade}(${decision.upgrade.upgrade_priority})` +
      ` 步骤=${decision.output.step_sequence.length} 置信度=${decision.quality.confidence}` +
      ` 耗时=L0 ${worldState.encode_time_ms}ms + L1 ${decision.latency_ms}ms`
    );
    sendJson(res, 200, decision);
  } catch (err) {
    console.error(`[决策异常] ${err && err.message ? err.message : String(err)}`);
    sendJson(res, 500, { error: '决策链路执行异常', detail: String(err && err.message ? err.message : err) });
  }
}

/** 静态托管：仅允许同目录下的 .html 页面（防目录穿越） */
function serveHtml(req, res, urlPath) {
  const rel = urlPath === '/' ? '/debug-panel.html' : urlPath;
  const name = decodeURIComponent(rel).replace(/^\/+/, '');
  if (!/^[\w\-.\u4e00-\u9fa5]+\.html$/i.test(name)) {
    res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
    res.end('404 未找到：仅托管同目录下的 .html 页面');
    return;
  }
  const file = path.join(ROOT, name);
  if (!file.startsWith(ROOT) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
    res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
    res.end('404 未找到：' + name);
    return;
  }
  res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
  fs.createReadStream(file).pipe(res);
}

const server = http.createServer((req, res) => {
  const urlPath = (req.url || '/').split('?')[0];

  // —— 决策接口 ——
  if (urlPath === '/api/decide') {
    if (req.method === 'OPTIONS') return sendJson(res, 204, {});
    if (req.method === 'GET') {
      const qs = new URLSearchParams((req.url || '').split('?')[1] || '');
      const payload = {
        query: qs.get('query') || '',
        permission: qs.get('permission') || 'read',
        options: qs.get('options') ? JSON.parse(qs.get('options')) : {},
      };
      return handleDecide(res, payload);
    }
    if (req.method === 'POST') {
      let body = '';
      req.on('data', (c) => {
        body += c;
        if (body.length > 1e6) req.destroy(); // 防御：超大请求直接断开
      });
      req.on('end', () => {
        let payload = {};
        try {
          payload = body ? JSON.parse(body) : {};
        } catch (err) {
          return sendJson(res, 400, { error: '请求体不是合法 JSON', detail: String(err && err.message) });
        }
        handleDecide(res, payload);
      });
      return;
    }
    return sendJson(res, 405, { error: '方法不允许，请使用 GET 或 POST' });
  }

  // —— 静态页面 ——
  if (req.method === 'GET') return serveHtml(req, res, urlPath);
  res.writeHead(405, { 'Content-Type': 'text/plain; charset=utf-8' });
  res.end('405 方法不允许');
});

server.on('error', (err) => {
  if (err.code === 'EADDRINUSE') {
    console.error(`\n[启动失败] 端口 ${PORT} 已被占用——调试面板可能已在运行。`);
    console.error(`  若未运行，请关闭占用 ${PORT} 端口的程序后重试。`);
    process.exitCode = 1;
  } else {
    console.error('\n[启动失败]', err && err.message ? err.message : String(err));
    process.exitCode = 1;
  }
});

server.listen(PORT, HOST, () => {
  const line = '='.repeat(60);
  console.log(line);
  console.log('  MoRay 决策内核 v1.0 —— 可视化调试服务');
  console.log(line);
  console.log(`  面板地址 : http://${HOST}:${PORT}/debug-panel.html`);
  console.log(`  首页地址 : http://${HOST}:${PORT}/`);
  console.log(`  决策接口 : POST http://${HOST}:${PORT}/api/decide`);
  console.log(`             请求体 {"query":"...","permission":"read|write|admin","options":{...}}`);
  console.log('  核心复用 : encodeWorldState（L0） + makeDecision（L1），零侵入');
  console.log('  运行日志 : 下方实时输出每次决策请求的核心结果');
  console.log(line);
});
