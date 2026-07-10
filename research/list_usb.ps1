Get-PnpDevice -PresentOnly | Where-Object {
    $_.InstanceId -match 'VID_|USB\\|BTHENUM|BATTERY' -and
    $_.Status -eq 'OK'
} | Select-Object Class, FriendlyName, InstanceId |
    Format-Table -AutoSize -Wrap
