#!/usr/bin/env sh
# install.sh — instala o binário standalone do Permafrost
#
# Uso rápido:
#   curl -fsSL https://raw.githubusercontent.com/caua-ferreira/permafrost-framework/main/scripts/install.sh | sh
#
# Ou especificando versão:
#   VERSION=v0.7.0 curl -fsSL ... | sh
#
# Variáveis de ambiente:
#   VERSION      — versão a instalar (padrão: latest release)
#   INSTALL_DIR  — diretório de instalação (padrão: /usr/local/bin)
#   NO_VERIFY    — set para 1 para pular verificação SHA-256

set -e

REPO="caua-ferreira/permafrost-framework"
INSTALL_DIR="${INSTALL_DIR:-/usr/local/bin}"
BINARY_NAME="permafrost"

# ── detectar plataforma ───────────────────────────────────────────────────────

OS="$(uname -s)"
ARCH="$(uname -m)"

case "$OS" in
  Linux)
    case "$ARCH" in
      x86_64) ASSET="permafrost-linux-x86_64" ;;
      aarch64|arm64) ASSET="permafrost-linux-arm64" ;;
      *) echo "❌ Arquitetura não suportada: $ARCH" >&2; exit 1 ;;
    esac
    ;;
  Darwin)
    ASSET="permafrost-macos-arm64"
    ;;
  *)
    echo "❌ Sistema operacional não suportado: $OS" >&2
    echo "   Windows: use install.ps1 ou baixe manualmente em:"
    echo "   https://github.com/$REPO/releases"
    exit 1
    ;;
esac

# ── resolver versão ───────────────────────────────────────────────────────────

if [ -z "$VERSION" ]; then
  echo "🔍 Verificando última versão..."
  VERSION="$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" \
    | grep '"tag_name"' | cut -d'"' -f4)"
  if [ -z "$VERSION" ]; then
    echo "❌ Não foi possível obter a versão mais recente. Use VERSION=v0.x.0 para especificar." >&2
    exit 1
  fi
fi

echo "📦 Instalando Permafrost $VERSION ($ASSET)..."

# ── baixar binário ────────────────────────────────────────────────────────────

BASE_URL="https://github.com/$REPO/releases/download/$VERSION"
TMP_DIR="$(mktemp -d)"
TMP_BIN="$TMP_DIR/$BINARY_NAME"
TMP_SHA="$TMP_DIR/$ASSET.sha256"

curl -fsSL "$BASE_URL/$ASSET" -o "$TMP_BIN"

# ── verificar SHA-256 ─────────────────────────────────────────────────────────

if [ "${NO_VERIFY:-0}" != "1" ]; then
  curl -fsSL "$BASE_URL/$ASSET.sha256" -o "$TMP_SHA"
  EXPECTED="$(cut -d' ' -f1 "$TMP_SHA")"
  if command -v sha256sum >/dev/null 2>&1; then
    ACTUAL="$(sha256sum "$TMP_BIN" | cut -d' ' -f1)"
  else
    ACTUAL="$(shasum -a 256 "$TMP_BIN" | cut -d' ' -f1)"
  fi
  if [ "$EXPECTED" != "$ACTUAL" ]; then
    echo "❌ SHA-256 inválido! O download pode estar corrompido." >&2
    rm -rf "$TMP_DIR"
    exit 1
  fi
  echo "✓ SHA-256 verificado"
fi

# ── instalar ──────────────────────────────────────────────────────────────────

chmod +x "$TMP_BIN"

if [ -w "$INSTALL_DIR" ]; then
  mv "$TMP_BIN" "$INSTALL_DIR/$BINARY_NAME"
else
  echo "🔐 Requer sudo para instalar em $INSTALL_DIR..."
  sudo mv "$TMP_BIN" "$INSTALL_DIR/$BINARY_NAME"
fi

rm -rf "$TMP_DIR"

# ── verificar instalação ──────────────────────────────────────────────────────

if command -v permafrost >/dev/null 2>&1; then
  echo "✅ Permafrost $VERSION instalado em $INSTALL_DIR/$BINARY_NAME"
  echo ""
  echo "   Uso:"
  echo "     permafrost freeze dados.csv"
  echo "     permafrost thaw  dados.permafrost"
  echo "     permafrost audit dados.permafrost"
  echo "     permafrost --help"
else
  echo "✅ Binário instalado em $INSTALL_DIR/$BINARY_NAME"
  echo "   (adicione $INSTALL_DIR ao seu PATH se necessário)"
fi
