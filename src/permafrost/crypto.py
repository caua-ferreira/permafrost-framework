"""
AES-256-GCM encryption layer for .permafrost chunks.

Usage::

    from permafrost.crypto import LocalKeyProvider, encrypt_chunk, decrypt_chunk

    key = LocalKeyProvider(os.urandom(32))
    blob = encrypt_chunk(compressed_data, key.get_key())
    original = decrypt_chunk(blob, key.get_key())
"""
from __future__ import annotations
import hashlib, os
from abc import ABC, abstractmethod

NONCE_SIZE = 12   # GCM standard nonce
TAG_SIZE   = 16   # GCM authentication tag (always 16 bytes)


def _aesgcm():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        return AESGCM
    except ImportError:
        raise ImportError(
            "Encryption requires the 'cryptography' package. "
            "Install with: pip install permafrost-framework[crypto]"
        )


class KeyProvider(ABC):
    """Abstract key provider — extend to integrate with a KMS."""

    @abstractmethod
    def get_key(self) -> bytes:
        """Returns the raw 32-byte AES-256 key."""

    def key_id(self) -> str:
        """Short non-secret fingerprint for key identification in audit()."""
        return hashlib.sha256(self.get_key()).hexdigest()[:16]

    @property
    def kms_name(self) -> str:
        return self.__class__.__name__


class LocalKeyProvider(KeyProvider):
    """Key provider backed by a raw 32-byte key in memory."""

    def __init__(self, key: bytes | bytearray):
        key = bytes(key)
        if len(key) != 32:
            raise ValueError(f"AES-256 key must be exactly 32 bytes, got {len(key)}")
        self._key = key

    def get_key(self) -> bytes:
        return self._key

    @property
    def kms_name(self) -> str:
        return "local"


def resolve_key(
    key: bytes | bytearray | KeyProvider | None,
) -> tuple[bytes | None, str, str]:
    """Resolves a key argument to (raw_bytes, kms_name, key_id).

    Accepts:
    - ``None``: checks ``PERMAFROST_KEY`` env var (64-char hex or 32 raw bytes)
    - ``bytes`` / ``bytearray``: wraps in :class:`LocalKeyProvider`
    - :class:`KeyProvider` subclass: used directly

    Returns ``(None, '', '')`` when no key is configured anywhere.
    """
    if key is None:
        env = os.environ.get("PERMAFROST_KEY", "")
        if not env:
            return None, "", ""
        raw = bytes.fromhex(env) if len(env) == 64 else env.encode("latin-1")
        if len(raw) != 32:
            raise ValueError(
                "PERMAFROST_KEY must be a 32-byte key or a 64-character hex string"
            )
        key = LocalKeyProvider(raw)

    if isinstance(key, (bytes, bytearray)):
        key = LocalKeyProvider(bytes(key))

    if not isinstance(key, KeyProvider):
        raise TypeError(f"key must be bytes or a KeyProvider, got {type(key)}")

    raw_key = key.get_key()
    return raw_key, key.kms_name, key.key_id()


def encrypt_chunk(plaintext: bytes, key: bytes) -> bytes:
    """Encrypts a chunk payload with AES-256-GCM.

    Returns ``nonce(12) + ciphertext + tag(16)``.
    The GCM tag provides both confidentiality and integrity.
    """
    AESGCM = _aesgcm()
    nonce = os.urandom(NONCE_SIZE)
    # AESGCM.encrypt appends the 16-byte tag to the ciphertext
    ct_and_tag = AESGCM(key).encrypt(nonce, plaintext, None)
    return nonce + ct_and_tag


def decrypt_chunk(blob: bytes, key: bytes) -> bytes:
    """Decrypts a chunk encrypted by :func:`encrypt_chunk`.

    Args:
        blob: ``nonce(12) + ciphertext + tag(16)`` as produced by encrypt_chunk.
        key: 32-byte AES-256 key.

    Raises:
        ValueError: If the blob is too short, the key is wrong, or the
            GCM tag fails (data was tampered with).
    """
    if len(blob) < NONCE_SIZE + TAG_SIZE:
        raise ValueError(
            f"Encrypted blob too short: {len(blob)} bytes "
            f"(minimum {NONCE_SIZE + TAG_SIZE})"
        )
    AESGCM = _aesgcm()
    nonce      = blob[:NONCE_SIZE]
    ct_and_tag = blob[NONCE_SIZE:]
    try:
        return AESGCM(key).decrypt(nonce, ct_and_tag, None)
    except Exception as exc:
        raise ValueError(
            "Chunk decryption failed — wrong key or tampered data"
        ) from exc
