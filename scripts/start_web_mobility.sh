#!/usr/bin/env bash
# Linux/macOS counterpart of start_web_mobility.ps1: serves the 3D viewer and the
# /planner page from the repository's own datasets/ folder (run `git lfs pull` first).
# Usage: scripts/start_web_mobility.sh [port]   (default 8012)
set -euo pipefail
REPO=$(cd "$(dirname "$0")/.." && pwd)
D=$REPO/datasets
PORT=${1:-8012}
PY=$REPO/.venv/bin/python
export PYTHONUTF8=1
export DYNACITY_DATA_ROOT=${DYNACITY_DATA_ROOT:-$HOME/dynacity-data}
export DYNACITY_ARTIFACT_ROOT=${DYNACITY_ARTIFACT_ROOT:-$HOME/dynacity-artifacts}
export SUMO_HOME=${SUMO_HOME:-$("$PY" -c 'import sumo; print(sumo.SUMO_HOME)')}
export PATH=$REPO/.venv/bin:$SUMO_HOME/bin:$PATH
mkdir -p "$DYNACITY_ARTIFACT_ROOT"

# An LFS file that was never pulled is a ~130-byte text pointer: refuse to serve it.
for f in "$D"/beirut001/beirut001-pack.npz "$D"/terrain/terrain-glo30.npz; do
  if head -c 64 "$f" | grep -q 'git-lfs.github.com/spec'; then
    echo "$f is a Git LFS pointer, not data. Run: git lfs pull" >&2; exit 1
  fi
done

# The trained model is not in git; train it once from the shipped panel.
MODEL=$DYNACITY_ARTIFACT_ROOT/forecast.joblib
if [ ! -f "$MODEL" ]; then
  "$PY" -m dynacity.cli train --panel "$D/buildings/bbed-transition-panel.csv" --output "$MODEL"
fi

# The OSM landscape extract is not in git (see datasets/README.md). Context buildings
# need it, so both are served only when a local copy exists.
EXTRA=()
LANDSCAPE=${DYNACITY_LANDSCAPE:-$DYNACITY_DATA_ROOT/traffic/osm-beirut-context.json}
if [ -f "$LANDSCAPE" ]; then
  EXTRA+=(--landscape "$LANDSCAPE" --context-buildings "$D/buildings/context-buildings-web.json")
else
  echo "No OSM landscape at $LANDSCAPE: serving without landscape and context buildings." >&2
fi

cd "$REPO"
exec "$PY" -m dynacity.cli serve --model "$MODEL" "${EXTRA[@]}" \
  --snapshot "$D/buildings/snapshot-2024-located.json" \
  --bbed "$D/buildings/bbed-2024.geojson" \
  --forecast "$D/forecast/forecast-2024-2030.json" \
  --traffic "$D/traffic/beirut-traffic-20261024.json" \
  --terrain "$D/terrain/terrain-glo30.npz" \
  --beirut001 "$D/beirut001/beirut001-pack.npz" \
  --pedestrians "$D/pedestrians/pedestrian-walkers.json" \
  --prewarm-traffic --host 127.0.0.1 --port "$PORT"
