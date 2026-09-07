# -*- coding: utf-8 -*-
"""延迟分支隔离实验:1ms vs 2500ms"""
import io
import sys
import threading
import time
import http.client

src = io.open('scripts/mock_llm_server.py', encoding='utf-8').read()
src1 = src.replace('2500', '1')  # delay 1ms 版
import types


def make_server(port, source):
    mod = types.ModuleType('mockx%d' % port)
    exec(compile(source, 'mockx', 'exec'), mod.__dict__)
    srv = mod.ThreadingHTTPServer(('127.0.0.1', port), mod.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def post(port, path, body, timeout=5):
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=timeout)
    conn.request('POST', path, body=body.encode(), headers={'Content-Type': 'application/json'})
    r = conn.getresponse()
    d = r.read()
    conn.close()
    return r.status, d[:100]


time.sleep(0.3)
# A: delay=1ms
s1 = make_server(8903, src1)
print('A ctl', post(8903, '/__ctl', '{"scenario": "multi_slow"}'))
t0 = time.time()
print('A chat1', post(8903, '/v1/chat/completions', '{"model":"m","messages":[{"role":"user","content":"x"}],"stream":false,"tools":[]}'), '%.1fs' % (time.time() - t0))
t0 = time.time()
print('A chat2', post(8903, '/v1/chat/completions', '{"model":"m","messages":[{"role":"user","content":"x"}],"stream":false,"tools":[]}'), '%.1fs' % (time.time() - t0))
s1.shutdown()
# B: 原生 delay=2500ms
s2 = make_server(8904, src)
print('B ctl', post(8904, '/__ctl', '{"scenario": "multi_slow"}'))
t0 = time.time()
print('B chat1', post(8904, '/v1/chat/completions', '{"model":"m","messages":[{"role":"user","content":"x"}],"stream":false,"tools":[]}', timeout=8), '%.1fs' % (time.time() - t0))
s2.shutdown()
print('done')
