#!/usr/bin/env bash
# ===========================================================
# Offline Survival Computer – Linux Installer
# ===========================================================
# Installs Docker, Docker Compose (if missing), creates the
# necessary data directories, copies the example .env, and
# starts all services.
#
# Usage:
#   chmod +x install.sh
#   ./install.sh
# ===========================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }

echo ""
echo "  ╔══════════════════════════════════════════╗"
echo "  ║     Offline Survival Computer            ║"
echo "  ║     Installation Script                  ║"
echo "  ╚══════════════════════════════════════════╝"
echo ""

# -----------------------------------------------------------
# 1. Check / install Docker
# -----------------------------------------------------------
if command -v docker &>/dev/null; then
  success "Docker is already installed: $(docker --version)"
else
  info "Docker not found. Installing via get.docker.com …"
  curl -fsSL https://get.docker.com | sh
  sudo usermod -aG docker "$USER"
  success "Docker installed. You may need to log out and back in for group changes to take effect."
fi

# -----------------------------------------------------------
# 2. Check / install Docker Compose plugin
# -----------------------------------------------------------
if docker compose version &>/dev/null 2>&1; then
  success "Docker Compose plugin available: $(docker compose version --short)"
elif command -v docker-compose &>/dev/null; then
  success "docker-compose (standalone) found: $(docker-compose --version)"
else
  info "Docker Compose plugin not found. Installing …"
  COMPOSE_VERSION=$(curl -s https://api.github.com/repos/docker/compose/releases/latest \
    | grep '"tag_name"' | cut -d '"' -f4)
  sudo mkdir -p /usr/local/lib/docker/cli-plugins
  sudo curl -SL \
    "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-$(uname -m)" \
    -o /usr/local/lib/docker/cli-plugins/docker-compose
  sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
  success "Docker Compose installed."
fi

# -----------------------------------------------------------
# 3. Create data directories
# -----------------------------------------------------------
info "Creating data directories …"
mkdir -p data/zim data/notes data/uploads data/documents data/maps/tiles
success "Data directories ready."

# -----------------------------------------------------------
# 4. Copy .env if it doesn't exist
# -----------------------------------------------------------
if [ ! -f .env ]; then
  cp .env.example .env
  warn ".env created from .env.example — please edit it to set a strong SECRET_KEY."
else
  info ".env already exists, skipping copy."
fi

# -----------------------------------------------------------
# 5. Build and start services
# -----------------------------------------------------------
info "Building Docker images …"
docker compose build

info "Starting services …"
docker compose up -d

# -----------------------------------------------------------
# 6. Post-install tips
# -----------------------------------------------------------
echo ""
success "Installation complete!"
echo ""

# Source .env to pick up port overrides (if any)
# shellcheck disable=SC1091
[ -f .env ] && set -a && . ./.env && set +a

echo -e "  ${CYAN}Services started:${NC}"
echo -e "  • Command Center:  http://localhost:${APP_PORT:-5000}"
echo -e "  • Kiwix Library:   http://localhost:${KIWIX_PORT:-8080}"
echo -e "  • Ollama API:      http://localhost:${OLLAMA_PORT:-11434}"
echo ""
echo -e "  ${CYAN}Next steps:${NC}"
echo "  1. Pull an AI model:"
echo "       docker compose exec ollama ollama pull llama3"
echo ""
echo "  2. Download .zim content files and place them in data/zim/"
echo "     then restart kiwix:"
echo "       docker compose restart kiwix"
echo ""
echo "  3. To stop all services:"
echo "       docker compose down"
echo ""
echo "  4. To view logs:"
echo "       docker compose logs -f"
echo ""
