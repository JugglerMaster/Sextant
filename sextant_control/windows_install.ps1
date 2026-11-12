[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Server,
    [Parameter(Mandatory = $true)][string]$Token,
    [string]$Name = $env:COMPUTERNAME,
    [Parameter(Mandatory = $true)][string]$SourcePath,
    [string]$Profile = "workstation",
    [string[]]$WatchServices = @(),
    [string]$ExpectedSha256 = "",
    [switch]$AllowDiscovery,
    [switch]$NoSelfUpdate
)

$ErrorActionPreference = 'Stop'

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "The WinRM session is not running with administrator rights."
    }
}

function Resolve-Python {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        return @('py', '-3')
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        return @('python')
    }
    throw "Python 3.11+ is required. Install it from python.org and enable 'Add python.exe to PATH'."
}

Assert-Administrator
if ($Server.StartsWith('http://') -and -not $Server.StartsWith('http://127.0.0.1') -and -not $Server.StartsWith('http://localhost')) {
    throw "The console URL must use HTTPS for a remote Windows install."
}
if (-not (Test-Path -LiteralPath $SourcePath -PathType Leaf)) {
    throw "The agent wheel was not found at $SourcePath."
}
if ($ExpectedSha256) {
    $actual = (Get-FileHash -LiteralPath $SourcePath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $ExpectedSha256.ToLowerInvariant()) {
        throw "Agent wheel SHA-256 mismatch."
    }
}

$InstallDir = Join-Path $env:ProgramData 'SextantAgent'
$venvDir = Join-Path $InstallDir 'venv'
$venvPython = Join-Path $venvDir 'Scripts\python.exe'
$configPath = Join-Path $InstallDir 'config.json'
$logPath = Join-Path $InstallDir 'agent.log'

New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
if (-not (Test-Path -LiteralPath $venvPython)) {
    $python = Resolve-Python
    if ($python.Length -gt 1) {
        & $python[0] $python[1] -m venv $venvDir
    } else {
        & $python[0] -m venv $venvDir
    }
    if ($LASTEXITCODE -ne 0) { throw "Failed to create the Python virtual environment." }
}

& (Join-Path $venvDir 'Scripts\pip.exe') install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed." }
& (Join-Path $venvDir 'Scripts\pip.exe') install --no-deps --upgrade $SourcePath
if ($LASTEXITCODE -ne 0) { throw "Package installation failed." }

$currentDir = Join-Path $InstallDir 'current'
cmd /c rmdir "$currentDir" 2>$null | Out-Null
cmd /c mklink /J "$currentDir" "$venvDir" | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Could not create the current junction."
}
$agentExe = Join-Path $currentDir 'Scripts\sextant-agent.exe'

$enrollArgs = @(
    'enroll',
    '--config', $configPath,
    '--server', $Server,
    '--token', $Token,
    '--name', $Name,
    '--profile', $Profile
)
if ($AllowDiscovery) { $enrollArgs += '--allow-discovery' }
if ($NoSelfUpdate) { $enrollArgs += '--no-self-update' }
foreach ($service in $WatchServices) {
    if ($service) { $enrollArgs += @('--watch-service', $service) }
}

& $agentExe @enrollArgs
if ($LASTEXITCODE -ne 0) { throw "Agent enrollment failed." }

$acl = Get-Acl -Path $configPath
$acl.SetAccessRuleProtection($true, $false)
$systemSid = [System.Security.Principal.SecurityIdentifier]::new(
    [System.Security.Principal.WellKnownSidType]::LocalSystemSid, $null)
$adminsSid = [System.Security.Principal.SecurityIdentifier]::new(
    [System.Security.Principal.WellKnownSidType]::BuiltinAdministratorsSid, $null)
$systemRule = [System.Security.AccessControl.FileSystemAccessRule]::new(
    $systemSid, [System.Security.AccessControl.FileSystemRights]::FullControl,
    [System.Security.AccessControl.AccessControlType]::Allow)
$adminsRule = [System.Security.AccessControl.FileSystemAccessRule]::new(
    $adminsSid, [System.Security.AccessControl.FileSystemRights]::FullControl,
    [System.Security.AccessControl.AccessControlType]::Allow)
$acl.SetAccessRule($systemRule)
$acl.SetAccessRule($adminsRule)
Set-Acl -Path $configPath -AclObject $acl
icacls $InstallDir /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" | Out-Null

$action = New-ScheduledTaskAction `
    -Execute $agentExe `
    -Argument ('run --config "{0}" --log-file "{1}"' -f $configPath, $logPath) `
    -WorkingDirectory $InstallDir
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask `
    -TaskName 'SextantAgent' `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description 'Sextant agent: heartbeat, telemetry, and audited remote operations' `
    -Force | Out-Null
Start-ScheduledTask -TaskName 'SextantAgent'

Write-Output "Sextant agent installed and started."
Write-Output "Config: $configPath"
Write-Output "Log: $logPath"
Write-Output "Task: SextantAgent"
