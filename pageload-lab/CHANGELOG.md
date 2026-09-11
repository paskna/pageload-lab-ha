# Changelog

Alle wesentlichen Änderungen werden nach Keep-a-Changelog-Grundsätzen dokumentiert. Das Projekt verwendet Semantic Versioning.

## 0.1.0 – 2026-09-11

### Added

- Erstveröffentlichung als Home Assistant Ingress App für `aarch64` und `amd64`.
- FastAPI-Adminoberfläche mit Onboarding, Dashboard, Testhistorie, Live-Report, Proxy Pool, Einstellungen und Systemdiagnose.
- Playwright-Chromium-Worker und HTTP-Worker mit echter Aufenthaltsdauer, Navigation Timing, Fehlerklassifikation und sauberem Context-Lifecycle.
- APScheduler-Koordination, gleichmäßige und exakt minutenweise zufällige Verteilung, Parallelitätssemaphoren und Graceful Shutdown.
- SQLite-Datenmodell, WAL-Modus, Indizes, Schema-Versionierung, automatische Recovery und permanente Report-Snapshots.
- Verschlüsselte Proxy-Passwörter, SSRF-Schutz, CSRF-Prüfung, strukturierte rotierende Logs und eigenes AppArmor-Profil.
- CSV-/JSON-Export, paginierte Einzelrequests, Retention und Wartungsfunktionen.
- Multi-Arch-GHCR-Workflow mit den aktuellen Home-Assistant-Builder-Actions.
