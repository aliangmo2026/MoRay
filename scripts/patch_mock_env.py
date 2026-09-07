# -*- coding: utf-8 -*-
import io

p = 'scripts/mock_llm_server.py'
src = io.open(p, encoding='utf-8').read()
old = '''def main():
    port = int(os_getenv("MOCK_PORT", "8898"))
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print("mock llm listening on", port, "scenario:", state["scenario"])
    srv.serve_forever()'''
new = '''def main():
    port = int(os_getenv("MOCK_PORT", "8898"))
    # [阶段1.6] 启动时可用 MOCK_SCENARIO 指定初始剧本（避免页面跨域调用控制接口）
    initial = os_getenv("MOCK_SCENARIO", "")
    if initial:
        state["scenario"] = initial
        state["idx"] = 0
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print("mock llm listening on", port, "scenario:", state["scenario"])
    srv.serve_forever()'''
assert old in src, 'main pattern not found'
src = src.replace(old, new)
# state 定义提前引用（state 在 main 之前已定义 ✓）
io.open(p, 'w', encoding='utf-8', newline='').write(src)
print('MOCK_SCENARIO support added')
