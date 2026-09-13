# Changelog

Alle wesentlichen Änderungen werden nach Keep-a-Changelog-Grundsätzen dokumentiert. Das Projekt verwendet Semantic Versioning.

## 0.1.3 – 2026-09-13

### Fixed

- Ingress-Assets verwenden beim ersten Request ohne `root_path` einen relativen Basis-Pfad.
- Onboarding bleibt dadurch inklusive CSS und JavaScript innerhalb des Home-Assistant-Ingress erreichbar.

## 0.1.2 – 2026-09-13

### Fixed

- Health-Endpoint funktioniert auch unter dem restriktiven Home-Assistant-AppArmor-Profil.
- Die optionale Zombie-Prozessprüfung fällt bei nicht lesbarem `/proc` sicher auf keine beobachtbaren Zombies zurück.

## 0.1.1 – 2026-09-12

### Fixed

- Home-Assistant-Startfehler durch fehlendes AppArmor-Ausführungsrecht für `run.sh` behoben.
- Ausschließlich die erforderlichen Einstiegspunkt-, Python-, Uvicorn-, Playwright-Node- und Chromium-Pfade zur Ausführung zugelassen.
- Pauschale Ausführungsrechte unter `/usr`, `/bin`, `/lib` und `/sbin` durch eng begrenzte Lese- und Library-Mapping-Rechte ersetzt.
- Strikte Container-Isolation ohne zusätzliche Home-Assistant-, Host-, Docker- oder privilegierte Rechte beibehalten.

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
