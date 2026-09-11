"""Proxy persistence, selection, safe credential handling and connectivity tests."""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx
from sqlalchemy.orm import Session

from .models import Proxy
from .security import SecretBox


@dataclass(frozen=True)
class ProxyConfig:
    id: int
    name: str
    protocol: str
    host: str
    port: int
    username: str | None
    password: str | None

    @property
    def server(self) -> str:
        return f"{self.protocol.lower()}://{self.host}:{self.port}"

    @property
    def url(self) -> str:
        if not self.username:
            return self.server
        user = quote(self.username, safe="")
        password = quote(self.password or "", safe="")
        return f"{self.protocol.lower()}://{user}:{password}@{self.host}:{self.port}"

    def playwright(self) -> dict[str, str]:
        value = {"server": self.server}
        if self.username:
            value["username"] = self.username
            value["password"] = self.password or ""
        return value


class ProxyManager:
    def __init__(self, session_factory: Any, secret_box: SecretBox) -> None:
        self.session_factory = session_factory
        self.secret_box = secret_box
        self._round_robin_index = 0
        self._lock = asyncio.Lock()

    def list_public(self) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            rows = session.query(Proxy).order_by(Proxy.name).all()
            return [self._public(row) for row in rows]

    @staticmethod
    def _public(row: Proxy) -> dict[str, Any]:
        return {
            "id": row.id,
            "name": row.name,
            "protocol": row.protocol,
            "host": row.host,
            "port": row.port,
            "username": row.username,
            "password_configured": bool(row.encrypted_password),
            "enabled": row.enabled,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    def create(self, data: dict[str, Any]) -> dict[str, Any]:
        self._validate(data)
        with self.session_factory() as session:
            row = Proxy(
                name=data["name"].strip(),
                protocol=data["protocol"].upper(),
                host=data["host"].strip(),
                port=int(data["port"]),
                username=(data.get("username") or "").strip() or None,
                encrypted_password=self.secret_box.encrypt(data.get("password")),
                enabled=bool(data.get("enabled", True)),
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._public(row)

    def update(self, proxy_id: int, data: dict[str, Any]) -> dict[str, Any]:
        self._validate(data)
        with self.session_factory() as session:
            row = session.get(Proxy, proxy_id)
            if row is None:
                raise KeyError(proxy_id)
            row.name = data["name"].strip()
            row.protocol = data["protocol"].upper()
            row.host = data["host"].strip()
            row.port = int(data["port"])
            row.username = (data.get("username") or "").strip() or None
            if data.get("password"):
                row.encrypted_password = self.secret_box.encrypt(data["password"])
            elif data.get("clear_password"):
                row.encrypted_password = None
            row.enabled = bool(data.get("enabled", True))
            session.commit()
            return self._public(row)

    def delete(self, proxy_id: int) -> None:
        with self.session_factory() as session:
            row = session.get(Proxy, proxy_id)
            if row is None:
                raise KeyError(proxy_id)
            session.delete(row)
            session.commit()

    @staticmethod
    def _validate(data: dict[str, Any]) -> None:
        if not str(data.get("name", "")).strip():
            raise ValueError("Ein Proxy-Name ist erforderlich.")
        if str(data.get("protocol", "")).upper() not in {"HTTP", "HTTPS", "SOCKS5"}:
            raise ValueError("Das Proxy-Protokoll muss HTTP, HTTPS oder SOCKS5 sein.")
        if not str(data.get("host", "")).strip():
            raise ValueError("Ein Proxy-Host ist erforderlich.")
        if not 1 <= int(data.get("port", 0)) <= 65535:
            raise ValueError("Der Proxy-Port muss zwischen 1 und 65535 liegen.")

    def _config(self, row: Proxy) -> ProxyConfig:
        return ProxyConfig(
            id=row.id,
            name=row.name,
            protocol=row.protocol,
            host=row.host,
            port=row.port,
            username=row.username,
            password=self.secret_box.decrypt(row.encrypted_password),
        )

    async def select(self, mode: str, fixed_id: int | None = None) -> ProxyConfig | None:
        if mode == "direct":
            return None
        with self.session_factory() as session:
            rows = session.query(Proxy).filter(Proxy.enabled.is_(True)).order_by(Proxy.id).all()
            if mode == "fixed":
                row = next((item for item in rows if item.id == fixed_id), None)
                if row is None:
                    raise RuntimeError("Der fest konfigurierte Proxy ist nicht aktiv oder nicht vorhanden.")
                return self._config(row)
            if not rows:
                raise RuntimeError("Im Proxy Pool ist kein aktiver Proxy verfügbar.")
            if mode == "random":
                return self._config(random.choice(rows))
            if mode == "round_robin":
                async with self._lock:
                    row = rows[self._round_robin_index % len(rows)]
                    self._round_robin_index = (self._round_robin_index + 1) % len(rows)
                return self._config(row)
        raise ValueError("Unbekannter Proxy-Modus.")

    async def test(self, proxy_id: int, test_url: str) -> dict[str, Any]:
        with self.session_factory() as session:
            row = session.get(Proxy, proxy_id)
            if row is None:
                raise KeyError(proxy_id)
            config = self._config(row)
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(proxy=config.url, timeout=15, follow_redirects=False) as client:
                response = await client.get(test_url)
            latency = round((time.perf_counter() - started) * 1000, 1)
            visible_ip = None
            content_type = response.headers.get("content-type", "")
            if "json" in content_type:
                try:
                    body = response.json()
                    if isinstance(body, dict):
                        visible_ip = body.get("ip") or body.get("origin")
                except ValueError:
                    pass
            return {
                "success": response.is_success,
                "status_code": response.status_code,
                "latency_ms": latency,
                "visible_source_ip": visible_ip,
                "explanation": "Die sichtbare Quell-IP stammt von der Antwort der Test-URL; HTTP-Header ändern die echte öffentliche Quell-IP nicht.",
            }
        except (httpx.HTTPError, OSError) as exc:
            return {
                "success": False,
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                "error": type(exc).__name__,
                "message": "Der konfigurierte Proxy ist nicht erreichbar.",
            }
