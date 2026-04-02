# 🖥️ Offline Survival Computer

A self-contained, offline-first personal server packed with knowledge and tools.
Everything runs on your local machine — no internet required.

Access a central **Command Center** web dashboard from any browser on your network.

---

## Features

| Module | Description |
|---|---|
| 📚 **Information Library** | Offline Wikipedia, medical references, survival guides via [Kiwix](https://www.kiwix.org/) |
| 🤖 **AI Assistant** | Private ChatGPT-style chat powered by [Ollama](https://ollama.com/) — upload your own documents |
| 🎓 **Education** | Khan Academy, Wikibooks, TED Talks and more — all offline via Kiwix |
| 🗺️ **Offline Maps** | Interactive maps with OpenStreetMap tiles (supports fully offline tile cache) |
| 📝 **Notes** | Simple, searchable note-taking with tags — stored locally in SQLite |
| 🔐 **Authentication** | Built-in admin login (defaults: `admin` / `offline`, override via `ADMIN_USERNAME` / `ADMIN_PASSWORD`) |
| 📁 **File Manager** | Upload, download, and delete files stored on the device |
| 💾 **Backup & Restore** | Export/import a `.tar.gz` snapshot of notes, uploads, documents, and maps |
| 📊 **System Monitor** | CPU, RAM, disk usage and service health, plus a system page |
| 🧭 **Survival Tools** | Water treatment, calorie planning, medication dosing, and fire-starting helpers |
| 📦 **Inventory** | Track supplies with quantity, category, and location metadata |

---

## Quick Start

### Prerequisites
- Linux (Ubuntu 20.04+ recommended)
- Docker & Docker Compose (the installer can set these up for you)

### 1. Clone and install

```bash
git clone https://github.com/im0d00/offline-survival-computer.git
cd offline-survival-computer
chmod +x install.sh
./install.sh
```

The installer will:
- Check for / install Docker
- Create the required data directories
- Copy `.env.example` → `.env`
- Build Docker images and start all services

### 2. Open the dashboard

Navigate to **http://localhost:5000** in your browser.

---

## Manual Setup (without the installer)

```bash
# 1. Copy the environment file
cp .env.example .env
# Edit .env and set a strong SECRET_KEY

# 2. Create data directories
mkdir -p data/zim data/notes data/uploads data/documents data/maps/tiles

# 3. Build and start
docker compose up -d --build
```

---

## Services

| Service | Default Port | Purpose |
|---|---|---|
| Command Center (Flask) | **5000** | Main web dashboard |
| Kiwix | **8080** | Offline content server |
| Ollama | **11434** | Local LLM API |

---

## Getting Content

### 📚 Adding Library / Education Content (Kiwix `.zim` files)

1. Visit [library.kiwix.org](https://library.kiwix.org) while online.
2. Download one or more `.zim` files — see recommendations below.
3. Place them in the `data/zim/` directory.
4. Restart Kiwix: `docker compose restart kiwix`
5. Open **http://localhost:8080** or use the Library/Education pages.

**Recommended ZIM files:**

| File | Size | Content |
|---|---|---|
| `wikipedia_en_all_nopic` | ~20 GB | Full English Wikipedia (no images) |
| `kiwix.khan-academy_en_all` | ~14 GB | Khan Academy – all subjects |
| `wikimed_en_all` | ~2 GB | Medical encyclopedia |
| `wikibooks_en_all` | ~4 GB | Free textbooks |
| `ted` | ~3 GB | TED Talks |
| `gutenberg_en_all` | ~60 GB | Project Gutenberg eBooks |

### 🤖 Pulling AI Models (Ollama)

```bash
# Pull a model (requires internet access once)
docker compose exec ollama ollama pull llama3

# Other recommended models:
docker compose exec ollama ollama pull mistral
docker compose exec ollama ollama pull phi3
docker compose exec ollama ollama pull gemma2:2b   # smallest / fastest
```

Models are stored in the `ollama_data` Docker volume and persist across restarts.

### 🗺️ Offline Maps

By default the map uses live OpenStreetMap tiles (requires internet).

For **fully offline maps**:
1. Download map tiles for your region using a tool like [MOBAC](https://mobac.sourceforge.io/) or wget.
2. Place tiles at `data/maps/tiles/{z}/{x}/{y}.png`.
3. Edit `.env` and set `MAPS_TILE_URL=/tiles/{z}/{x}/{y}.png` (already the default — just ensure tiles exist).
4. Restart the app: `docker compose restart app`

---

## Configuration

All configuration is done via the `.env` file:

```env
# Strong random secret for Flask sessions
SECRET_KEY=change-me-in-production-use-a-long-random-string

# Host ports
APP_PORT=5000
KIWIX_PORT=8080
OLLAMA_PORT=11434

# Maps tile URL (blank = live OSM, or point to local tile server)
MAPS_TILE_URL=/tiles/{z}/{x}/{y}.png
# Admin credentials (override defaults of admin/offline)
ADMIN_USERNAME=admin
ADMIN_PASSWORD=change-me
```

---

## GPU Acceleration (Ollama)

To enable GPU acceleration for Ollama, uncomment the `deploy` section in `docker-compose.yml`:

```yaml
ollama:
  ...
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            count: all
            capabilities: [gpu]
```

Requires the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html).

---

## Development & Testing

```bash
# Install Python dependencies
pip install -r app/requirements.txt
pip install pytest pytest-flask

# Run tests
pytest tests/ -v
```

---

## Project Structure

```
offline-survival-computer/
├── app/
│   ├── main.py              # Flask application
│   ├── requirements.txt
│   ├── Dockerfile
│   ├── templates/
│   │   ├── base.html
│   │   ├── index.html       # Command Center dashboard
│   │   ├── library.html     # Information Library
│   │   ├── ai_assistant.html
│   │   ├── education.html
│   │   ├── maps.html
│   │   ├── notes.html
│   │   ├── notes_edit.html
│   │   └── notes_view.html
│   └── static/
│       ├── css/style.css
│       └── js/app.js
├── tests/
│   └── test_app.py
├── data/                    # Persistent data (git-ignored)
│   ├── zim/                 # Kiwix .zim content files
│   ├── notes/               # SQLite notes database
│   ├── uploads/             # File uploads
│   ├── documents/           # AI knowledge-base documents
│   └── maps/                # Offline map tiles
├── docker-compose.yml
├── .env.example
├── install.sh
└── README.md
```

---

## Useful Commands

```bash
# Start all services
docker compose up -d

# Stop all services
docker compose down

# View logs
docker compose logs -f

# View logs for a single service
docker compose logs -f app

# Rebuild after code changes
docker compose up -d --build app

# List available Ollama models
docker compose exec ollama ollama list

# Open a shell in the app container
docker compose exec app bash
```

---

## Privacy & Security

- **Everything runs locally** — no data leaves your machine.
- Change `SECRET_KEY` in `.env` before exposing the service to a network.
- The app listens on all interfaces by default (`0.0.0.0:5000`). Use a firewall to restrict access if needed.

---

## License

MIT — see [LICENSE](LICENSE).
