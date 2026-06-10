# uninstall.ps1 — retire proprement le sidecar (Windows).
#
# Lit %USERPROFILE%\.config\anon-sidecar\.installed pour savoir quoi enlever ;
# refuse de toucher quoi que ce soit s'il n'existe pas (autre installation manuelle).
#
# Idempotent : peut être relancé plusieurs fois.
#
# Par défaut, conserve le token (utile pour réinstaller). Ajoute -Purge pour
# tout effacer y compris le token et le marker.
#
# Usage :
#   powershell -ExecutionPolicy Bypass -File sidecar\uninstall.ps1
#   powershell -ExecutionPolicy Bypass -File sidecar\uninstall.ps1 -Purge

[CmdletBinding()]
param(
    [switch]$Purge
)

$ErrorActionPreference = 'Stop'

$ConfigDir = Join-Path $env:USERPROFILE '.config\anon-sidecar'
$Marker    = Join-Path $ConfigDir '.installed'

Write-Host ""
Write-Host "  LLM Anonymization — désinstallation du sidecar (Windows)"
Write-Host ""

if (-not (Test-Path $Marker)) {
    Write-Host "  [!] Pas de marker à $Marker — rien à désinstaller."
    Write-Host "      (Si tu as installé manuellement, fais 'docker compose down' à la main.)"
    exit 0
}

# Parse le marker (KEY=VALUE par ligne)
$vars = @{}
Get-Content $Marker | ForEach-Object {
    if ($_ -match '^([A-Z_]+)=(.*)$') { $vars[$Matches[1]] = $Matches[2] }
}

$ScriptDir = $vars['SCRIPT_DIR']
$EnvFile   = $vars['ENV_FILE']
$TokenFile = $vars['TOKEN_FILE']

# ── 1. Stopper les containers ───────────────────────────────────────────────
$compose = if ($ScriptDir) { Join-Path $ScriptDir 'docker-compose.yml' } else { $null }
if ($compose -and (Test-Path $compose)) {
    docker compose --env-file "$EnvFile" -f "$compose" down 2>&1 | ForEach-Object { "  $_" }
    Write-Host "  [OK] Stack arrêtée"
} else {
    Write-Host "  [!] Script dir introuvable, fallback rm container par nom"
    docker rm -f anon-sidecar anon-sidecar-redis 2>$null | Out-Null
}

# ── 2. Purge optionnelle ────────────────────────────────────────────────────
if ($Purge) {
    if ($EnvFile   -and (Test-Path $EnvFile))   { Remove-Item $EnvFile   -Force }
    if ($TokenFile -and (Test-Path $TokenFile)) { Remove-Item $TokenFile -Force }
    Remove-Item $Marker -Force
    if ((Test-Path $ConfigDir) -and -not (Get-ChildItem $ConfigDir -Force)) {
        Remove-Item $ConfigDir -Force
    }
    Write-Host "  [OK] Token + env + marker purgés"
} else {
    Remove-Item $Marker -Force
    Write-Host "  [OK] Marker retiré (token et env conservés — relance install.ps1 pour réutiliser)"
    Write-Host "      (utilise -Purge pour tout effacer)"
}

Write-Host ""
Write-Host "  Désinstallation terminée."
Write-Host ""
