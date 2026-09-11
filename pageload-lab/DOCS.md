# PageLoad Lab 0.1.0 – Betriebshandbuch

## Zweck und Verantwortung

PageLoad Lab führt wiederholbare Seitenlade- und HTTP-Tests gegen Systeme aus, die dir gehören oder für deren Test du ausdrücklich autorisiert bist. Die Anwendung enthält keine Funktionen zum Umgehen von Rate Limits, WAFs, CAPTCHAs, Login- oder DDoS-Schutz. Sie erzeugt keine angeblich zufälligen IP-Adressen, lädt keine öffentlichen Proxy-Listen und führt keine Proxy-Scans aus.

## Installation in Home Assistant OS

1. Öffne **Einstellungen → Apps → App Store**. In älteren Home-Assistant-Versionen heißt der Bereich **Add-ons**.
2. Öffne das Repository-Menü und füge `https://github.com/paskna/pageload-lab-ha` hinzu.
3. Öffne **PageLoad Lab** und wähle **Installieren**.
4. Starte die App. Aktiviere optional „Beim Systemstart starten“ und „In Seitenleiste anzeigen“.
5. Wähle **Web UI öffnen**. Home Assistant authentifiziert den Zugriff via Ingress; es gibt kein zusätzliches Konto.
6. Bestätige im Onboarding den autorisierten Verwendungszweck und prüfe die konservativen Limits.

Die Anwendung benötigt weder SSH noch eine manuelle Python-/Chromium-Installation. Sie verwendet kein `host_network`, keine Supervisor-API, keine Home-Assistant-API, keine privilegierten Capabilities und keinen Zugriff auf `/homeassistant`.

## Einen Test konfigurieren

### Ziel und Modus

Nur `http://` und `https://` sind zulässig. **Real Browser** lädt die Seite in einem isolierten Chromium Context, führt JavaScript aus und lädt Ressourcen. **HTTP Test** führt einen leichten Request aus und ignoriert Aufenthaltsdauer.

Private Ziele sind standardmäßig gesperrt. Sie können unter **Einstellungen → Erweiterte Netzwerkziele** bewusst freigegeben werden, beispielsweise für einen eigenen lokalen Testserver. Supervisor-, Docker-, Metadata-, `.internal`-, `.local`-, reservierte und gefährliche Link-Local-Ziele bleiben gesperrt. Die Prüfung gilt auch für Redirects und Browser-Unterressourcen.

### Frequenz

Bei gleichmäßiger Verteilung wird ein 60-Sekunden-Fenster exakt aufgeteilt. Fünf Aufrufe ergeben Offsets `0, 12, 24, 36, 48`. Bei zufälliger Verteilung werden pro 60-Sekunden-Fenster exakt so viele sortierte Offsets erzeugt, wie die Frequenz vorgibt. Dadurch bleibt der langfristige Mittelwert erhalten.

### Aufenthaltsdauer und Parallelität

Eine Browserseite kann ohne Aufenthalt, für eine feste Dauer oder für eine zufällige Dauer zwischen Minimum und Maximum offen bleiben. Danach schließt PageLoad Lab den Context immer in einem `finally`-Block. Contexts werden aus Gründen der Sitzungsisolation nicht zwischen unabhängigen Aufrufen wiederverwendet; ein einzelner Chromium-Prozess wird hingegen geteilt und bei einem Crash automatisch neu gestartet.

Die erwartete Parallelität wird konservativ als `Aufrufe pro Minute × maximale Aufenthaltsdauer / 60` berechnet. Bei zu kleiner testspezifischer Parallelität erscheint vor dem Start eine Warnung. Globale Limits sind harte Obergrenzen.

### Beendigung und Abbruch

