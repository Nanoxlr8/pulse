<#
  PULSE TUNE - measure, then apply reversible performance fixes to this PC.

    .\tune.ps1 report          # read-only: what is wrong and what I would change
    .\tune.ps1 apply           # apply fixes (asks before each group)
    .\tune.ps1 apply -Yes      # apply without prompting
    .\tune.ps1 apply -Only power,defender
    .\tune.ps1 restore         # undo everything from the latest backup
    .\tune.ps1 restore -Backup <path>

  Every change is recorded in a JSON backup under ~\.pulse\backups\ before it
  is made. Nothing is deleted; no files are removed from the system.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('report', 'apply', 'restore')]
    [string]$Action = 'report',

    [switch]$Yes,
    [string[]]$Only,
    [string]$Backup
)

$ErrorActionPreference = 'Stop'
$PulseDir = Join-Path $env:USERPROFILE '.pulse'
$BackupDir = Join-Path $PulseDir 'backups'
New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null

# ---------------------------------------------------------------- output

function Say([string]$t, [string]$c = 'Gray') { Write-Host $t -ForegroundColor $c }
function Head([string]$t) {
    Write-Host ''
    Write-Host "  $t" -ForegroundColor White
    Write-Host ("  " + ("-" * 64)) -ForegroundColor DarkGray
}
function Item([string]$state, [string]$text, [string]$detail = '') {
    $map = @{ 'FAIL' = 'Red'; 'WARN' = 'Yellow'; 'OK' = 'Green'; 'FIX' = 'Cyan'; 'SKIP' = 'DarkGray' }
    $col = $map[$state]; if (-not $col) { $col = 'Gray' }
    Write-Host ("    [{0,-4}] " -f $state) -ForegroundColor $col -NoNewline
    Write-Host $text
    if ($detail) { Write-Host "           $detail" -ForegroundColor DarkGray }
}

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal $id).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

$IsAdmin = Test-Admin

# ---------------------------------------------------------------- state

$State = [ordered]@{}

function Read-State {
    $s = [ordered]@{}

    $s.powerPlan = (powercfg /getactivescheme) -replace '^.*GUID:\s*([0-9a-f-]+).*$', '$1'
    $s.powerPlanName = (powercfg /getactivescheme) -replace '^.*\(([^)]+)\)\s*$', '$1'

    $os = Get-CimInstance Win32_OperatingSystem
    $cs = Get-CimInstance Win32_ComputerSystem
    $s.ramTotalGB = [math]::Round($cs.TotalPhysicalMemory / 1GB, 1)
    $s.ramFreeGB = [math]::Round($os.FreePhysicalMemory / 1MB, 1)

    $s.startup = @(Get-CimInstance Win32_StartupCommand |
        Select-Object Name, Command, Location, User)

    # Visual effects
    try {
        $vfx = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects' -ErrorAction Stop
        $s.visualFx = $vfx.VisualFXSetting
    } catch { $s.visualFx = $null }

    # Storage Sense
    try {
        $ss = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\StorageSense\Parameters\StoragePolicy' -ErrorAction Stop
        $s.storageSense = $ss.'01'
    } catch { $s.storageSense = $null }

    # Defender exclusions (admin only)
    if ($IsAdmin) {
        try { $s.defenderExclusions = @((Get-MpPreference).ExclusionPath) }
        catch { $s.defenderExclusions = @() }
    } else { $s.defenderExclusions = $null }

    # SysMain / Windows Search services
    foreach ($svc in 'SysMain', 'WSearch') {
        try {
            $o = Get-Service -Name $svc -ErrorAction Stop
            $s["svc_$svc"] = @{ Status = "$($o.Status)"; StartType = "$($o.StartType)" }
        } catch { $s["svc_$svc"] = $null }
    }

    # NVMe TRIM
    try { $s.trimDisabled = (fsutil behavior query DisableDeleteNotify) -join ' ' }
    catch { $s.trimDisabled = $null }

    # Python UTF-8 (user env)
    $s.pythonUtf8 = [Environment]::GetEnvironmentVariable('PYTHONUTF8', 'User')
    $s.pythonIoEnc = [Environment]::GetEnvironmentVariable('PYTHONIOENCODING', 'User')

    # Disks
    $s.disks = @(Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' |
        ForEach-Object {
            [pscustomobject]@{
                Drive = $_.DeviceID
                TotalGB = [math]::Round($_.Size / 1GB, 1)
                FreeGB = [math]::Round($_.FreeSpace / 1GB, 1)
                PctUsed = if ($_.Size) { [math]::Round(100 * ($_.Size - $_.FreeSpace) / $_.Size, 0) } else { 0 }
            }
        })

    return $s
}

