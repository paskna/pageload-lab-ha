# PageLoad Lab

PageLoad Lab is a self-contained Home Assistant app (add-on) for controlled page-load tests against websites you own or are expressly authorized to test. It uses real Chromium sessions or lightweight HTTP requests, retains test history in SQLite, supports an administrator-managed proxy pool, and exposes a responsive Home Assistant Ingress UI.

> Use PageLoad Lab only for websites and systems that belong to you or for which you have explicit testing authorization.

## Technology and repository layout

- Python 3.12, FastAPI, SQLAlchemy/SQLite, APScheduler, Playwright/Chromium
- dependency-free HTML/CSS/JavaScript UI with lightweight five-second live polling and Canvas charts
- `repository.yaml` – Home Assistant custom repository metadata
- `pageload-lab/config.yaml` – app manifest and Ingress/security configuration
- `pageload-lab/app/` – API, UI, scheduler, workers, persistence, reporting and proxy management
- `pageload-lab/tests/` – unit, lifecycle, security and real-browser integration tests
- `.github/workflows/build.yml` – tests, ARM64/AMD64 images and the GHCR multi-architecture manifest

## Installation

1. In Home Assistant, open **Settings → Apps → App store** (called **Add-ons** in older releases).
2. Open the repository menu, choose **Repositories**, and add `https://github.com/paskna/pageload-lab-ha`.
3. Select **PageLoad Lab**, install it, and start it.
4. Select **Open Web UI**. No separate PageLoad Lab account is required; Home Assistant Ingress provides authentication.
5. Complete the first-run safety onboarding.

No SSH access or Python installation on Home Assistant OS is required. Application data, reports, settings, proxy configuration, encryption material, and logs are kept below `/data` and are included in Home Assistant app backups.

## Images and architectures

- Version: `0.1.3`
- Image: `ghcr.io/paskna/pageload-lab`
- Architectures: `aarch64`, `amd64`
- Primary target: Home Assistant OS on Raspberry Pi 5 (ARM64)

Images are published by the workflow in `.github/workflows/build.yml`. A release builds architecture-specific images in parallel and publishes a signed multi-architecture manifest. Pull requests build both platforms without publishing.

## Development

```bash
cd pageload-lab
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
playwright install chromium
PAGELAB_DATA_DIR=/tmp/pageload-lab-dev PAGELAB_TESTING=1 uvicorn app.main:app --port 8099
pytest
```

For a complete description, configuration notes, safety model, and operations guide, see [pageload-lab/DOCS.md](pageload-lab/DOCS.md).

## License

MIT. See [LICENSE](LICENSE).
