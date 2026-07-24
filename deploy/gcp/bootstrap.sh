#!/usr/bin/env bash
# btc-quant collector — one-shot, idempotent VM bootstrap (Debian 12 / systemd).
#
# Lifts the collector that runs on the Mac via launchd (DESIGN §3c) onto a
# always-on Linux VM: a dedicated unprivileged user, a pinned venv, the two
# systemd units + daily timer, and a mandatory post-install smoke test so a bad
# runtime fails HERE, loudly, not silently at 03:00.
#
#   sudo APP_DIR=/opt/btcquant RUN_USER=btcquant bash bootstrap.sh
#
# Re-runnable: every step checks-then-acts, so running it again after `git pull`
# safely re-renders the units and restarts the services.
set -euo pipefail

# ---- config (override via environment) --------------------------------------
APP_DIR="${APP_DIR:-/opt/btcquant}"
RUN_USER="${RUN_USER:-btcquant}"
REPO_URL="${REPO_URL:-https://github.com/azulcoder/btc-quant.git}"
REPO_REF="${REPO_REF:-main}"
TZ_NAME="${TZ_NAME:-Asia/Jakarta}"
# Python strategy: "system" = Debian's python3 (3.11, no external installer — the
# collector uses no 3.12-only syntax); "uv" = pin python 3.12 to match CI exactly.
PYTHON_STRATEGY="${PYTHON_STRATEGY:-system}"
VENV="$APP_DIR/.venv"
UNIT_DIR=/etc/systemd/system
HERE="$APP_DIR/deploy/gcp"

log() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
die() { printf '\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run as root (sudo)."

# ---- 1. base packages + timezone --------------------------------------------
log "apt packages + timezone ($TZ_NAME)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git curl ca-certificates tzdata python3 python3-venv >/dev/null
timedatectl set-timezone "$TZ_NAME" || true

# ---- 2. service user --------------------------------------------------------
if ! id "$RUN_USER" >/dev/null 2>&1; then
  log "create system user $RUN_USER"
  useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$RUN_USER"
fi

# ---- 3. repo (clone or update) ----------------------------------------------
if [ -d "$APP_DIR/.git" ]; then
  log "update repo at $APP_DIR ($REPO_REF)"
  git -C "$APP_DIR" fetch --quiet origin "$REPO_REF"
  git -C "$APP_DIR" checkout --quiet "$REPO_REF"
  git -C "$APP_DIR" reset --hard --quiet "origin/$REPO_REF"
else
  log "clone $REPO_URL -> $APP_DIR"
  mkdir -p "$(dirname "$APP_DIR")"
  git clone --quiet --branch "$REPO_REF" "$REPO_URL" "$APP_DIR"
fi
mkdir -p "$APP_DIR/data/ticks"
# The service only ever writes under data/ (systemd ReadWritePaths). Keep the repo and
# venv root-owned so the unprivileged account cannot rewrite the code it runs next; give
# it ownership of data/ alone.
chown -R "$RUN_USER":"$RUN_USER" "$APP_DIR/data"

# ---- 4. python venv + collector deps ----------------------------------------
if [ "$PYTHON_STRATEGY" = "uv" ]; then
  log "python via uv (pinned 3.12 — CI parity)"
  if ! command -v uv >/dev/null 2>&1; then
    # NOTE: this pipes a vendor installer to sh AS ROOT. Review it first if that
    # is a concern; PYTHON_STRATEGY=system avoids it entirely.
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
  fi
  # Build the venv AS ROOT so it stays root-owned (the service only reads it). The
  # managed interpreter lands in a root-owned, world-readable dir so the unprivileged
  # account can still execute it under the sandbox.
  export UV_PYTHON_INSTALL_DIR="$APP_DIR/.uv-python"
  uv python install 3.12
  uv venv --python 3.12 "$VENV"
  VIRTUAL_ENV="$VENV" uv pip install --quiet -r "$APP_DIR/requirements-collector.txt"
else
  log "python via system python3 ($(python3 --version 2>&1))"
  # Root-owned venv (see chown note above): the service reads/executes it, never writes.
  [ -d "$VENV" ] || python3 -m venv "$VENV"
  "$VENV/bin/pip" install --quiet --upgrade pip
  "$VENV/bin/pip" install --quiet -r "$APP_DIR/requirements-collector.txt"
fi

# ---- 5. env file (secrets) --------------------------------------------------
if [ ! -f "$HERE/env" ]; then
  log "seed env file (FILL IN HF_TOKEN before the sync will work)"
  cp "$HERE/env.example" "$HERE/env"
fi
chown "$RUN_USER":"$RUN_USER" "$HERE/env"
chmod 600 "$HERE/env"

# ---- 6. render + install systemd units --------------------------------------
log "render systemd units -> $UNIT_DIR"
render() {  # $1 = unit filename
  sed -e "s#@APP_DIR@#$APP_DIR#g" \
      -e "s#@RUN_USER@#$RUN_USER#g" \
      -e "s#@VENV@#$VENV#g" \
      "$HERE/$1" > "$UNIT_DIR/$1"
}
render btcquant-collector.service
render btcquant-hfsync.service
render btcquant-hfsync.timer
systemctl daemon-reload

# ---- 7. smoke test BEFORE enabling (fail loud, fail here) --------------------
log "smoke test: imports + collector/uploader --help on the installed venv"
sudo -u "$RUN_USER" "$VENV/bin/python" -c "import duckdb, websockets, huggingface_hub" \
  || die "collector deps failed to import on $($VENV/bin/python --version 2>&1) — try PYTHON_STRATEGY=uv"
sudo -u "$RUN_USER" env HOME="$APP_DIR" "$VENV/bin/python" \
  "$APP_DIR/scripts/run_collector.py" --help >/dev/null \
  || die "run_collector.py --help failed on the installed runtime"
# Also parse the uploader: it imports duckdb + huggingface_hub and builds its own
# argparse, so a broken sync script fails HERE rather than at the first 07:20 timer.
# (--help returns before any Hub call, so this checks import/parse, not the Xet path.)
sudo -u "$RUN_USER" env HOME="$APP_DIR" "$VENV/bin/python" \
  "$APP_DIR/scripts/upload_hf.py" --help >/dev/null \
  || die "upload_hf.py --help failed on the installed runtime"

# ---- 8. enable services -----------------------------------------------------
log "enable + (re)start collector and the daily sync timer"
systemctl enable --now btcquant-collector.service
systemctl enable --now btcquant-hfsync.timer

log "done"
cat <<EOF

  collector : systemctl status btcquant-collector   (live logs: journalctl -u btcquant-collector -f)
  hf timer  : systemctl list-timers btcquant-hfsync
  health    : sudo -u $RUN_USER bash $HERE/status.sh

  NEXT: if you have not yet, put your Hugging Face WRITE token in
        $HERE/env  (HF_TOKEN=...), then:  sudo systemctl start btcquant-hfsync
        to run the first sync now. Until then the collector records fine but the
        daily upload will fail auth (local disk keeps filling — see README §disk).
EOF