# ---------------------------------------------------------------- fixes
# Each fix: Id, Group, Title, Why, NeedsAdmin, Test (returns $true if ALREADY good),
#           Apply (scriptblock), Undo (scriptblock built from backup)

$ULTIMATE_GUID = 'e9a42b02-d5df-448d-aa00-03f14749eb61'
$HIGHPERF_GUID = '8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c'

# Directories worth excluding from real-time scanning: dev + accounting work
# dirs that are constantly read/written. These are the user's own working
# folders, not system or download paths.
$ExclusionTargets = @(
    (Join-Path $env:USERPROFILE '.pulse'),
    (Join-Path $env:USERPROFILE '.gemini\tools'),
    (Join-Path $env:USERPROFILE 'Apps\pulse'),
    (Join-Path $env:USERPROFILE 'AppData\Local\Programs\Python\Python312')
)

function Get-Fixes {
    param($s)
    $f = @()

    $f += [pscustomobject]@{
        Id = 'power'; Group = 'power'
        Title = 'Switch power plan to Ultimate Performance'
        Why = "Currently '$($s.powerPlanName)'. On a 13th-gen H-series CPU, Balanced parks cores and caps sustained clocks."
        NeedsAdmin = $false
        IsGood = ($s.powerPlan -eq $ULTIMATE_GUID -or $s.powerPlan -eq $HIGHPERF_GUID)
        Apply = {
            $existing = powercfg /list | Select-String $ULTIMATE_GUID
            if (-not $existing) { powercfg -duplicatescheme $ULTIMATE_GUID | Out-Null }
            powercfg /setactive $ULTIMATE_GUID
            if ($LASTEXITCODE -ne 0) { powercfg /setactive $HIGHPERF_GUID }
        }
    }

    $f += [pscustomobject]@{
        Id = 'utf8'; Group = 'dev'
        Title = 'Set PYTHONUTF8=1 and PYTHONIOENCODING=utf-8 (user-wide)'
        Why = 'Python here defaults to cp1252, so any script that prints an Arabic filename crashes with UnicodeEncodeError.'
        NeedsAdmin = $false
        IsGood = ($s.pythonUtf8 -eq '1' -and $s.pythonIoEnc -eq 'utf-8')
        Apply = {
            [Environment]::SetEnvironmentVariable('PYTHONUTF8', '1', 'User')
            [Environment]::SetEnvironmentVariable('PYTHONIOENCODING', 'utf-8', 'User')
        }
    }

    $f += [pscustomobject]@{
        Id = 'visualfx'; Group = 'ui'
        Title = 'Trim window animations (keep font smoothing + thumbnails)'
        Why = 'Removes the 150-250ms animation on every window open, minimise and alt-tab.'
        NeedsAdmin = $false
        IsGood = ($s.visualFx -eq 3)
        Apply = {
            $k = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects'
            New-Item -Path $k -Force | Out-Null
            Set-ItemProperty $k 'VisualFXSetting' 3 -Type DWord
            Set-ItemProperty 'HKCU:\Control Panel\Desktop\WindowMetrics' 'MinAnimate' '0' -Type String
            Set-ItemProperty 'HKCU:\Control Panel\Desktop' 'MenuShowDelay' '0' -Type String
            $k2 = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced'
            Set-ItemProperty $k2 'ListviewAlphaSelect' 1 -Type DWord
            Set-ItemProperty $k2 'TaskbarAnimations' 0 -Type DWord
        }
    }

    $f += [pscustomobject]@{
        Id = 'storagesense'; Group = 'disk'
        Title = 'Turn on Storage Sense (auto-clear temp files)'
        Why = 'Keeps the system drive from creeping toward full on its own.'
        NeedsAdmin = $false
        IsGood = ($s.storageSense -eq 1)
        Apply = {
            $k = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\StorageSense\Parameters\StoragePolicy'
            New-Item -Path $k -Force | Out-Null
            Set-ItemProperty $k '01' 1 -Type DWord      # enabled
            Set-ItemProperty $k '2048' 30 -Type DWord   # run every 30 days
            Set-ItemProperty $k '08' 1 -Type DWord      # clean temp files
            Set-ItemProperty $k '32' 30 -Type DWord     # recycle bin 30 days
        }
    }

    $f += [pscustomobject]@{
        Id = 'defender'; Group = 'defender'
        Title = 'Exclude working directories from real-time scanning'
        Why = 'Defender re-scans these on every read/write. Excluding them cuts index and script time noticeably.'
        NeedsAdmin = $true
        IsGood = ($null -ne $s.defenderExclusions -and
                  (@($ExclusionTargets | Where-Object { $s.defenderExclusions -notcontains $_ }).Count -eq 0))
        Apply = {
            foreach ($p in $ExclusionTargets) {
                if (Test-Path $p) { Add-MpPreference -ExclusionPath $p -ErrorAction SilentlyContinue }
            }
        }
    }

    $f += [pscustomobject]@{
        Id = 'trim'; Group = 'disk'
        Title = 'Enable TRIM on the NVMe SSD'
        Why = 'Without TRIM an SSD slows down as it fills. This drive is 70% full.'
        NeedsAdmin = $true
        IsGood = ($s.trimDisabled -match 'DisableDeleteNotify\s*=\s*0')
        Apply = { fsutil behavior set DisableDeleteNotify 0 | Out-Null }
    }

    # OPT-IN ONLY. Not applied by a plain `apply` run: Outlook's own search
    # and Start-menu file search are built on WSearch, so turning it off
    # trades those away. Enable deliberately with:
    #     .\tune.ps1 apply -Only searchscope
    $f += [pscustomobject]@{
        Id = 'searchscope'; Group = 'optin'
        Title = 'Disable the Windows Search indexer (OPT-IN - breaks Outlook search)'
        Why = 'It re-indexes files PULSE already covers, costing background CPU and disk. But Outlook search and Start-menu file search stop working. Only worth it if you do not search inside Outlook.'
        NeedsAdmin = $true
        OptIn = $true
        IsGood = ($null -ne $s.svc_WSearch -and $s.svc_WSearch.StartType -eq 'Disabled')
        Apply = {
            Stop-Service WSearch -Force -ErrorAction SilentlyContinue
            Set-Service WSearch -StartupType Disabled
        }
    }

    return $f
}

