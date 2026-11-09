# Starts the bot at Windows logon (shortcut in the Startup folder). Does nothing if it already runs.
$root = $PSScriptRoot
$running = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like '*-m leadly*' -and $_.CommandLine -notlike '*devtools*' }
if ($running) { exit 0 }
Start-Sleep -Seconds 20  # let the network come up after logon
$env:PYTHONUTF8 = '1'
Start-Process -FilePath (Join-Path $root '.venv\Scripts\python.exe') -ArgumentList '-m', 'leadly' `
    -WorkingDirectory $root -WindowStyle Minimized
