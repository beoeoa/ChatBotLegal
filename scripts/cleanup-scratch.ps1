[CmdletBinding(SupportsShouldProcess)]
param(
    [ValidateRange(1, 3650)]
    [int]$OlderThanDays = 30,
    [switch]$Apply
)

$ErrorActionPreference = "Stop"
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$scratchRoot = Join-Path $repositoryRoot "scratch"

if (-not (Test-Path -LiteralPath $scratchRoot -PathType Container)) {
    Write-Host "Không có thư mục scratch để dọn: $scratchRoot"
    exit 0
}

$cutoff = (Get-Date).AddDays(-$OlderThanDays)
$files = @(
    Get-ChildItem -LiteralPath $scratchRoot -Force -Recurse -File |
        Where-Object { $_.LastWriteTime -lt $cutoff }
)

if ($files.Count -eq 0) {
    Write-Host "Không có tệp scratch cũ hơn $OlderThanDays ngày."
    exit 0
}

$totalBytes = [long](($files | Measure-Object -Property Length -Sum).Sum)
Write-Host ("Tìm thấy {0} tệp ({1:N2} MB) cũ hơn {2:yyyy-MM-dd}." -f $files.Count, ($totalBytes / 1MB), $cutoff)
$files | Select-Object FullName, Length, LastWriteTime | Format-Table -AutoSize

if (-not $Apply) {
    Write-Host "Chế độ xem trước: chưa xóa gì. Chạy lại với -Apply để thực hiện."
    exit 0
}

foreach ($file in $files) {
    $resolvedFile = (Resolve-Path -LiteralPath $file.FullName).Path
    if (-not $resolvedFile.StartsWith($scratchRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Từ chối xóa tệp nằm ngoài scratch: $resolvedFile"
    }

    if ($PSCmdlet.ShouldProcess($resolvedFile, "Xóa tệp scratch cũ")) {
        Remove-Item -LiteralPath $resolvedFile -Force
    }
}

Get-ChildItem -LiteralPath $scratchRoot -Force -Recurse -Directory |
    Sort-Object FullName -Descending |
    ForEach-Object {
        if (-not (Get-ChildItem -LiteralPath $_.FullName -Force)) {
            Remove-Item -LiteralPath $_.FullName -Force
        }
    }

Write-Host "Đã dọn các tệp scratch đủ điều kiện."
