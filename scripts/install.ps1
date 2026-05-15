# install.ps1 — instala o binário standalone do Permafrost no Windows
#
# Uso rápido (PowerShell):
#   iwr -useb https://raw.githubusercontent.com/caua-ferreira/permafrost-framework/main/scripts/install.ps1 | iex
#
# Ou especificando versão:
#   $env:VERSION="v0.7.0"; iwr -useb ... | iex
#
# Variáveis de ambiente:
#   VERSION      — versão a instalar (padrão: latest release)
#   INSTALL_DIR  — diretório de instalação (padrão: $env:LOCALAPPDATA\permafrost\bin)
#   NO_VERIFY    — "1" para pular verificação SHA-256

param()

$ErrorActionPreference = "Stop"

$Repo        = "caua-ferreira/permafrost-framework"
$AssetName   = "permafrost-windows-x86_64.exe"
$BinaryName  = "permafrost.exe"
$InstallDir  = if ($env:INSTALL_DIR) { $env:INSTALL_DIR } else { "$env:LOCALAPPDATA\permafrost\bin" }

# ── resolver versão ───────────────────────────────────────────────────────────

$Version = $env:VERSION
if (-not $Version) {
    Write-Host "Verificando ultima versao..." -ForegroundColor Cyan
    $Release = Invoke-RestMethod "https://api.github.com/repos/$Repo/releases/latest"
    $Version = $Release.tag_name
    if (-not $Version) {
        Write-Error "Nao foi possivel obter a versao mais recente. Use `$env:VERSION='v0.x.0'` para especificar."
        exit 1
    }
}

Write-Host "Instalando Permafrost $Version..." -ForegroundColor Cyan

$BaseUrl = "https://github.com/$Repo/releases/download/$Version"
$TmpDir  = [System.IO.Path]::GetTempPath() + "permafrost_$([System.Guid]::NewGuid().ToString('N').Substring(0,8))"
New-Item -ItemType Directory -Path $TmpDir -Force | Out-Null

$TmpBin = "$TmpDir\$BinaryName"
$TmpSha = "$TmpDir\$AssetName.sha256"

# ── baixar binário ────────────────────────────────────────────────────────────

Write-Host "Baixando $AssetName..." -ForegroundColor Gray
Invoke-WebRequest "$BaseUrl/$AssetName" -OutFile $TmpBin -UseBasicParsing

# ── verificar SHA-256 ─────────────────────────────────────────────────────────

if ($env:NO_VERIFY -ne "1") {
    Write-Host "Verificando SHA-256..." -ForegroundColor Gray
    Invoke-WebRequest "$BaseUrl/$AssetName.sha256" -OutFile $TmpSha -UseBasicParsing
    $Expected = (Get-Content $TmpSha).Split(" ")[0].Trim()
    $Actual   = (Get-FileHash $TmpBin -Algorithm SHA256).Hash.ToLower()
    if ($Expected.ToLower() -ne $Actual) {
        Write-Error "SHA-256 invalido! O download pode estar corrompido."
        Remove-Item $TmpDir -Recurse -Force
        exit 1
    }
    Write-Host "SHA-256 verificado" -ForegroundColor Green
}

# ── instalar ──────────────────────────────────────────────────────────────────

New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
Copy-Item $TmpBin "$InstallDir\$BinaryName" -Force
Remove-Item $TmpDir -Recurse -Force

# ── adicionar ao PATH (sessão atual + perfil do usuário) ─────────────────────

$UserPath = [System.Environment]::GetEnvironmentVariable("PATH", "User")
if ($UserPath -notlike "*$InstallDir*") {
    [System.Environment]::SetEnvironmentVariable(
        "PATH",
        "$UserPath;$InstallDir",
        "User"
    )
    $env:PATH = "$env:PATH;$InstallDir"
    Write-Host "Adicionado ao PATH: $InstallDir" -ForegroundColor Gray
}

# ── verificar instalação ──────────────────────────────────────────────────────

Write-Host ""
Write-Host "Permafrost $Version instalado em $InstallDir\$BinaryName" -ForegroundColor Green
Write-Host ""
Write-Host "Uso:" -ForegroundColor Cyan
Write-Host "  permafrost freeze dados.csv"
Write-Host "  permafrost thaw  dados.permafrost"
Write-Host "  permafrost audit dados.permafrost"
Write-Host "  permafrost --help"
Write-Host ""
Write-Host "Obs: reinicie o terminal para que o PATH seja reconhecido." -ForegroundColor Yellow
