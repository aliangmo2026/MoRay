# -*- coding: utf-8 -*-
import io

p = 'scripts/mock_llm_server.py'
lines = io.open(p, encoding='utf-8').readlines()
i_def = next(i for i, l in enumerate(lines) if 'def main()' in l)
# 重写 main（保留后续 import os 段）
new_main = '''def main():
    port = int(os_getenv("MOCK_PORT", "8898"))
    # [阶段1.6] 启动时用 MOCK_SCENARIO 指定初始剧本（避免页面跨域调用控制接口）
    initial = os_getenv("MOCK_SCENARIO", "")
    if initial:
        state["scenario"] = initial
        state["idx"] = 0
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print("mock llm listening on", port, "scenario:", state["scenario"])
    srv.serve_forever()
'''
out = lines[:i_def] + [new_main] + lines[i_def + 5:]
io.open(p, 'w', encoding='utf-8', newline='').write(''.join(out))
import py_compile
py_compile.compile(p, doraise=True)
print('main patched & compiled')