# ---------------------------------------------------------------- actions

function Show-Report {
    param($s, $fixes)

    Head "PULSE TUNE - system report"
    Say ("    Machine   : {0}" -f (Get-CimInstance Win32_ComputerSystem).Model) 'DarkGray'
    Say ("    RAM       : {0} GB total, {1} GB free" -f $s.ramTotalGB, $s.ramFreeGB) 'DarkGray'
    Say ("    Power plan: {0}" -f $s.powerPlanName) 'DarkGray'
    Say ("    Admin     : {0}" -f $(if ($IsAdmin) { 'yes' } else { 'NO - admin fixes will be skipped' })) 'DarkGray'

    Head "Disks"
    foreach ($d in $s.disks) {
        $st = if ($d.PctUsed -gt 90) { 'FAIL' } elseif ($d.PctUsed -gt 80) { 'WARN' } else { 'OK' }
        Item $st ("{0} {1,3}% used - {2} GB free of {3} GB" -f $d.Drive, $d.PctUsed, $d.FreeGB, $d.TotalGB)
    }

    Head "Startup programs ($($s.startup.Count))"
    foreach ($p in ($s.startup | Select-Object -First 20)) {
        Item 'WARN' $p.Name ($p.Command -replace '\s+', ' ')
    }
    Say "    (PULSE does not disable these automatically - they are your call." 'DarkGray'
    Say "     Review in Task Manager > Startup apps.)" 'DarkGray'

    Head "Tunable settings"
    foreach ($fx in $fixes) {
        if ($fx.IsGood) {
            Item 'OK' $fx.Title
        } elseif ($fx.OptIn) {
            Item 'SKIP' $fx.Title $fx.Why
        } elseif ($fx.NeedsAdmin -and -not $IsAdmin) {
            Item 'SKIP' $fx.Title 'needs an elevated shell'
        } else {
            Item 'FIX' $fx.Title $fx.Why
        }
    }

    $todo = @($fixes | Where-Object {
        -not $_.IsGood -and -not $_.OptIn -and (-not $_.NeedsAdmin -or $IsAdmin) })
    Write-Host ''
    if ($todo.Count) {
        Say ("  {0} fix(es) available.  Run:  .\tune.ps1 apply" -f $todo.Count) 'Cyan'
    } else {
        Say "  Nothing to change - this PC is already tuned." 'Green'
    }
    Write-Host ''
}

