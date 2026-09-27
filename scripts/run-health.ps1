# On-demand, read-only health check. Loads the stored credential into process-scoped environment
# variables only (never printed or written), runs ha_health.py, then removes them.
#   scripts\run-health.ps1                 full report -> maintenance\reports\
#   scripts\run-health.ps1 --only backups  one area (system, backups, updates, integrations, devices, logs, dashboards)
#   scripts\run-health.ps1 --no-save       print only
param([Parameter(ValueFromRemainingArguments = $true)] $PyArgs)

$file = Join-Path $env:USERPROFILE '.ha-dashboard\ha-credential.xml'
if (-not (Test-Path $file)) { Write-Error "No credential found. Run scripts\setup-ha-credentials.ps1 first."; exit 1 }

$cred = Import-Clixml -Path $file
$env:HA_URL   = $cred.UserName
$env:HA_TOKEN = $cred.GetNetworkCredential().Password
$env:PYTHONIOENCODING = 'utf-8'
try {
    python (Join-Path $PSScriptRoot 'ha_health.py') @PyArgs
} finally {
    Remove-Item Env:HA_TOKEN, Env:HA_URL -ErrorAction SilentlyContinue
}
