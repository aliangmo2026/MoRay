#!/usr/bin/env node
/* ============================================================
 * moray-eco-test / plugin-skeleton.js
 * MoRay 生态插件骨架：产物卡增强器（Artifact Card Enhancer）
 *
 * 背景（来自 2026-10-27 深度 dogfooding）：
 *   ZCode/工具产物卡只显示文件 basename，用户拿不到真实路径，
 *   也没有"在资源管理器中打开"这类生态动作。本骨架演示第三方
 *   插件如何以最小成本给产物卡追加动作，验证：
 *     ① 本地工作区文件读写能力；
 *   ② 产物卡是否暴露了可编程的挂载点（data-preview-path 委托）。
 *
 * 运行：node plugin-skeleton.js   （零依赖，6 条断言自测）
 * ============================================================ */

'use strict';

/** 产物卡路径编码：zcode:<task_id>:<encodeURIComponent(filename)> */
const ZCODE_PATH_RE = /^zcode:([0-9a-f]{32}):(.+)$/i;

/**
 * 解析产物卡上的路径编码。
 * @param {string} raw - data-preview-path 原值
 * @returns {{kind:'zcode'|'file', taskId?:string, name:string, full:string}|null}
 */
function parseArtifactPath(raw) {
  const s = String(raw || '').trim();
  if (!s) return null;
  const m = ZCODE_PATH_RE.exec(s);
  if (m) {
    let name = m[2];
    try { name = decodeURIComponent(name); } catch (e) { /* 保留原编码串 */ }
    return { kind: 'zcode', taskId: m[1], name, full: s };
  }
  return { kind: 'file', name: s.split(/[\\/]/).pop(), full: s };
}

/**
 * 给一张产物卡追加生态动作（纯数据变换 → 返回按钮描述数组）。
 * 真实插件里会把返回值渲染进卡片；此处保持零 DOM 依赖以便单测。
 * @param {string} pathAttr - 卡上的 data-preview-path
 * @returns {Array<{id:string, label:string, hint:string}>}
 */
function actionsFor(pathAttr) {
  const p = parseArtifactPath(pathAttr);
  if (!p) return [];
  const actions = [
    { id: 'copy-path', label: '复制路径', hint: p.kind === 'zcode' ? '复制 zcode: 编码路径' : '复制文件系统路径' },
    { id: 'reveal', label: '打开所在目录', hint: p.kind === 'zcode' ? '沙箱产物：导出后可打开' : '在资源管理器中显示' }
  ];
  if (/\.(py|js|ts|md|json|txt|csv)$/i.test(p.name)) {
    actions.push({ id: 'copy-content', label: '复制内容', hint: '经 /api/engines/zcode/files/<tid>/<name> 读取' });
  }
  return actions;
}

/** 自测：覆盖编码解析 / 中文文件名 / 非法输入 / 动作过滤 */
function selfTest() {
  const assert = require('assert');
  const tid = 'ffb54b03fe784e25b84d75a472a83417';

  const a = parseArtifactPath(`zcode:${tid}:${encodeURIComponent('binary_search.py')}`);
  assert.strictEqual(a.kind, 'zcode');
  assert.strictEqual(a.taskId, tid);
  assert.strictEqual(a.name, 'binary_search.py');

  const b = parseArtifactPath(`zcode:${tid}:${encodeURIComponent('输出 报告.md')}`);
  assert.strictEqual(b.name, '输出 报告.md', '中文+空格文件名须 decode 回来');

  assert.strictEqual(parseArtifactPath(''), null);
  assert.strictEqual(parseArtifactPath('zcode:not-a-uuid:x').kind, 'file', '非法编码回落 file');

  const acts = actionsFor(`zcode:${tid}:${encodeURIComponent('binary_search.py')}`);
  assert.ok(acts.some(x => x.id === 'copy-path'), 'py 文件须有复制路径');
  assert.ok(acts.some(x => x.id === 'copy-content'), 'py 文件须有复制内容');

  const noExt = actionsFor(`zcode:${tid}:${encodeURIComponent('Makefile')}`);
  assert.ok(!noExt.some(x => x.id === 'copy-content'), '无扩展名不给复制内容');

  console.log('selftest: 6/6 passed');
}

if (require.main === module) selfTest();

module.exports = { parseArtifactPath, actionsFor };
