# Runs ha_apply.py with the stored credential (process-scoped env vars, never printed).
# Dry run by default; changes Home Assistant only with --apply.
param([Parameter(ValueFromRemainingArguments = $true)] $PyArgs)

$file = Join-Path $env:USERPROFILE '.ha-dashboard\ha-credential.xml'
if (-not (Test-Path $file)) { Write-Error "No credential found. Run scripts\setup-ha-credentials.ps1 first."; exit 1 }

$cred = Import-Clixml -Path $file
$env:HA_URL   = $cred.UserName
$env:HA_TOKEN = $cred.GetNetworkCredential().Password
try {
    python (Join-Path $PSScriptRoot 'ha_apply.py') @PyArgs
} finally {
    Remove-Item Env:HA_TOKEN, Env:HA_URL -ErrorAction SilentlyContinue
}
