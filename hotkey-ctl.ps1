<#
  Start / stop / restart / check the PULSE global hotkey listener.

    .\hotkey-ctl.ps1 start
    .\hotkey-ctl.ps1 stop
    .\hotkey-ctl.ps1 restart      <- run this after changing the key
    .\hotkey-ctl.ps1 status
    .\hotkey-ctl.ps1 set -Mods alt -Key F
    .\hotkey-ctl.ps1 install      <- also run it at every sign-in
#>
param(
    [Parameter(Position = 0)]
    [ValidateSet('start', 'stop', 'restart', 'status', 'set', 'install', 'uninstall')]
    [string]$Action = 'status',
    [string[]]$Mods,
    [string]$Key
)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Join-Path $here 'hotkey.pyw'
$cfg = Join-Path $env:USERPROFILE '.pulse\hotkey.json'
$log = Join-Path $env:USERPROFILE '.pulse\hotkey.log'
$startup = [IO.Path]::Combine($env:APPDATA,
    'Microsoft\Windows\Start Menu\Programs\Startup', 'PULSE Hotkey.lnk')

function Get-Listener {
    Get-CimInstance Win32_Process |
        Where-Object { $_.CommandLine -like '*hotkey.pyw*' }
}

function Stop-Listener {
    $p = Get-Listener
    foreach ($x in $p) { Stop-Process -Id $x.ProcessId -Force -ErrorAction SilentlyContinue }
    if ($p) { Start-Sleep -Milliseconds 600; "  stopped $($p.Count) listener(s)" }
    else { '  nothing was running' }
}

function Start-Listener {
    $pyw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
    if (-not $pyw) { $pyw = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\pythonw.exe' }
    Start-Process $pyw -ArgumentList ('"' + $script + '"') -WorkingDirectory $here
    Start-Sleep -Seconds 2
    if (Get-Listener) { '  started' } else { '  FAILED to start - see ' + $log }
}

function Show-Status {
    $p = Get-Listener
    if (Test-Path $cfg) {
        $c = Get-Content $cfg -Raw | ConvertFrom-Json
        $combo = (($c.mods + $c.key) -join '+').ToUpper()
    } else { $combo = 'ALT+F (default)' }
    Write-Host ''
    Write-Host "  hotkey   : $combo" -ForegroundColor Cyan
    if ($p) { Write-Host "  listener : running (pid $($p.ProcessId -join ', '))" -ForegroundColor Green }
    else { Write-Host '  listener : NOT running' -ForegroundColor Yellow }
    $atLogon = Test-Path $startup
    Write-Host "  at sign-in: $(if ($atLogon) { 'yes' } else { 'no' })"
    if (Test-Path $log) {
        Write-Host ''
        Write-Host '  recent log:' -ForegroundColor DarkGray
        Get-Content $log -Tail 4 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
    }
    Write-Host ''
}

switch ($Action) {
    'stop' { Stop-Listener }
    'start' { Start-Listener; Show-Status }
    'restart' { Stop-Listener; Start-Listener; Show-Status }
    'status' { Show-Status }
    'set' {
        if (-not $Mods -or -not $Key) { throw 'usage: .\hotkey-ctl.ps1 set -Mods alt,ctrl -Key P' }
        New-Item -ItemType Directory -Force -Path (Split-Path $cfg) | Out-Null
        @{ mods = @($Mods); key = $Key } | ConvertTo-Json | Set-Content $cfg -Encoding utf8
        "  saved $cfg"
        Stop-Listener; Start-Listener; Show-Status
    }
    'install' {
        $pyw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
        if (-not $pyw) { $pyw = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\pythonw.exe' }
        $sh = New-Object -ComObject WScript.Shell
        $sc = $sh.CreateShortcut($startup)
        $sc.TargetPath = $pyw
        $sc.Arguments = '"' + $script + '"'
        $sc.WorkingDirectory = $here
        $sc.Description = 'PULSE global hotkey'
        $sc.WindowStyle = 7
        $sc.Save()
        "  will start at sign-in: $startup"
        Stop-Listener; Start-Listener; Show-Status
    }
    'uninstall' {
        Remove-Item $startup -Force -ErrorAction SilentlyContinue
        '  removed from sign-in'
        Stop-Listener
    }
}
