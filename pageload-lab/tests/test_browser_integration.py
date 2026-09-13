import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.browser import BrowserEngine
from app.proxy_manager import ProxyConfig


class MemorySettings:
    def __init__(self): self.values={'allow_private_targets':True,'max_parallel_browsers':2,'max_memory_percent':98,'navigation_timeout_seconds':1}
    def get(self,key,default=None): return self.values.get(key,default)


def test_zombie_probe_tolerates_restricted_proc(monkeypatch):
    class RestrictedProcess:
        def children(self, recursive=False):
            raise PermissionError("/proc is restricted by AppArmor")

    monkeypatch.setattr("app.browser.psutil.Process", RestrictedProcess)
    assert BrowserEngine._zombie_processes() == []


@pytest.fixture(scope='module')
def test_server():
    executed=threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path=='/executed': executed.set();self.send_response(204);self.end_headers();return
            if self.path=='/404': code=404
            elif self.path=='/500': code=500
            elif self.path=='/timeout':
                import time;time.sleep(2);code=200
            else: code=200
            body=b"<html><body>PageLoad Lab<script>fetch('/executed')</script></body></html>"
            self.send_response(code);self.send_header('Content-Type','text/html');self.send_header('Content-Length',str(len(body)));self.end_headers()
            try: self.wfile.write(body)
            except BrokenPipeError: pass
        def log_message(self,*args): pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
    yield f'http://127.0.0.1:{server.server_port}',executed
    server.shutdown();server.server_close()


@pytest.mark.browser
async def test_real_chromium_javascript_timing_and_stay(test_server):
    base,executed=test_server;engine=BrowserEngine(MemorySettings());await engine.start()
    try:
        result=await engine.visit(base+'/', 'browser', .2, None)
        assert result.success and result.http_status==200
        assert result.load_time_ms is not None and result.ttfb_ms is not None
        assert result.stay_time_seconds>=.19 and executed.wait(1)
        assert engine.active_sessions==0
    finally: await engine.stop()


@pytest.mark.browser
async def test_http_errors_timeout_dns_and_browser_recovery(test_server):
    base,_=test_server;engine=BrowserEngine(MemorySettings());await engine.start()
    try:
        assert (await engine.visit(base+'/404','browser',0,None)).error_type=='http_status'
        assert (await engine.visit(base+'/500','http',0,None)).http_status==500
        assert (await engine.visit(base+'/timeout','http',0,None)).error_type=='timeout'
        unreachable=ProxyConfig(1,'Kontrollierter Testproxy','HTTP','127.0.0.1',1,None,None)
        assert (await engine.visit(base+'/','http',0,unreachable)).error_type=='proxy'
        with pytest.raises(Exception):
            await engine.visit('http://nonexistent.invalid/','http',0,None)
        await engine._browser.close()
        assert (await engine.visit(base+'/','browser',0,None)).success
    finally: await engine.stop()
