# install.ps1 — installe le sidecar zero-trust sur Windows (Docker Desktop + WSL2).
#
# Équivalent fonctionnel de install.sh :
#   1. Vérifie Docker + Docker Compose
#   2. Génère un secret X-Sidecar-Token (ACL restreinte à l'utilisateur courant) si absent
#   3. Build l'image (~2 GB, premier run uniquement)
#   4. Lance la stack avec docker compose
#   5. Healthcheck sur http://127.0.0.1:8787/healthz
#
# Pas d'équivalent systemd : Docker Desktop avec `restart: unless-stopped`
# redémarre les containers automatiquement au login si "Start Docker Desktop when
# you sign in" est activé (Settings → General).
#
# Désinstallation : `powershell -ExecutionPolicy Bypass -File sidecar\uninstall.ps1`
#
# Usage :
#   powershell -ExecutionPolicy Bypass -File sidecar\install.ps1
#   powershell -ExecutionPolicy Bypass -File sidecar\install.ps1 -Gateway http://192.168.1.13:8001
#   powershell -ExecutionPolicy Bypass -File sidecar\install.ps1 -Gateway http://192.168.1.13:8001 -Key sk-xxx

[CmdletBinding()]
param(
    [string]$Gateway = $env:GATEWAY_URL,
    [string]$Key     = $env:GATEWAY_API_KEY,
    [string]$AllowedOrigin = $env:ANON_SIDECAR_ALLOWED_ORIGIN
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrEmpty($Gateway))       { $Gateway = 'http://host.docker.internal:8001' }
if ([string]::IsNullOrEmpty($AllowedOrigin)) { $AllowedOrigin = 'http://localhost:3000' }

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoDir   = Split-Path -Parent $ScriptDir
$ConfigDir = Join-Path $env:USERPROFILE '.config\anon-sidecar'
$TokenFile = Join-Path $ConfigDir 'token'
$EnvFile   = Join-Path $ConfigDir 'env'
$Marker    = Join-Path $ConfigDir '.installed'

Write-Host ""
Write-Host "  LLM Anonymization — sidecar zero-trust install (Windows)"
Write-Host "  Gateway (pour KB + classify_column) : $Gateway"
Write-Host ""

# ── 1. Dépendances ──────────────────────────────────────────────────────────
try {
    $dockerVer = (docker --version) 2>$null
    if ($LASTEXITCODE -ne 0) { throw "docker not found" }
    Write-Host "  [OK] $dockerVer"
} catch {
    Write-Host "  [X] Docker requis. Installe Docker Desktop : https://docs.docker.com/desktop/install/windows-install/"
    exit 1
}

try {
    $composeVer = (docker compose version --short) 2>$null
    if ($LASTEXITCODE -ne 0) { throw "compose not found" }
    Write-Host "  [OK] docker compose $composeVer"
} catch {
    Write-Host "  [X] docker compose plugin requis (inclus dans Docker Desktop récent)."
    exit 1
}

# Vérifier que le daemon répond
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  [X] Docker daemon injoignable. Lance Docker Desktop puis relance ce script."
    exit 1
}

# ── 2. Config dir + ACL utilisateur seul ────────────────────────────────────
if (-not (Test-Path $ConfigDir)) {
    New-Item -ItemType Directory -Path $ConfigDir -Force | Out-Null
}

function Set-OwnerOnlyAcl {
    param([string]$Path)
    $acl = Get-Acl $Path
    $acl.SetAccessRuleProtection($true, $false)  # break inheritance
    $acl.Access | ForEach-Object { [void]$acl.RemoveAccessRule($_) }
    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
        [System.Security.Principal.WindowsIdentity]::GetCurrent().Name,
        'FullControl', 'Allow'
    )
    $acl.AddAccessRule($rule)
    Set-Acl -Path $Path -AclObject $acl
}

Set-OwnerOnlyAcl -Path $ConfigDir

