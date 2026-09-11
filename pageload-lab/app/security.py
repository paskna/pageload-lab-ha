"""URL validation, SSRF controls, secrets and log redaction."""

from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import socket
from pathlib import Path
from urllib.parse import urlparse

from cryptography.fernet import Fernet, InvalidToken


ALWAYS_BLOCKED_HOSTS = {
    "supervisor",
    "homeassistant",
    "home-assistant",
    "hassio",
    "docker",
    "host.docker.internal",
    "metadata",
    "metadata.google.internal",
}
ALWAYS_BLOCKED_IPS = {
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("100.100.100.200"),
}
SENSITIVE_URL = re.compile(r"(?P<scheme>(?:https?|socks5)://)(?P<creds>[^/@\s]+@)", re.IGNORECASE)


class URLSecurityError(ValueError):
    pass


def _dangerous_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address, allow_private: bool) -> bool:
    if address in ALWAYS_BLOCKED_IPS:
        return True
    if address.is_unspecified or address.is_multicast or address.is_reserved:
        return True
    if not allow_private and (address.is_private or address.is_loopback or address.is_link_local):
        return True
    return False


async def validate_target_url(url: str, allow_private: bool = False) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise URLSecurityError("Nur http:// und https:// URLs sind erlaubt.")
    if not parsed.hostname or parsed.username or parsed.password:
        raise URLSecurityError("Die Ziel-URL ist ungültig; Zugangsdaten in der URL sind nicht erlaubt.")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname in ALWAYS_BLOCKED_HOSTS or hostname.endswith(".internal") or hostname.endswith(".local"):
        raise URLSecurityError("Dieses interne Systemziel ist aus Sicherheitsgründen gesperrt.")
    try:
        literal = ipaddress.ip_address(hostname)
        addresses = [literal]
    except ValueError:
        loop = asyncio.get_running_loop()
        try:
            infos = await loop.getaddrinfo(hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise URLSecurityError("Der Hostname der Ziel-URL konnte nicht aufgelöst werden.") from exc
        addresses = list({ipaddress.ip_address(info[4][0]) for info in infos})
    if any(_dangerous_ip(address, allow_private) for address in addresses):
        if allow_private:
            raise URLSecurityError("Die Ziel-URL verweist auf einen immer gesperrten System- oder Metadata-Endpunkt.")
        raise URLSecurityError("Private Netzwerkziele sind standardmässig gesperrt. Eine bewusste Freigabe ist in den erweiterten Einstellungen möglich.")
    return url


def redact(value: str) -> str:
    return SENSITIVE_URL.sub(lambda match: f"{match.group('scheme')}***:***@", value)


class SecretBox:
    def __init__(self, key_path: Path) -> None:
        self.key_path = key_path
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.key_path.exists():
            self.key_path.write_bytes(Fernet.generate_key())
            os.chmod(self.key_path, 0o600)
        self.fernet = Fernet(self.key_path.read_bytes().strip())

    def encrypt(self, value: str | None) -> str | None:
        return self.fernet.encrypt(value.encode()).decode() if value else None

    def decrypt(self, value: str | None) -> str | None:
        if not value:
            return None
        try:
            return self.fernet.decrypt(value.encode()).decode()
        except InvalidToken as exc:
            raise RuntimeError("Stored proxy password cannot be decrypted") from exc


def load_or_create_secret(path: Path, length: int = 32) -> str:
    if not path.exists():
        path.write_bytes(os.urandom(length))
        os.chmod(path, 0o600)
    return path.read_bytes().hex()
