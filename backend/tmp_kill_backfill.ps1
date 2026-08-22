Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like '*backfill*' } |
    ForEach-Object {
        Write-Host ("kill " + $_.ProcessId)
        Stop-Process -Id $_.ProcessId -Force
    }
