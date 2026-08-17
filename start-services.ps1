[CmdletBinding()]
param(
    [switch]$NoReload,
    [switch]$PreflightOnly
)

$ErrorActionPreference = "Stop"

# Historical entry point retained so that old shortcuts cannot start a second
# Docker/ngrok stack.  All local testing now has exactly one runtime.
if ($PreflightOnly) {
    & (Join-Path $PSScriptRoot "scripts\start_local.ps1") -PreflightOnly
} elseif ($NoReload) {
    & (Join-Path $PSScriptRoot "scripts\start_local.ps1") -NoReload
} else {
    & (Join-Path $PSScriptRoot "scripts\start_local.ps1")
}
