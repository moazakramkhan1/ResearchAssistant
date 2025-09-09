#!/usr/bin/env bash
set -euo pipefail

echo "Waiting for containers..."
sleep 6

echo "Pulling local LLM (llama3.1:8b) into the Ollama container..."
docker compose exec -T ollama bash -lc "ollama pull llama3.1:8b || true"

echo "Fetching CSL styles into frontend/public/styles..."
mkdir -p frontend/public/styles
# Minimal placeholders; you can replace these with real CSL files later.
cat > frontend/public/styles/apa.csl <<'EOF'
<!-- placeholder APA CSL; replace with the official style as needed -->
<style xmlns="http://purl.org/net/xbiblio/csl" class="in-text"/>
EOF
cat > frontend/public/styles/mla.csl <<'EOF'
<!-- placeholder MLA CSL -->
<style xmlns="http://purl.org/net/xbiblio/csl" class="in-text"/>
EOF
cat > frontend/public/styles/chicago-author-date.csl <<'EOF'
<!-- placeholder Chicago Author-Date CSL -->
<style xmlns="http://purl.org/net/xbiblio/csl" class="in-text"/>
EOF

echo "Checking backend health..."
curl -sf http://localhost:8000/healthz >/dev/null && echo "Backend OK" || echo "Backend not up yet"

echo "Open n8n at http://localhost:5678 (use creds from .env) and import n8n/workflows/main-pipeline.json"
echo "Then open app at http://localhost:5173"
