# Docker Update Dashboard

Übersicht über alle laufenden Docker-Container mit automatischer Prüfung auf verfügbare Image-Updates.

## Features
- Echtzeit-Übersicht aller laufenden Container
- Automatische Prüfung auf Image-Updates via Registry API
- Unterstützung für Docker Hub, ghcr.io und generische v2-Registries
- Konfigurierbares Cache-Intervall
- Responsives Dashboard mit Live-Status

## Screenshot
<!-- Screenshot hier einfügen -->

## Schnellstart

```bash
docker compose up -d
```

Dashboard öffnen: [http://localhost:8080](http://localhost:8080)

## Konfiguration

| Variable | Beschreibung | Standard |
|----------|-------------|----------|
| `CHECK_INTERVAL` | Cache-TTL für Update-Prüfungen in Sekunden | `300` |

## Architektur

- **Backend:** Python / FastAPI (uvicorn)
- **Frontend:** Vanilla HTML/CSS/JS (statisch ausgeliefert)
- **Update-Check:** Vergleich lokaler Image-Digests mit Remote-Registry via Docker Registry HTTP API v2

## Unterstützte Registries

- Docker Hub (`docker.io`)
- GitHub Container Registry (`ghcr.io`)
- Generische Docker Registry v2-kompatible Registries

## Entwicklung

Lokales Setup ohne Docker:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```

## Lizenz

MIT
