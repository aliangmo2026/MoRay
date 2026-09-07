# -*- coding: utf-8 -*-
import time
import threading
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class H(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers.get('Content-Length', 0)))
        time.sleep(1.0)
        body = b'ok'
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


srv = ThreadingHTTPServer(('127.0.0.1', 8902), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
time.sleep(0.4)
t0 = time.time()
conn = http.client.HTTPConnection('127.0.0.1', 8902, timeout=5)
conn.request('POST', '/x', body=b'{}', headers={'Content-Type': 'application/json'})
r = conn.getresponse()
print('minimal sleep test:', r.status, r.read(), 'elapsed %.1fs' % (time.time() - t0))
srv.shutdown()
