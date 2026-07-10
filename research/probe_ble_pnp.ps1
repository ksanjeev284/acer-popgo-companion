$devs = Get-PnpDevice -PresentOnly | Where-Object {
    $_.InstanceId -match '32C2_PID&0026|F900C6021401|0000180F'
}
foreach ($d in $devs) {
    Write-Host "DEVICE $($d.FriendlyName)"
    Write-Host "ID $($d.InstanceId)"
    Get-PnpDeviceProperty -InstanceId $d.InstanceId -ErrorAction SilentlyContinue |
        Where-Object { $_.KeyName -match 'Battery|Power|Energy|Charge|Level|Device_DeviceDesc|Bluetooth' } |
        Select-Object KeyName, Type, Data |
        Format-List
    Write-Host "---"
}

# Also try battery class under BTHLE
Get-CimInstance -Namespace root\wmi -ClassName BatteryStatus -ErrorAction SilentlyContinue |
    Format-List *
Get-CimInstance Win32_Battery -ErrorAction SilentlyContinue | Format-List *
