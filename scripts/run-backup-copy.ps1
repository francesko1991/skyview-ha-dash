# Copies the newest Home Assistant backups to ha-backups\ (keeps 2; older copies go to the Recycle Bin).
# Dry run by default; add --apply to download. Credential stays in process-scoped env vars only.
param([Parameter(ValueFromRemainingArguments = $true)] $PyArgs)

$file = Join-Path $env:USERPROFILE '.ha-dashboard\ha-credential.xml'
if (-not (Test-Path $file)) { Write-Error "No credential found. Run scripts\setup-ha-credentials.ps1 first."; exit 1 }

$cred = Import-Clixml -Path $file
$env:HA_URL   = $cred.UserName
$env:HA_TOKEN = $cred.GetNetworkCredential().Password
$env:PYTHONIOENCODING = 'utf-8'
try {
    python (Join-Path $PSScriptRoot 'ha_backup_copy.py') @PyArgs
} finally {
    Remove-Item Env:HA_TOKEN, Env:HA_URL -ErrorAction SilentlyContinue
}
