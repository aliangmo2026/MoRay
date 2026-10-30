# -*- coding: utf-8 -*-
"""MoRay 外部 MCP 服务适配层（p_mcp 迭代新增目录，全部独立文件、零核心嵌入）。

模块清单：
  iris_mcp_client.py   Iris MCP Server（9301）的 SSE 客户端（消费 config.MCP_SERVERS['iris']）
  local_log_adapter.py 本地日志三源采集器（阶段4）
  routes.py            /api/adapters/* 薄路由（由 server/app/api.py 尾部 include 挂载）
"""
