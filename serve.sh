#!/usr/bin/env bash
# Serve the repo root so part5/ui/index.html can fetch ../data/*.json.
# Opening part5/ui/index.html directly also works, but falls back to an inlined
# snapshot of the data instead of reading the live files.
set -euo pipefail
cd "$(dirname "$0")"
PORT="${1:-8080}"
echo "Demo:  http://localhost:${PORT}/part5/ui/"
python3 -m http.server "$PORT"
