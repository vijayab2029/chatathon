#!/usr/bin/env bash
# Serve the repo root so ui/index.html can fetch ../data/*.json.
# Opening ui/index.html directly also works, but falls back to an inlined
# snapshot of the data instead of reading the live files.
set -euo pipefail
cd "$(dirname "$0")"
PORT="${1:-8080}"
echo "Demo:  http://localhost:${PORT}/ui/"
python3 -m http.server "$PORT"
