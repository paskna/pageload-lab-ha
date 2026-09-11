import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import pytest

from app.models import Proxy
from app.proxy_manager import ProxyManager
from app.security import SecretBox


@pytest.fixture
def manager(database, tmp_path):
    return ProxyManager(database.session_factory, SecretBox(tmp_path / "proxy.key"))


def add(manager, name, port):
    return manager.create({"name": name, "protocol": "SOCKS5", "host": "proxy.example", "port": port, "username": "u", "password": "p", "enabled": True})


async def test_round_robin_selection(manager):
    first, second = add(manager, "A", 1080), add(manager, "B", 1081)
    assert [(await manager.select("round_robin")).id, (await manager.select("round_robin")).id, (await manager.select("round_robin")).id] == [first["id"], second["id"], first["id"]]


async def test_random_and_fixed_selection(manager):
    first, second = add(manager, "A", 1080), add(manager, "B", 1081)
    with patch("app.proxy_manager.random.choice", side_effect=lambda rows: rows[-1]):
        assert (await manager.select("random")).id == second["id"]
    assert (await manager.select("fixed", first["id"])).id == first["id"]
    assert await manager.select("direct") is None


def test_password_is_encrypted_and_never_public(database, manager):
    row = add(manager, "A", 1080)
    assert "password" not in row
    with database.session() as session:
        assert session.get(Proxy, row["id"]).encrypted_password != "p"
        assert "password" not in manager.list_public()[0]


def test_proxy_validation(manager):
    with pytest.raises(ValueError): manager.create({"name": "", "protocol": "HTTP", "host": "x", "port": 80})
    with pytest.raises(ValueError): manager.create({"name": "x", "protocol": "FTP", "host": "x", "port": 80})


async def test_controlled_http_proxy_connection(database, tmp_path):
    class ControlledProxy(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"ip": "192.0.2.10", "requested": self.path}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), ControlledProxy)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        local = ProxyManager(database.session_factory, SecretBox(tmp_path / "controlled-proxy.key"))
        row = local.create({"name": "Controlled", "protocol": "HTTP", "host": "127.0.0.1", "port": server.server_port, "enabled": True})
        result = await local.test(row["id"], "http://example.invalid/ip")
        assert result["success"] is True
        assert result["status_code"] == 200
        assert result["visible_source_ip"] == "192.0.2.10"
        assert result["latency_ms"] >= 0
        assert "HTTP-Header" in result["explanation"]
    finally:
        server.shutdown()
        server.server_close()