Genau einer der Modi Zeitdauer, Enddatum oder Aufrufanzahl ist aktiv. Der Aufrufanzahl-Modus reserviert jeden Start vor dem Erzeugen des Worker-Tasks; deshalb werden auch bei Parallelität exakt die konfigurierten Sessions erzeugt. Optional stoppen Fehlerquote, aufeinanderfolgende Fehler, lange Ladezeit, Timeout-Anzahl, HTTP 429 oder HTTP 503 den Test kontrolliert.

**Pause** verhindert neue Sessions und lässt laufende Sessions fertig werden. **Fortsetzen** beginnt ein neues Frequenzfenster und erzeugt keinen Nachhol-Burst. **Stoppen** verlangt eine Bestätigung, startet nichts Neues und wartet auf aktive Sessions.

## Proxy Pool

Unterstützt werden HTTP, HTTPS und SOCKS5. Benutzername ist optional; Passwörter werden mit Fernet verschlüsselt. Der Schlüssel wird beim ersten Start mit Modus `0600` in `/data/secret.key` erzeugt. Passwörter werden nie zurückgegeben, im GUI nur als konfiguriert markiert und von Logs ferngehalten.

Auswahlmodi:

- **Direkt**: keine Proxy-Verbindung.
- **Zufällig**: zufällige Auswahl aus aktiven, vom Administrator hinterlegten Proxys.
- **Round Robin**: zyklische Auswahl aus aktiven Proxys.
- **Fest**: genau ein aktiver Proxy.

„Verbindung testen“ ruft ausschließlich die unter Einstellungen definierte Proxy-Test-URL auf. Liefert sie JSON mit `ip` oder `origin`, zeigt die Oberfläche die von diesem Dienst beobachtete Quell-IP. HTTP-Header ändern keine echte öffentliche Quell-IP.

## Messung und Reporting

Browser-Aufrufe erfassen HTTP-Status, Navigation/Gesamtladezeit, TTFB, DOMContentLoaded, Load Event, tatsächliche Aufenthaltsdauer, Proxy, Fehlertyp und eine bereinigte Fehlermeldung. Werte stammen, soweit vorhanden, aus `PerformanceNavigationTiming`. HTTP-Tests erfassen die Gesamtdauer; Browser-spezifische Felder bleiben korrekt leer.

Die Live-Ansicht aktualisiert sich ressourcenschonend alle fünf Sekunden. Zeitreihen werden in SQLite pro Minute aggregiert und Canvas-Diagramme ohne externes Chart-Framework gezeichnet. Einzelrequests sind auf 50 Zeilen paginiert. CSV und JSON enthalten reale Daten des gewählten Tests.

P50/P90/P95/P99 werden über indizierte, sortierte SQLite-Abfragen bestimmt, ohne hunderttausende Werte in Python zu laden. Status- und Fehlergruppen werden direkt in SQL aggregiert.

## Persistenz, Migration und Backup

Alle persistenten Daten liegen ausschließlich unter `/data`:

- `/data/pageloadlab.db` – Tests, Requests, Proxys, Einstellungen, Schema-Version und Snapshots
- `/data/reports/` – reserviert für dauerhaft erzeugte Report-Artefakte
- `/data/logs/` – rotierende JSON-Logs
- `/data/secret.key` – Proxy-Verschlüsselungsschlüssel
- `/data/csrf.secret` – CSRF-Geheimnis

Home Assistant bindet `/data` automatisch persistent ein und nimmt es in App-Backups auf. `backup: cold` stoppt die App für einen konsistenten Snapshot. `CREATE TABLE IF NOT EXISTS` und eine versionierte, transaktionale Migration laufen bei jedem Start vor Scheduler und Webserver. Updates des Images ersetzen `/data` nicht.

Einzelrequests können nach 7, 30, 90, 180 oder 365 Tagen bereinigt oder unbegrenzt behalten werden. Beim Abschluss jedes Tests bleibt ein unabhängiger aggregierter Snapshot bestehen.

## Neustart und Fehlerfälle

