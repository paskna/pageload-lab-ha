import pytest

from app.security import SecretBox, URLSecurityError, redact, validate_target_url


@pytest.mark.parametrize("url", ["ftp://example.com", "https://user:pass@example.com", "not-a-url"])
async def test_rejects_invalid_schemes_and_credentials(url):
    with pytest.raises(URLSecurityError):
        await validate_target_url(url)


@pytest.mark.parametrize("url", ["http://169.254.169.254/latest/meta-data", "http://100.100.100.200/", "http://supervisor/core/api", "http://metadata.google.internal/"])
async def test_always_blocks_system_and_metadata_targets(url):
    with pytest.raises(URLSecurityError):
        await validate_target_url(url, allow_private=True)


async def test_private_targets_require_explicit_opt_in():
    with pytest.raises(URLSecurityError):
        await validate_target_url("http://127.0.0.1:8090")
    assert await validate_target_url("http://127.0.0.1:8090", allow_private=True) == "http://127.0.0.1:8090"


async def test_public_literal_is_allowed():
    assert await validate_target_url("https://93.184.216.34/") == "https://93.184.216.34/"


def test_secret_box_encrypts_and_redacts(tmp_path):
    box = SecretBox(tmp_path / "secret.key")
    encrypted = box.encrypt("very-secret")
    assert encrypted and "very-secret" not in encrypted
    assert box.decrypt(encrypted) == "very-secret"
    assert "alice:secret" not in redact("http://alice:secret@proxy.example:8080")
