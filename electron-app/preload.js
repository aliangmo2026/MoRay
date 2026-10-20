/* ============================================================
 * MoRay 全生态工作台 - 预加载桥 (preload.js)
 *
 * 渲染进程保持 contextIsolation，无法直接访问 Node/Electron。
 * 通过 contextBridge 只暴露本程序需要的最小一组 API。
 * ============================================================ */

'use strict';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('moray', {
  /**
   * 获取全部服务配置与实时状态
   * @returns {Promise<Array<{id:string,name:string,port:number,url:string,status:string}>>}
   */
  getServices: () => ipcRenderer.invoke('app:get-services'),

  /**
   * 重启全部服务（侧边栏按钮）
   * @returns {Promise<boolean>}
   */
  restartServices: () => ipcRenderer.invoke('app:restart'),

  /**
   * 设置开机自启（如后续界面需要）
   * @param {boolean} enabled
   * @returns {Promise<boolean>} 最终状态
   */
  setAutoStart: (enabled) => ipcRenderer.invoke('app:set-autostart', enabled),

  /* ---------- 自定义标题栏按钮 ---------- */
  minimize: () => ipcRenderer.send('win:minimize'),
  toggleMaximize: () => ipcRenderer.send('win:toggle-maximize'),
  close: () => ipcRenderer.send('win:close'),

  /**
   * 订阅主进程推送的服务状态变化
   * @param {(data:{id:string,status:string}) => void} callback
   */
  onStatusChange: (callback) => {
    const listener = (_event, data) => callback(data);
    ipcRenderer.on('service-status', listener);
  },

  /* ---------- 日志中心 ---------- */
  logs: {
    /** 获取可查看日志的服务列表 */
    list: () => ipcRenderer.invoke('logs:list'),
    /** 增量读取某服务自上次以来的新日志 */
    read: (svcId) => ipcRenderer.invoke('logs:read', svcId)
  },

  /* ---------- [L7.7 加性] Laya 调试面板 ---------- */
  laya: {
    /** 保存文本为本地文件（主进程弹另存为对话框）；返回 {ok, path?, error?} */
    save: (text, defaultName) => ipcRenderer.invoke('laya:save', text, defaultName)
  }
});