Beim Start werden Datenpfade und Datenbank geprüft, Defaults angelegt, Geheimnisse geladen/erzeugt und Chromium geprüft. Tests, die vor dem Neustart `running`, `paused` oder `waiting_capacity` waren, werden auf **Unterbrochen** gesetzt. Sie können anschließend manuell gestartet werden; ein Neustart erzeugt keine unkontrollierten Doppelaufrufe.

Beim Stoppen werden Scheduler und neue Starts angehalten, Runner erhalten ein Shutdown-Signal, aktive Worker sauber abgewartet, Tests als **Unterbrochen** gespeichert, Browser und Datenbank geschlossen. Chromium-Crashes werden als eigener Fehlertyp gespeichert und die Engine beim nächsten Aufruf neu aufgebaut.

## System und Logs

Die Systemseite zeigt Versionen, Architektur, Datenbankintegrität/-größe, Test- und Requestanzahl, freien Speicher, Prozess-RAM, Browser-Sessions und Scheduler-Zustand. Dort stehen Datenbankprüfung, Retention-Bereinigung und Log-Download bereit.

Logs sind JSON-Zeilen mit DEBUG/INFO/WARNING/ERROR, rotieren bei 5 MiB und behalten fünf Archive. Proxy-Zugangsdaten, Passwörter, Schlüssel und Home-Assistant-Tokens werden nicht geloggt. Das Add-on fordert keine Home-Assistant-Tokens an.

## Healthcheck

`GET /health` prüft Backend, SQLite, Scheduler und Browser Engine. `healthy` bedeutet, dass alle Komponenten verfügbar sind. Bei einer fehlenden Browser Engine bleibt die Oberfläche mit `degraded` erreichbar; HTTP-Tests und Diagnose bleiben damit nutzbar. Ein Datenbank- oder Scheduler-Fehler liefert HTTP 503.

## Build und Veröffentlichung

Das Dockerfile verwendet explizit `python:3.12.11-slim-bookworm`. Die reproduzierbar gepinnte Playwright-Version 1.61 installiert den zugehörigen Chromium-Build und seine Debian-12-Systemabhängigkeiten für ARM64 bzw. AMD64. Es gibt keine `BUILD_FROM`- oder `build.yaml`-Abhängigkeit.

Der Workflow `.github/workflows/build.yml` führt Unit- und echte Chromium-Integrationstests aus, erstellt über die aktuellen Home-Assistant-Builder-Composite-Actions getrennte `aarch64`- und `amd64`-Images und veröffentlicht bei einem GitHub Release ein signiertes Multi-Arch-Manifest:

`ghcr.io/paskna/pageload-lab:0.1.0`

Für lokale Entwicklung kann direkt gebaut werden:

```bash
docker build --platform linux/amd64 -t pageload-lab:dev pageload-lab
docker build --platform linux/arm64 -t pageload-lab:dev-arm64 pageload-lab
```

## Bekannte Einschränkungen

- Browser-Performance-Werte hängen davon ab, was Chromium und die Zielseite über die Navigation Timing API bereitstellen. Cross-Origin-Unterressourcen liefern ohne `Timing-Allow-Origin` keine separaten Detail-Timings; die Hauptnavigation bleibt messbar.
- SQLite ist auf den Einzelknoten-Betrieb des Home-Assistant-Add-ons ausgelegt, nicht auf horizontale Mehrknoten-Ausführung.
- Ein Proxy kann die vom Ziel beobachtete Quell-IP ändern, garantiert aber weder geografische Herkunft noch Erreichbarkeit; PageLoad Lab trifft dazu keine Behauptung.
- Ein Image wird erst aus einem GitHub Custom Repository installierbar, nachdem der Release-Workflow das zur `config.yaml`-Version passende öffentliche GHCR-Tag veröffentlicht hat.

## Support-Diagnose

Bei einem Problem zuerst die Systemseite und den Healthcheck prüfen, anschließend die strukturierten Logs herunterladen. Fehlermeldungen im GUI sind bewusst nutzerverständlich; technische Details stehen im Log, jedoch ohne Geheimnisse.
