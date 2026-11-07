#!/bin/sh
# One-time: teach the router service about /home/jahanzaib/models/presets.ini
# Run with:  sh /home/jahanzaib/Desktop/Loco/scripts/install-preset-unit.sh
set -e
DIR=/etc/systemd/system/llama-server.service.d
sudo mkdir -p "$DIR"
sudo tee "$DIR/loco.conf" >/dev/null <<'EOF'
[Service]
ExecStart=
ExecStart=/usr/local/bin/llama-server --models-dir /home/jahanzaib/models --models-preset /home/jahanzaib/models/presets.ini --host 127.0.0.1 --port 8080 -ngl 99
EOF
sudo systemctl daemon-reload
sudo systemctl restart llama-server
sleep 3
curl -s http://127.0.0.1:8080/v1/models | head -c 300