function Invoke-Apply {
    param($s, $fixes)

    $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
    $backupPath = Join-Path $BackupDir "tune_$stamp.json"
    $s | ConvertTo-Json -Depth 6 | Set-Content -Path $backupPath -Encoding utf8
    Head "PULSE TUNE - apply"
    Say "    Backup written: $backupPath" 'DarkGray'
    Say "    Undo any time with:  .\tune.ps1 restore" 'DarkGray'

    $applied = @()
    foreach ($fx in $fixes) {
        if ($Only -and $Only -notcontains $fx.Group -and $Only -notcontains $fx.Id) { continue }
        # Opt-in fixes are only ever applied when named explicitly via -Only.
        if ($fx.OptIn -and -not $Only) { Item 'SKIP' $fx.Title 'opt-in: use -Only searchscope'; continue }
        if ($fx.IsGood) { Item 'OK' $fx.Title; continue }
        if ($fx.NeedsAdmin -and -not $IsAdmin) {
            Item 'SKIP' $fx.Title 'needs an elevated shell'
            continue
        }
        if (-not $Yes) {
            Write-Host ''
            Write-Host "    $($fx.Title)" -ForegroundColor Cyan
            Write-Host "    $($fx.Why)" -ForegroundColor DarkGray
            $ans = Read-Host '    Apply? [Y/n]'
            if ($ans -and $ans -notmatch '^[Yy]') { Item 'SKIP' $fx.Title 'declined'; continue }
        }
        try {
            & $fx.Apply
            Item 'OK' ("applied: " + $fx.Title)
            $applied += $fx.Id
        } catch {
            Item 'FAIL' ("failed: " + $fx.Title) $_.Exception.Message
        }
    }

    (@{ backup = $backupPath; applied = $applied; at = $stamp } |
        ConvertTo-Json) | Set-Content (Join-Path $BackupDir 'last.json') -Encoding utf8

    Write-Host ''
    Say ("  Applied {0} change(s)." -f $applied.Count) 'Green'
    if ($applied -contains 'visualfx') {
        Say "  Sign out and back in for the animation changes to take full effect." 'Yellow'
    }
    Write-Host ''
}

function Invoke-Restore {
    $path = $Backup
    if (-not $path) {
        $last = Join-Path $BackupDir 'last.json'
        if (-not (Test-Path $last)) { Say '  No backup found - nothing to restore.' 'Yellow'; return }
        $path = (Get-Content $last -Raw | ConvertFrom-Json).backup
    }
    if (-not (Test-Path $path)) { Say "  Backup not found: $path" 'Red'; return }

    $b = Get-Content $path -Raw | ConvertFrom-Json
    Head "PULSE TUNE - restore from $([IO.Path]::GetFileName($path))"

    try { powercfg /setactive $b.powerPlan; Item 'OK' "power plan -> $($b.powerPlanName)" }
    catch { Item 'FAIL' 'power plan' $_.Exception.Message }

    try {
        [Environment]::SetEnvironmentVariable('PYTHONUTF8', $b.pythonUtf8, 'User')
        [Environment]::SetEnvironmentVariable('PYTHONIOENCODING', $b.pythonIoEnc, 'User')
        Item 'OK' 'python env vars restored'
    } catch { Item 'FAIL' 'python env vars' $_.Exception.Message }

    if ($null -ne $b.visualFx) {
        try {
            Set-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects' `
                'VisualFXSetting' $b.visualFx -Type DWord
            Item 'OK' "visual effects -> $($b.visualFx)"
        } catch { Item 'FAIL' 'visual effects' $_.Exception.Message }
    }

    if ($null -ne $b.storageSense) {
        try {
            Set-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\StorageSense\Parameters\StoragePolicy' `
                '01' $b.storageSense -Type DWord
            Item 'OK' "storage sense -> $($b.storageSense)"
        } catch { Item 'FAIL' 'storage sense' $_.Exception.Message }
    }

    if ($IsAdmin) {
        if ($null -ne $b.defenderExclusions) {
            try {
                $now = @((Get-MpPreference).ExclusionPath)
                foreach ($p in $now) {
                    if ($b.defenderExclusions -notcontains $p) {
                        Remove-MpPreference -ExclusionPath $p -ErrorAction SilentlyContinue
                    }
                }
                Item 'OK' 'defender exclusions restored'
            } catch { Item 'FAIL' 'defender exclusions' $_.Exception.Message }
        }
        if ($b.svc_WSearch) {
            try {
                Set-Service WSearch -StartupType $b.svc_WSearch.StartType
                if ($b.svc_WSearch.Status -eq 'Running') { Start-Service WSearch -ErrorAction SilentlyContinue }
                Item 'OK' "WSearch -> $($b.svc_WSearch.StartType)"
            } catch { Item 'FAIL' 'WSearch' $_.Exception.Message }
        }
        if ($b.trimDisabled -match 'DisableDeleteNotify\s*=\s*1') {
            try { fsutil behavior set DisableDeleteNotify 1 | Out-Null; Item 'OK' 'TRIM restored to disabled' }
            catch { Item 'FAIL' 'TRIM' $_.Exception.Message }
        }
    } else {
        Item 'SKIP' 'admin-scoped settings' 'rerun restore in an elevated shell to finish'
    }

    Write-Host ''
    Say '  Restore complete.' 'Green'
    Write-Host ''
}

# ---------------------------------------------------------------- main

$State = Read-State
$Fixes = Get-Fixes $State

switch ($Action) {
    'report'  { Show-Report $State $Fixes }
    'apply'   { Invoke-Apply $State $Fixes }
    'restore' { Invoke-Restore }
}
