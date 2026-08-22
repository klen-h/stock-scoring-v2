Get-CimInstance Win32_Process -Filter "ProcessId=10816" | Format-List ProcessId, CommandLine, CreationDate
