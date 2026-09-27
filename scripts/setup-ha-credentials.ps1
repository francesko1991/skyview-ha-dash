# Run this YOURSELF in your own PowerShell terminal (not through Claude).
# Prompts for the Home Assistant URL and a long-lived access token, then stores them
# OUTSIDE the project workspace, with the token encrypted by Windows DPAPI
# (only your Windows user on this machine can decrypt it). Nothing is echoed.

$dir  = Join-Path $env:USERPROFILE '.ha-dashboard'
$file = Join-Path $dir 'ha-credential.xml'

$url = Read-Host 'Home Assistant base URL (e.g. http://homeassistant.local:8123)'
$url = $url.Trim().TrimEnd('/')
if ($url -match '^eyJ' -or $url.Length -gt 120) {
    Write-Host 'That looks like the token, not the URL. Nothing saved - run the script again and enter the address first.' -ForegroundColor Yellow
    exit 1
}
if ($url -notmatch '^[a-zA-Z][a-zA-Z0-9+.-]*://') { $url = "http://$url" }
if ($url -notmatch '^https?://[^/\s]+') {
    Write-Host 'URL must look like http://homeassistant.local:8123 or http://192.168.x.x:8123. Nothing saved.' -ForegroundColor Yellow
    exit 1
}

$token = Read-Host 'Long-lived access token (input hidden)' -AsSecureString
if ($token.Length -lt 20) { Write-Error 'Token looks too short; nothing saved.'; exit 1 }

New-Item -ItemType Directory -Force -Path $dir | Out-Null
# PSCredential.Password is DPAPI-encrypted by Export-Clixml; the URL is stored as the user name.
New-Object System.Management.Automation.PSCredential($url, $token) | Export-Clixml -Path $file

# Restrict the file to the current user only.
icacls $file /inheritance:r /grant:r "$($env:USERNAME):(R,W)" | Out-Null

Write-Host "Saved encrypted credential to $file"
