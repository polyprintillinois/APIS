param(
    [string]$Version = "0.1.4"
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$py = Join-Path $root "build_venv\Scripts\python.exe"

function Invoke-WithRetry {
    param(
        [scriptblock]$Action,
        [string]$Label,
        [int]$MaxAttempts = 5,
        [int]$DelaySeconds = 2
    )

    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        try {
            & $Action
            return
        }
        catch {
            if ($attempt -eq $MaxAttempts) {
                throw
            }
            Write-Warning "[APIS] $Label failed on attempt $attempt/${MaxAttempts}: $($_.Exception.Message)"
            Start-Sleep -Seconds $DelaySeconds
        }
    }
}

Write-Host "[APIS] Building with PyInstaller spec..."
if (Test-Path (Join-Path $root "dist")) {
    Remove-Item (Join-Path $root "dist") -Recurse -Force
}
& $py -m PyInstaller --noconfirm --clean build\apis.spec

$releaseDir = Join-Path $root "release"
New-Item -ItemType Directory -Force $releaseDir | Out-Null
$stageDir = Join-Path $root "build\release_stage"
$releaseNotesPath = Join-Path $releaseDir "RELEASE_NOTES_v$Version.md"

$zipName = "APIS-win64-v$Version.zip"
$zipPath = Join-Path $releaseDir $zipName

if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
if (Test-Path $stageDir) { Remove-Item $stageDir -Recurse -Force }

Add-Type -AssemblyName System.IO.Compression.FileSystem

Write-Host "[APIS] Staging release contents..."
New-Item -ItemType Directory -Force $stageDir | Out-Null

try {
    $stageItems = @(
        "dist\APIS",
        "release\README.txt",
        "release\prereq_checklist.md"
    )
    if (Test-Path $releaseNotesPath) {
        $stageItems += $releaseNotesPath
    }

    foreach ($item in $stageItems) {
        Invoke-WithRetry -Label "Copy $item" -Action {
            Copy-Item $item $stageDir -Recurse -Force
        }
    }

    Write-Host "[APIS] Creating release zip: $zipName"
    Invoke-WithRetry -Label "Create zip archive" -Action {
        if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
        [System.IO.Compression.ZipFile]::CreateFromDirectory(
            $stageDir,
            $zipPath,
            [System.IO.Compression.CompressionLevel]::Optimal,
            $false
        )
    }
}
finally {
    if (Test-Path $stageDir) {
        Remove-Item $stageDir -Recurse -Force
    }
}

Write-Host "[APIS] Done: $zipPath"
