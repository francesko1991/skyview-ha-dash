# Loads the DPAPI-encrypted credential and passes it to the Python tool through
# process-scoped environment variables only. The token is never printed or written to disk.
param([Parameter(ValueFromRemainingArguments = $true)] $PyArgs)

$file = Join-Path $env:USERPROFILE '.ha-dashboard\ha-credential.xml'
if (-not (Test-Path $file)) { Write-Error "No credential found. Run scripts\setup-ha-credentials.ps1 first."; exit 1 }

$cred = Import-Clixml -Path $file
$env:HA_URL   = $cred.UserName
$env:HA_TOKEN = $cred.GetNetworkCredential().Password
try {
    python (Join-Path $PSScriptRoot 'ha_inventory.py') @PyArgs
} finally {
    Remove-Item Env:HA_TOKEN, Env:HA_URL -ErrorAction SilentlyContinue
}
