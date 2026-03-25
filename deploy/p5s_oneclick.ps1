Param(
    [string]$Distro = "Ubuntu",
    [int]$HealthSeconds = 600,
    [int]$HealthInterval = 5,
    [string]$ApiBase = "http://127.0.0.1:8000",
    [string]$Exchange = "binance",
    [int]$BuildImages = 0
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Convert-ToWslPath {
    param([Parameter(Mandatory = $true)][string]$WindowsPath)
    $full = [System.IO.Path]::GetFullPath($WindowsPath)
    $drive = $full.Substring(0, 1).ToLowerInvariant()
    $rest = $full.Substring(2).Replace('\', '/')
    return "/mnt/$drive$rest"
}

# Always execute docker compose from WSL to avoid Windows/WSL dual-engine confusion.
$projectRoot = Convert-ToWslPath (Join-Path $PSScriptRoot "..")
$cmd = @"
cd '$projectRoot' && \
HEALTH_SECONDS='$HealthSeconds' \
HEALTH_INTERVAL='$HealthInterval' \
API_BASE='$ApiBase' \
EXCHANGE='$Exchange' \
BUILD_IMAGES='$BuildImages' \
bash deploy/p5s_oneclick.sh
"@

wsl -d $Distro -e bash -lc $cmd
