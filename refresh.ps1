<#
  PULSE daily refresh - incremental index + a bounded content pass.
  Registered as the scheduled task "PULSE daily index".
  Safe to run by hand at any time.
#>
$ErrorActionPreference = 'Continue'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$log = Join-Path $env:USERPROFILE '.pulse\refresh.log'
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null

$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:NO_COLOR = '1'

"=== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" | Add-Content $log

# Names first - cheap, and keeps every root current.
foreach ($r in 'home', 'D', 'G', 'H', 'I') {
    try {
        python (Join-Path $here 'pulse.py') index --root $r --max-seconds 420 --wait 1200 2>&1 |
            Add-Content $log
    } catch {
        "index $r FAILED: $($_.Exception.Message)" | Add-Content $log
    }
}

# Then extract text from documents that are new or changed, time-boxed so
# this never runs long enough to be noticed.
try {
    python (Join-Path $here 'pulse.py') content `
        --ext pdf,xlsx,xlsm,docx,pptx,csv,md,txt --max-seconds 900 --wait 1200 2>&1 |
        Add-Content $log
} catch {
    "content FAILED: $($_.Exception.Message)" | Add-Content $log
}

# Keep the log from growing without bound.
try {
    $lines = Get-Content $log -ErrorAction Stop
    if ($lines.Count -gt 4000) {
        $lines | Select-Object -Last 2000 | Set-Content $log -Encoding utf8
    }
} catch { }
