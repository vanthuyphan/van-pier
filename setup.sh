#!/bin/bash
set -e

echo "================================"
echo "  BYOA - Bring Your Own Agent"
echo "  Setup Script"
echo "================================"
echo ""

# Step 1: Generate Synapse config
echo "1. Generating Synapse config..."
docker run -it --rm \
  -v "$(pwd)/synapse/data:/data" \
  -e SYNAPSE_SERVER_NAME=byoa.local \
  -e SYNAPSE_REPORT_STATS=no \
  matrixdotorg/synapse:latest generate

# Enable registration for agents
echo "" >> synapse/data/homeserver.yaml
echo "# BYOA: Allow agent registration" >> synapse/data/homeserver.yaml
echo "enable_registration: true" >> synapse/data/homeserver.yaml
echo "enable_registration_without_verification: true" >> synapse/data/homeserver.yaml
echo "suppress_key_server_warning: true" >> synapse/data/homeserver.yaml

echo ""
echo "2. Starting Matrix homeserver + Element..."
docker compose up -d

echo ""
echo "3. Waiting for Synapse to be ready..."
until curl -sf http://localhost:8008/health > /dev/null 2>&1; do
  sleep 2
  echo "  waiting..."
done
echo "  Synapse is ready!"

echo ""
echo "4. Creating admin user..."
docker exec -it byoa-synapse register_new_matrix_user \
  -u admin -p admin123 -a \
  -c /data/homeserver.yaml \
  http://localhost:8008 || echo "  (admin may already exist)"

echo ""
echo "5. Installing Python dependencies..."
pip install -r requirements.txt

echo ""
echo "================================"
echo "  Setup complete!"
echo ""
echo "  Element UI:  http://localhost:8080"
echo "  Homeserver:  http://localhost:8008"
echo ""
echo "  Login with:  admin / admin123"
echo ""
echo "  Start agents:"
echo "    python -m agent_runtime.main"
echo ""
echo "  Add agents by dropping .md files"
echo "  into the agents/ directory."
echo "================================"
