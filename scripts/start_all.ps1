[CmdletBinding()]
param(
    [switch]$NoReload,
    [switch]$PreflightOnly
)

$ErrorActionPreference = "Stop"

# Compatibility entry point.  Keep one runtime for local testing: the native
# Windows services managed by start_local.ps1, never Docker or ngrok.
if ($PreflightOnly) {
    & (Join-Path $PSScriptRoot "start_local.ps1") -PreflightOnly
} elseif ($NoReload) {
    & (Join-Path $PSScriptRoot "start_local.ps1") -NoReload
} else {
    & (Join-Path $PSScriptRoot "start_local.ps1")
}
