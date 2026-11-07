#!/bin/sh
# Start the Loco embedding sidecar (if not running) then Loco itself.
P="/home/jahanzaib/Desktop/Loco"
cd "$P"
if ! curl -s -m 2 http://127.0.0.1:8210/health >/dev/null 2>&1; then
  nohup "$P/scripts/embed-server.sh" >"$P/logs/embed.log" 2>&1 &
  echo "embed sidecar pid $!"
  sleep 6
fi
exec python3 -m loco
