#!/bin/sh
# Install and enable the hardened Loco systemd units:
#   loco.service            the router/data plane
#   loco-flywheel.service   daily maintenance (curate, split, report)
#   loco-flywheel.timer     daily schedule for the flywheel
# Preview by default. Apply only with explicit --apply and sudo.
set -eu

MODE=preview
for arg in "$@"; do
  case "$arg" in
    --apply|--system) MODE=apply ;;
    -h|--help)
      echo "Usage: sh scripts/install-loco-unit.sh [--apply]"
      echo "       default: print resulting units and checks without writing anything"
      exit 0
      ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

DEPLOY=/home/jahanzaib/Desktop/Loco/deploy
UNITS='loco.service loco-flywheel.service loco-flywheel.timer'
TARGET_DIR=/etc/systemd/system

echo "Loco unit sources: $DEPLOY"
echo
for unit in $UNITS; do
  echo "===== $unit ====="
  cat "$DEPLOY/$unit"
  echo
done

if [ "$MODE" = preview ]; then
  cat <<EOF
Preview only. To install:
  sudo cp $DEPLOY/loco.service $DEPLOY/loco-flywheel.service \\
         $DEPLOY/loco-flywheel.timer $TARGET_DIR/
  sudo systemctl daemon-reload
  sudo systemctl enable --now loco.service
  sudo systemctl enable --now loco-flywheel.timer
  systemctl status loco.service loco-flywheel.timer --no-pager
EOF
  exit 0
fi

if [ "$(id -u)" -ne 0 ]; then
  echo 'refusing: --apply requires root/sudo' >&2
  exit 3
fi
for unit in $UNITS; do
  cp "$DEPLOY/$unit" "$TARGET_DIR/$unit"
  chmod 0644 "$TARGET_DIR/$unit"
done
systemctl daemon-reload
systemctl enable --now loco.service
systemctl enable --now loco-flywheel.timer
sleep 2
systemctl status loco.service --no-pager || true
systemctl list-timers loco-flywheel.timer --no-pager || true
