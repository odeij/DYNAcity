param([int]$Port = 8012, [string]$DataRoot = 'C:/private/dynacity-data')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONUTF8 = '1'
$env:PYTHONPATH = Join-Path $projectRoot 'src'
$env:SUMO_HOME = 'C:/private/dynacity-tools/sumo-1.22.0'
$env:PATH = "$env:SUMO_HOME/bin;$env:PATH"
$viewerArgs = @('-X', 'utf8', '-m', 'dynacity.cli', 'serve',
  '--model', "$DataRoot/forecast-2018-2024.joblib",
  '--snapshot', "$DataRoot/snapshot-2024-located.json",
  '--bbed', "$DataRoot/bbed-2024.geojson",
  '--forecast', "$DataRoot/forecast-2024-2030.json",
  '--traffic', "$DataRoot/traffic/beirut-traffic-20261024.json",
  '--beirut001', "$DataRoot/beirut001/beirut001-pack.npz",
  '--landscape', "$DataRoot/traffic/osm-beirut-context-infrastructure-20261027.json",
  '--context-buildings', "$DataRoot/overture/context-buildings-web.json",
  '--pedestrians', "$DataRoot/pedestrian-una/pedestrian-walkers.json",
  '--host', '127.0.0.1', '--port', "$Port")
$overlayFiles = @(
  'traffic/greenery-refresh/osm-greenery-infrastructure-public-space-display-20261027.json',
  'engine/utilities-waterfront/osm-utilities-waterfront-20261027.json',
  'engine/street-lamp-inventory-20261031/osm-lighting-raw.json')
foreach ($relative in $overlayFiles) {
  $overlayPath = Join-Path $DataRoot $relative
  if (Test-Path -LiteralPath $overlayPath) { $viewerArgs += @('--context-overlay', $overlayPath) }
}
& (Join-Path $projectRoot '.venv/Scripts/python.exe') @viewerArgs
