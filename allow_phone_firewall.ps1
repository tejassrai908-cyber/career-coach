# Allows phones on your own Wi-Fi to reach Career Coach on port 5055.
# Run once, as Administrator.
$name = 'Career Coach (port 5055)'

Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue | Remove-NetFirewallRule

New-NetFirewallRule -DisplayName $name `
    -Direction Inbound -Action Allow -Protocol TCP -LocalPort 5055 `
    -Profile Private -Description 'Career Coach local web app, private networks only' | Out-Null

Write-Output 'Rule created:'
Get-NetFirewallRule -DisplayName $name |
    Select-Object DisplayName, Enabled, Direction, Action, Profile |
    Format-List
