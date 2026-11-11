#Requires -RunAsAdministrator
<#
.SYNOPSIS
  Install and enroll a Sextant agent on Windows.

.DESCRIPTION
  Creates a Python virtual environment under ProgramData, installs the
  sextant package, enrolls with a one-time token, locks down the config
  file, and registers a SYSTEM scheduled task at startup.

  Python 3.11+ must already be installed and available as `py -3` or `python`.

.EXAMPLE
  .\install-windows-agent.ps1 -Server https://ops.example.com -Token sextant_enroll_... -Name "Studio PC"
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Server,
    [Parameter(Mandatory = $true)][string]$Token,
    [string]$Name = $env:COMPUTERNAME,
    [string]$SourcePath = (Split-Path -Parent $PSScriptRoot),
    [string]$InstallDir = (Join-Path $env:ProgramData 'SextantAgent'),
    [string[]]$WatchServices = @(),
    [string]$CaFile = "",
    [string]$Profile = "workstation",
    [switch]$AllowPackages,
    [switch]$NoPackages,
    [switch]$NoSelfUpdate
)

$ErrorActionPreference = 'Stop'

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Open PowerShell as Administrator and run this script again."
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
if ($Server.StartsWith('http://')) {
    Write-Warning "This agent will use plain HTTP. Use HTTPS for any non-lab network."
}
if (-not (Test-Path (Join-Path $SourcePath 'pyproject.toml'))) {
    throw "SourcePath must point at the sextant source tree or a built wheel. Current: $SourcePath"
}

$venvDir = Join-Path $InstallDir 'venv'
$venvPython = Join-Path $venvDir 'Scripts\python.exe'
$agentExe = Join-Path $venvDir 'Scripts\sextant-agent.exe'
$configPath = Join-Path $InstallDir 'config.json'
$logPath = Join-Path $InstallDir 'agent.log'

New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
if (-not (Test-Path $venvPython)) {
    Write-Host "Creating virtual environment in $venvDir"
    $python = Resolve-Python
    if ($python.Length -gt 1) {
        & $python[0] $python[1] -m venv $venvDir
    } else {
        & $python[0] -m venv $venvDir
    }
    if ($LASTEXITCODE -ne 0) { throw "Failed to create the Python virtual environment." }
}

Write-Host "Installing sextant from $SourcePath"
& (Join-Path $venvDir 'Scripts\pip.exe') install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed." }
& (Join-Path $venvDir 'Scripts\pip.exe') install --no-deps --upgrade $SourcePath
if ($LASTEXITCODE -ne 0) { throw "Package installation failed." }

$enrollArgs = @(
    'enroll',
    '--config', $configPath,
    '--server', $Server,
    '--token', $Token,
    '--name', $Name,
    '--profile', $Profile
)
if ($CaFile) { $enrollArgs += @('--ca-file', $CaFile) }
if ($AllowPackages) { $enrollArgs += '--allow-packages' }
if ($NoPackages) { $enrollArgs += '--no-packages' }
if ($NoSelfUpdate) { $enrollArgs += '--no-self-update' }
foreach ($service in $WatchServices) {
    if ($service) { $enrollArgs += @('--watch-service', $service) }
}

& $agentExe @enrollArgs
if ($LASTEXITCODE -ne 0) { throw "Agent enrollment failed. Confirm the token is valid and the server is reachable." }

# Restrict the config (contains the agent bearer token) to SYSTEM and Administrators.
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

Write-Host ""
Write-Host "Agent enrolled and started." -ForegroundColor Green
Write-Host "  Config: $configPath"
Write-Host "  Log:    $logPath"
Write-Host "  Task:   SextantAgent"
Write-Host "Check status with: Get-ScheduledTask SextantAgent"