# ── 3. Token X-Sidecar-Token ────────────────────────────────────────────────
if (-not (Test-Path $TokenFile) -or ((Get-Item $TokenFile).Length -eq 0)) {
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $bytes = New-Object byte[] 32
    $rng.GetBytes($bytes)
    $hex = ($bytes | ForEach-Object { $_.ToString('x2') }) -join ''
    # Pas de BOM, pas de newline final — sinon le token comparé serveur-side ne match pas
    [System.IO.File]::WriteAllText($TokenFile, $hex, [System.Text.UTF8Encoding]::new($false))
    Set-OwnerOnlyAcl -Path $TokenFile
    Write-Host "  [OK] Token X-Sidecar-Token généré : $TokenFile"
} else {
    Write-Host "  [OK] Token existant : $TokenFile"
}

$Token = (Get-Content $TokenFile -Raw).Trim()

# ── 4. .env pour docker compose ─────────────────────────────────────────────
$envContent = @"
GATEWAY_URL=$Gateway
GATEWAY_API_KEY=$Key
ANON_SIDECAR_TOKEN=$Token
ANON_SIDECAR_ALLOWED_ORIGIN=$AllowedOrigin
"@
[System.IO.File]::WriteAllText($EnvFile, $envContent, [System.Text.UTF8Encoding]::new($false))
Set-OwnerOnlyAcl -Path $EnvFile
Write-Host "  [OK] Variables d'env écrites : $EnvFile"

# ── 5. Build de l'image ─────────────────────────────────────────────────────
Write-Host "  Build de l'image sidecar (peut prendre 5-10 min la première fois)..."
$compose = Join-Path $ScriptDir 'docker-compose.yml'
docker compose --env-file "$EnvFile" -f "$compose" build sidecar
if ($LASTEXITCODE -ne 0) { Write-Host "  [X] Build échoué."; exit 1 }

# ── 6. Démarrage de la stack ────────────────────────────────────────────────
docker compose --env-file "$EnvFile" -f "$compose" up -d
if ($LASTEXITCODE -ne 0) { Write-Host "  [X] Démarrage échoué."; exit 1 }
Write-Host "  [OK] Stack démarrée"

# ── 7. Healthcheck ──────────────────────────────────────────────────────────
Write-Host -NoNewline "  Attente du healthcheck... "
$ok = $false
for ($i = 1; $i -le 30; $i++) {
    try {
        $resp = Invoke-WebRequest -Uri 'http://127.0.0.1:8787/healthz' -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
        if ($resp.StatusCode -eq 200) { $ok = $true; break }
    } catch { }
    Start-Sleep -Seconds 1
}
if ($ok) { Write-Host "ok" } else { Write-Host "TIMEOUT"; exit 1 }

# ── 8. Marker pour uninstall ────────────────────────────────────────────────
$markerContent = @"
INSTALLED_AT=$([DateTimeOffset]::Now.ToString('o'))
REPO_DIR=$RepoDir
SCRIPT_DIR=$ScriptDir
ENV_FILE=$EnvFile
TOKEN_FILE=$TokenFile
"@
[System.IO.File]::WriteAllText($Marker, $markerContent, [System.Text.UTF8Encoding]::new($false))

Write-Host ""
Write-Host "  [OK] Installation terminée."
Write-Host "    Sidecar : http://127.0.0.1:8787"
Write-Host "    Token   : $TokenFile  (lu automatiquement par MCP/hooks)"
Write-Host "    Logs    : docker logs -f anon-sidecar"
Write-Host "    Stop    : powershell -ExecutionPolicy Bypass -File sidecar\uninstall.ps1"
Write-Host ""
Write-Host "    Démarrage au boot : vérifie que Docker Desktop est configuré pour"
Write-Host "    démarrer au login (Settings -> General -> 'Start Docker Desktop when"
Write-Host "    you sign in'). Le sidecar a restart=unless-stopped donc remontera seul."
Write-Host ""
