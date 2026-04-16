param(
    [switch]$Diagnose,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Args
)

$ErrorActionPreference = "Stop"
$env:SystemRoot = if ($env:SystemRoot) { $env:SystemRoot } elseif ($env:windir) { $env:windir } else { 'C:\Windows' }
$env:windir = $env:SystemRoot
$env:ComSpec = if ($env:ComSpec) { $env:ComSpec } else { Join-Path $env:SystemRoot 'System32\cmd.exe' }
$env:PATHEXT = if ($env:PATHEXT) { $env:PATHEXT } else { '.COM;.EXE;.BAT;.CMD;.VBS;.VBE;.JS;.JSE;.WSF;.WSH;.MSC;.CPL' }
if (-not $env:PROCESSOR_ARCHITECTURE) { $env:PROCESSOR_ARCHITECTURE = 'AMD64' }
$profileRoot = if ($env:USERPROFILE) { $env:USERPROFILE } elseif ($env:HOME) { $env:HOME } else { [Environment]::GetFolderPath('UserProfile') }
if (-not $env:USERPROFILE) { $env:USERPROFILE = $profileRoot }
if (-not $env:HOME) { $env:HOME = $profileRoot }
if (-not $env:HOMEDRIVE -and $profileRoot.Length -ge 2) { $env:HOMEDRIVE = $profileRoot.Substring(0,2) }
if (-not $env:HOMEPATH -and $profileRoot.Length -gt 2) { $env:HOMEPATH = $profileRoot.Substring(2) }
if (-not $env:LOCALAPPDATA) { $env:LOCALAPPDATA = [Environment]::GetFolderPath('LocalApplicationData') }
if (-not $env:APPDATA) { $env:APPDATA = [Environment]::GetFolderPath('ApplicationData') }
if (-not $env:TEMP) { $env:TEMP = Join-Path $env:LOCALAPPDATA 'Temp' }
if (-not $env:TMP) { $env:TMP = $env:TEMP }

$ScriptPath = $PSCommandPath
if ([string]::IsNullOrWhiteSpace($ScriptPath)) {
    $ScriptPath = $MyInvocation.MyCommand.Definition
}
if ([string]::IsNullOrWhiteSpace($ScriptPath)) {
    $ScriptPath = Join-Path (Get-Location).Path "start_jarvis.ps1"
}
$Root = Split-Path -Parent $ScriptPath
$MainScript = Join-Path $Root "main.py"

function Get-PythonCandidates {
    $list = New-Object System.Collections.Generic.List[string]

    if ($env:MARK_XXX_PYTHON) {
        $list.Add($env:MARK_XXX_PYTHON)
    }

    $localAppData = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $HOME "AppData\\Local" }
    foreach ($name in @("Python313", "Python312", "Python311", "Python310")) {
        $list.Add((Join-Path $localAppData "Programs\\Python\\$name\\python.exe"))
    }

    foreach ($venvName in @(".venv", "venv")) {
        $list.Add((Join-Path $Root "$venvName\\Scripts\\python.exe"))
    }

    foreach ($command in @("python", "python3")) {
        try {
            $resolved = (Get-Command $command -ErrorAction Stop).Source
            if ($resolved) {
                $list.Add($resolved)
            }
        } catch {
        }
    }

    $seen = @{}
    foreach ($item in $list) {
        if ([string]::IsNullOrWhiteSpace($item)) { continue }
        $key = $item.ToLowerInvariant()
        if (-not $seen.ContainsKey($key)) {
            $seen[$key] = $true
            $item
        }
    }
}

