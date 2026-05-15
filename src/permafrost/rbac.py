"""
RBAC — I4 feature (v0.8)

JWT HS256 simples sem dependências externas (só stdlib).
Fornece criação/validação de tokens e gerenciamento de usuários do cluster.

Token payload:
  sub (str)         — username
  can_freeze (bool) — permissão de submeter jobs de freeze
  can_thaw   (bool) — permissão de leitura/thaw
  namespace  (str)  — isolamento lógico de recursos
  iat (int)         — issued-at (epoch)
  exp (int)         — expiry (epoch), ausente = nunca expira
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass, asdict
from typing import Optional


class AuthError(Exception):
    """Token ausente, inválido, expirado ou sem a permissão requerida."""


# ── JWT primitivas ────────────────────────────────────────────────────────────

def _b64url_enc(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_dec(s: str) -> bytes:
    rem = len(s) % 4
    if rem:
        s += "=" * (4 - rem)
    return base64.urlsafe_b64decode(s)


def _hs256(header_payload: str, secret: str) -> str:
    sig = hmac.new(secret.encode(), header_payload.encode(), hashlib.sha256).digest()
    return _b64url_enc(sig)


# ── API pública de tokens ─────────────────────────────────────────────────────

def generate_token(
    username: str,
    can_freeze: bool,
    can_thaw: bool,
    namespace: str,
    secret_key: str,
    expires_in: int = 0,
) -> str:
    """Gera um JWT HS256 para o usuário com as permissões fornecidas.

    Args:
        username: Nome do usuário (``sub`` do token).
        can_freeze: Permite submeter jobs de freeze.
        can_thaw: Permite operações de leitura/thaw.
        namespace: Isolamento lógico (ex.: ``"prod"``, ``"staging"``).
        secret_key: Chave HMAC usada para assinar o token.
        expires_in: Segundos até expirar; 0 = nunca expira.

    Returns:
        String JWT no formato ``header.payload.signature``.
    """
    hdr = _b64url_enc(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload: dict = {
        "sub":        username,
        "can_freeze": can_freeze,
        "can_thaw":   can_thaw,
        "namespace":  namespace,
        "iat":        int(time.time()),
    }
    if expires_in > 0:
        payload["exp"] = int(time.time()) + expires_in
    pay = _b64url_enc(json.dumps(payload, separators=(",", ":")).encode())
    sig = _hs256(f"{hdr}.{pay}", secret_key)
    return f"{hdr}.{pay}.{sig}"


def validate_token(token: str, secret_key: str) -> dict:
    """Valida assinatura e expiração; retorna o payload (claims).

    Args:
        token: String JWT (com ou sem prefixo ``Bearer ``).
        secret_key: Chave HMAC.

    Returns:
        Dicionário com os claims do token.

    Raises:
        AuthError: Se o token for malformado, a assinatura inválida ou o token expirado.
    """
    if not token:
        raise AuthError("Token ausente")
    if token.startswith("Bearer "):
        token = token[7:]
    parts = token.split(".")
    if len(parts) != 3:
        raise AuthError("Token malformado")
    hdr_b64, pay_b64, sig = parts
    expected = _hs256(f"{hdr_b64}.{pay_b64}", secret_key)
    if not hmac.compare_digest(sig, expected):
        raise AuthError("Assinatura inválida")
    try:
        payload = json.loads(_b64url_dec(pay_b64))
    except Exception:
        raise AuthError("Payload inválido")
    if "exp" in payload and payload["exp"] <= int(time.time()):
        raise AuthError("Token expirado")
    return payload


# ── RBACManager ───────────────────────────────────────────────────────────────

@dataclass
class ClusterUser:
    """Representa um usuário registrado no cluster."""
    username:   str
    can_freeze: bool
    can_thaw:   bool
    namespace:  str
    created_at: float = 0.0

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = time.time()


class RBACManager:
    """Gerencia usuários e tokens do cluster.

    Args:
        secret_key: Chave HMAC para assinar/verificar JWTs.
    """

    def __init__(self, secret_key: str) -> None:
        if not secret_key:
            raise ValueError("secret_key não pode ser vazio")
        self._secret   = secret_key
        self._users:  dict[str, ClusterUser] = {}

    # ── Gerenciamento de usuários ─────────────────────────────────────────────

    def add_user(
        self,
        username: str,
        can_freeze: bool = False,
        can_thaw:   bool = False,
        namespace:  str  = "default",
        expires_in: int  = 0,
    ) -> str:
        """Cria ou sobrescreve um usuário e retorna seu token JWT.

        Args:
            username: Nome único do usuário.
            can_freeze: Permite submeter jobs de freeze.
            can_thaw: Permite leitura/thaw.
            namespace: Namespace do usuário.
            expires_in: Segundos até expirar (0 = nunca).

        Returns:
            Token JWT do usuário.
        """
        self._users[username] = ClusterUser(
            username=username, can_freeze=can_freeze,
            can_thaw=can_thaw, namespace=namespace,
        )
        return generate_token(username, can_freeze, can_thaw, namespace,
                              self._secret, expires_in)

    def remove_user(self, username: str) -> bool:
        """Remove um usuário. Retorna ``True`` se existia."""
        return self._users.pop(username, None) is not None

    def list_users(self) -> list[dict]:
        """Retorna lista de usuários sem dados sensíveis."""
        return [asdict(u) for u in self._users.values()]

    def verify_admin_key(self, key: str) -> bool:
        """Verifica se a chave fornecida é a chave-mestra do cluster."""
        return hmac.compare_digest(key or "", self._secret)

    # ── Validação de permissões ───────────────────────────────────────────────

    def validate(
        self,
        token: str,
        require_freeze: bool = False,
        require_thaw:   bool = False,
    ) -> dict:
        """Valida token e verifica permissões requeridas.

        Args:
            token: JWT (com ou sem ``Bearer ``).
            require_freeze: Exige ``can_freeze=true`` no token.
            require_thaw: Exige ``can_thaw=true`` OU ``can_freeze=true``.

        Returns:
            Claims do token.

        Raises:
            AuthError: Token inválido ou permissão insuficiente.
        """
        claims = validate_token(token, self._secret)
        if require_freeze and not claims.get("can_freeze"):
            raise AuthError("Requer permissão can_freeze")
        if require_thaw and not claims.get("can_thaw") and not claims.get("can_freeze"):
            raise AuthError("Requer permissão can_thaw ou can_freeze")
        return claims
