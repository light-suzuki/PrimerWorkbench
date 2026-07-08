[CmdletBinding()]
param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$runtime = Join-Path $root '.runtime'
$frontend = Join-Path $root 'frontend\workbench'
if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) { throw 'WSL2 with Ubuntu is required.' }
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { throw 'Node.js 20 or newer is required.' }
$windowsRootForWsl = $root.Replace('\', '/')
$wslRoot = (& wsl.exe wslpath -a -u $windowsRootForWsl).Trim()
if ($LASTEXITCODE -ne 0 -or -not $wslRoot) { throw 'Could not convert the repository path for WSL.' }
$backendUrl = 'http://127.0.0.1:8000'
$wslIp = ((& wsl.exe hostname -I).Trim() -split '\s+')[0]
if (-not $wslIp) { throw 'Could not determine the WSL address.' }
New-Item -ItemType Directory -Force -Path $runtime | Out-Null
if (Test-Path (Join-Path $frontend 'package-lock.json')) { & npm ci --prefix $frontend } else { & npm install --prefix $frontend }
if ($LASTEXITCODE) { throw 'Node dependency installation failed.' }
$backLog = Join-Path $runtime 'backend.log'
$frontLog = Join-Path $runtime 'frontend.log'
$backCommand = "cd '$wslRoot' && exec ./start_backend_wsl.sh"
$backCommandArg = '"' + $backCommand.Replace('"', '\"') + '"'
$back = Start-Process -FilePath 'wsl.exe' -ArgumentList 'bash','-lc',$backCommandArg -RedirectStandardOutput $backLog -RedirectStandardError (Join-Path $runtime 'backend.error.log') -WindowStyle Hidden -PassThru
$proxyScript = '"' + (Join-Path $root 'tools\wsl_localhost_proxy.js') + '"'
$proxy = Start-Process -FilePath 'node.exe' -ArgumentList $proxyScript,'8000',$wslIp,'8000' -RedirectStandardOutput (Join-Path $runtime 'proxy.log') -RedirectStandardError (Join-Path $runtime 'proxy.error.log') -WindowStyle Hidden -PassThru
$previousBioApiUrl = $env:VITE_BIOAPI_BASE_URL
$env:VITE_BIOAPI_BASE_URL = $backendUrl
$front = Start-Process -FilePath 'npm.cmd' -ArgumentList 'run','dev','--','--host','127.0.0.1','--port','5173','--strictPort' -WorkingDirectory $frontend -RedirectStandardOutput $frontLog -RedirectStandardError (Join-Path $runtime 'frontend.error.log') -WindowStyle Hidden -PassThru
if ($null -eq $previousBioApiUrl) { Remove-Item Env:VITE_BIOAPI_BASE_URL } else { $env:VITE_BIOAPI_BASE_URL = $previousBioApiUrl }
$ready = $false
for ($i = 0; $i -lt 240; $i++) {
  try { $null = Invoke-RestMethod "$backendUrl/health"; $ready = $true; break } catch { Start-Sleep -Milliseconds 500 }
}
if (-not $ready) { Stop-Process -Id $front.Id,$proxy.Id,$back.Id -Force -ErrorAction SilentlyContinue; throw "WSL backend failed to start. See $runtime" }
$frontendPid = (Get-NetTCPConnection -State Listen -LocalPort 5173 -ErrorAction Stop | Select-Object -First 1).OwningProcess
@{ wsl_launcher = $back.Id; proxy = $proxy.Id; frontend = $frontendPid; wsl_root = $wslRoot; backend_url = $backendUrl } | ConvertTo-Json | Set-Content (Join-Path $runtime 'pids.json')
Write-Host 'Sequence Workbench is running at http://127.0.0.1:5173/'
Write-Host "Backend: WSL Ubuntu (BLAST+ and Primer3) on $backendUrl/"
if (-not $NoBrowser) { Start-Process 'http://127.0.0.1:5173/' }