function Test-PythonCandidate {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return [pscustomobject]@{ Path = $Path; Exists = $false; Works = $false; Message = "Missing" }
    }

    try {
        $probeDir = $profileRoot
        if ([string]::IsNullOrWhiteSpace($probeDir) -or -not (Test-Path -LiteralPath $probeDir)) {
            $probeDir = Split-Path -Parent $Path
        }
        if ([string]::IsNullOrWhiteSpace($probeDir) -or -not (Test-Path -LiteralPath $probeDir)) {
            $probeDir = $Root
        }

        $stdoutFile = Join-Path $env:TEMP ("mark_xxx_probe_stdout_{0}.txt" -f ([guid]::NewGuid().ToString("N")))
        $stderrFile = Join-Path $env:TEMP ("mark_xxx_probe_stderr_{0}.txt" -f ([guid]::NewGuid().ToString("N")))
        try {
            $proc = Start-Process -FilePath $Path -ArgumentList "--version" -WorkingDirectory $probeDir -RedirectStandardOutput $stdoutFile -RedirectStandardError $stderrFile -Wait -PassThru
            $exitCode = $proc.ExitCode
            $stdout = if (Test-Path -LiteralPath $stdoutFile) { Get-Content -LiteralPath $stdoutFile -Raw -ErrorAction SilentlyContinue } else { "" }
            $stderr = if (Test-Path -LiteralPath $stderrFile) { Get-Content -LiteralPath $stderrFile -Raw -ErrorAction SilentlyContinue } else { "" }
            $output = (($stdout, $stderr) -join "`n").Trim()
        } finally {
            Remove-Item -LiteralPath $stdoutFile, $stderrFile -ErrorAction SilentlyContinue
        }
        return [pscustomobject]@{ Path = $Path; Exists = $true; Works = ($exitCode -eq 0); Message = ($output | Out-String).Trim() }
    } catch {
        return [pscustomobject]@{ Path = $Path; Exists = $true; Works = $false; Message = $_.Exception.Message }
    }
}

$results = @()
foreach ($candidate in @(Get-PythonCandidates)) {
    $candidateText = [string]$candidate
    if ([string]::IsNullOrWhiteSpace($candidateText)) {
        continue
    }
    $results += Test-PythonCandidate -Path $candidateText
}

if ($Diagnose) {
    Write-Host "MARK XXX Python launcher diagnostics" -ForegroundColor Cyan
    foreach ($item in $results) {
        $status = if (-not $item.Exists) { "MISSING" } elseif ($item.Works) { "OK" } else { "BROKEN" }
        Write-Host "[$status] $($item.Path)"
        if ($item.Message) {
            Write-Host "  $($item.Message)"
        }
    }
    exit 0
}

$selected = $results | Where-Object { $_.Exists -and $_.Works } | Select-Object -First 1
if (-not $selected) {
    Write-Host "No working Python runtime was found for MARK XXX." -ForegroundColor Red
    Write-Host "Run '.\\start_jarvis.ps1 -Diagnose' to inspect detected installs." -ForegroundColor Yellow
    Write-Host "If every candidate is broken, repair or reinstall Python 3.11+ and try again." -ForegroundColor Yellow
    exit 1
}

$LaunchDir = $profileRoot
if ([string]::IsNullOrWhiteSpace($LaunchDir) -or -not (Test-Path -LiteralPath $LaunchDir)) {
    $LaunchDir = $Root
}

Push-Location -LiteralPath $LaunchDir
$exitCode = 1
try {
    $stdoutFile = Join-Path $env:TEMP ("mark_xxx_launch_stdout_{0}.txt" -f ([guid]::NewGuid().ToString("N")))
    $stderrFile = Join-Path $env:TEMP ("mark_xxx_launch_stderr_{0}.txt" -f ([guid]::NewGuid().ToString("N")))
    try {
        $launchArgs = @($MainScript) + @($Args)
        $proc = Start-Process -FilePath $selected.Path -ArgumentList $launchArgs -WorkingDirectory $LaunchDir -RedirectStandardOutput $stdoutFile -RedirectStandardError $stderrFile -Wait -PassThru
        $exitCode = $proc.ExitCode
        if (Test-Path -LiteralPath $stdoutFile) {
            Get-Content -LiteralPath $stdoutFile -ErrorAction SilentlyContinue | Write-Output
        }
        if (Test-Path -LiteralPath $stderrFile) {
            Get-Content -LiteralPath $stderrFile -ErrorAction SilentlyContinue | Write-Error
        }
    } finally {
        Remove-Item -LiteralPath $stdoutFile, $stderrFile -ErrorAction SilentlyContinue
    }
} finally {
    Pop-Location
}

exit $exitCode
