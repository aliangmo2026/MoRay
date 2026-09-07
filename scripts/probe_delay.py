# -*- coding: utf-8 -*-
"""本地直接验证 delay 逻辑:起 server 线程,发请求,打印异常"""
import json
import sys
import threading
import time
import http.client

sys.path.insert(0, 'scripts')
import mock_llm_server as m

srv = m.ThreadingHTTPServer(('127.0.0.1', 8901), m.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
time.sleep(0.5)


def raw_post(path, body, timeout=8):
    conn = http.client.HTTPConnection('127.0.0.1', 8901, timeout=timeout)
    conn.request('POST', path, body=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp.status, data[:150]


print('ctl:', raw_post('/__ctl', {'scenario': 'multi_slow'}))
t0 = time.time()
try:
    print('chat:', raw_post('/v1/chat/completions', {
        'model': 'mock-llm', 'messages': [{'role': 'user', 'content': 'x'}],
        'stream': False, 'tools': [],
    }), 'elapsed %.1fs' % (time.time() - t0))
except Exception as e:
    print('chat err after %.1fs:' % (time.time() - t0), type(e).__name__, e)
srv.shutdown()
print('server thread alive:', srv._threads if hasattr(srv, '_threads') else 'n/a')
