#!/bin/bash
set -e

REMOTE="${REMOTE:-user@host}"
REMOTE_DIR="${REMOTE_DIR:-/path/to/openclaw-proxy}"

echo "=== Openclaw Reverse Proxy Deployment ==="
echo ""
echo "Target: $REMOTE:$REMOTE_DIR"
echo ""

# Build and copy files to remote
echo "[1/4] Syncing files to remote..."
rsync -avz --delete \
  --exclude '.mimocode' \
  --exclude '.git' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '.env' \
  ./ "$REMOTE:$REMOTE_DIR/"

# Deploy with docker compose
echo ""
echo "[2/4] Building and starting container..."
ssh "$REMOTE" "cd $REMOTE_DIR && sudo docker compose up -d --build"

# Wait for container to be healthy
echo ""
echo "[3/4] Waiting for container..."
sleep 3

# Create initial token if none exists
echo ""
echo "[4/4] Setting up initial token..."
ssh "$REMOTE" "cd $REMOTE_DIR && sudo docker exec openclaw-proxy python3 -c \"
import json, secrets
from pathlib import Path
p = Path('/data/tokens.json')
if not p.exists() or not json.loads(p.read_text()).get('tokens'):
    token = 'oc_' + secrets.token_hex(16)
    p.write_text(json.dumps({'tokens': {'admin': token}}, indent=2))
    print(f'Initial admin token: {token}')
else:
    print('Tokens already configured')
    tokens = json.loads(p.read_text()).get('tokens', {})
    for name in tokens:
        print(f'  {name}: {tokens[name][:12]}...')
\""

echo ""
echo "=== Deployment Complete ==="
echo "Web UI: http://<your-host>:41000/"
echo "API:    http://<your-host>:41000/v1/chat/completions"
