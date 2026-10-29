/* ============================================================
 * MoRay 桌面壳 - Harness 模式 preload [2026-10-30 沉浸式无边框]
 *
 * Harness（dsh web）是普通网页：页面里没有原生窗口按钮，无边框后
 * 最小化/最大化/关闭由这里桥接。只暴露三个无参动作，不暴露 ipcRenderer
 * 本体；win:* 通道与旧版工作台壳共用 main.js 里的同一组 ipcMain 监听。
 * sandbox: true 下 preload 只能 require('electron') 的 contextBridge/ipcRenderer
 * 子集——正好够用。
 * ============================================================ */
'use strict';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('morayWindow', {
  minimize: () => ipcRenderer.send('win:minimize'),
  toggleMaximize: () => ipcRenderer.send('win:toggle-maximize'),
  close: () => ipcRenderer.send('win:close')
});
