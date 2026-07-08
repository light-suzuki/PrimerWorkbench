$pidFile = Join-Path $PSScriptRoot '.runtime\pids.json'
if (-not (Test-Path $pidFile)) { Write-Host 'Sequence Workbench is not running.'; exit 0 }
$saved = Get-Content $pidFile -Raw | ConvertFrom-Json
foreach ($processId in @($saved.frontend, $saved.proxy, $saved.wsl_launcher)) {
  if ($processId -and (Get-Process -Id $processId -ErrorAction SilentlyContinue)) { Stop-Process -Id $processId -Force }
}
Remove-Item $pidFile -Force
Write-Host 'Sequence Workbench stopped.'
