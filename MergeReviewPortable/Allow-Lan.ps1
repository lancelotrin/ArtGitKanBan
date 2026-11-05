#Requires -RunAsAdministrator
param([int]$Port = 8767)
$ErrorActionPreference = 'Stop'
if ($Port -lt 1 -or $Port -gt 65535) { throw 'Invalid TCP port.' }
$reviewRuleName = "MergeReviewPortable-$Port-LAN"
if (-not (Get-NetFirewallRule -Name $reviewRuleName -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -Name $reviewRuleName -DisplayName "Merge Review Portable (LAN $Port)" -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port -RemoteAddress LocalSubnet -Profile Any | Out-Null
}
Get-NetFirewallRule -Name $reviewRuleName | Select-Object Name,Enabled,Direction,Action
