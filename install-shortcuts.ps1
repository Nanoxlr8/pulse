<#
  Creates the PULSE shortcuts:
    - Desktop            : double-click to open
    - Start Menu         : press Win, type "pulse"

  The global hotkey (Alt+F) is NOT set here. Windows shortcut hotkeys only
  reliably honour Ctrl+Alt / Ctrl+Shift combinations, so a bare Alt+F would be
  saved and silently ignored. It is handled by hotkey.pyw instead - manage it
  with hotkey-ctl.ps1.

  Re-run this any time to repair the shortcuts.
#>
param([string]$Hotkey = '')

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$app = Join-Path $here 'app.pyw'

if (-not (Test-Path $app)) { throw "app.pyw not found next to this script" }

# pythonw.exe runs it with no console window
$pyw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $pyw) {
    $guess = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\pythonw.exe'
    if (Test-Path $guess) { $pyw = $guess } else { throw "pythonw.exe not found" }
}

$targets = @(
    [IO.Path]::Combine([Environment]::GetFolderPath('Desktop'), 'PULSE.lnk'),
    [IO.Path]::Combine($env:APPDATA, 'Microsoft\Windows\Start Menu\Programs\PULSE.lnk')
)

$sh = New-Object -ComObject WScript.Shell
foreach ($t in $targets) {
    New-Item -ItemType Directory -Force -Path (Split-Path $t) | Out-Null
    $sc = $sh.CreateShortcut($t)
    $sc.TargetPath = $pyw
    $sc.Arguments = '"' + $app + '"'
    $sc.WorkingDirectory = $here
    $sc.Description = 'Search every file on this PC, and keep it fast'
    $sc.IconLocation = "$env:SystemRoot\System32\imageres.dll,3"
    $sc.WindowStyle = 1
    # Always assign, even when empty: CreateShortcut on an existing .lnk
    # loads its current properties, so skipping this would silently keep a
    # hotkey set by an earlier run.
    $sc.Hotkey = $Hotkey
    $sc.Save()
    Write-Host "  created  $t" -ForegroundColor Green
}

Write-Host ""
Write-Host "  PULSE is now available:" -ForegroundColor White
Write-Host "    - double-click PULSE on your Desktop"
Write-Host "    - press Win, type 'pulse'"
Write-Host "    - press Alt+F from anywhere (via hotkey-ctl.ps1)" -ForegroundColor Cyan
Write-Host ""
Write-Host "  To pin it to the taskbar: right-click the Desktop icon >" -ForegroundColor DarkGray
Write-Host "  Show more options > Pin to taskbar" -ForegroundColor DarkGray
Write-Host ""
