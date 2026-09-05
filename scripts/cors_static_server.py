#!/usr/bin/env python3
"""带 CORS 头的静态文件服务器（本地包端到端验收用）。"""
import http.server
import sys

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
DIR = sys.argv[2] if len(sys.argv) > 2 else "."


class CORSHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=DIR, **kw)

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


http.server.ThreadingHTTPServer(("127.0.0.1", PORT), CORSHandler).serve_forever()
