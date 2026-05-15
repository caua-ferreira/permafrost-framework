"""
AES-256-GCM encryption layer for .permafrost chunks.

Usage::

    from permafrost.crypto import LocalKeyProvider, AWSKMSProvider, GCPKMSProvider
    from permafrost.crypto import encrypt_chunk, decrypt_chunk

    # Local key
    key = LocalKeyProvider(os.urandom(32))

    # AWS KMS — envelope encryption, EDK stored in the file
    key = AWSKMSProvider(key_id="arn:aws:kms:us-east-1:123456789012:key/…")

    # GCP KMS — envelope encryption, EDK stored in the file
    key = GCPKMSProvider(key_resource_name="projects/p/locations/l/keyRings/r/cryptoKeys/k")
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

    @property
    def encrypted_dek(self) -> bytes:
        """Encrypted Data Encryption Key for envelope encryption.

        Empty bytes for local providers (no envelope encryption).
        KMS providers return the ciphertext produced by the KMS so it can be
        stored in the .permafrost header for later decryption.
        """
        return b""

    def set_encrypted_dek(self, edek: bytes) -> None:
        """Called by thaw() to inject the EDK stored in the file header.

        No-op for local providers. KMS providers override this to pre-populate
        their encrypted DEK so get_key() can call the KMS decrypt path.
        """


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


class AWSKMSProvider(KeyProvider):
    """KeyProvider backed by AWS KMS using envelope encryption.

    On the **freeze path** (no ``encrypted_dek`` supplied): calls
    ``kms.generate_data_key`` to obtain a fresh 32-byte DEK and its
    KMS-encrypted ciphertext (EDK). The EDK is stored in the
    ``.permafrost`` file header so that the file is self-contained.

    On the **thaw path**: ``thaw()`` automatically injects the EDK from
    the file header via :meth:`set_encrypted_dek`, then calls
    ``kms.decrypt`` to recover the plaintext DEK.

    Args:
        key_id: CMK ARN or alias, e.g.
            ``"arn:aws:kms:us-east-1:123456789012:key/…"``.
        region_name: AWS region of the CMK.
        encrypted_dek: Pre-populated encrypted DEK (used internally by
            :func:`resolve_key` when initialising a provider for thaw).

    Requires ``boto3``::

        pip install permafrost-framework[kms]
    """

    def __init__(
        self,
        key_id: str,
        region_name: str = "us-east-1",
        encrypted_dek: bytes | None = None,
    ):
        self._cmk_id = key_id
        self._region = region_name
        self._edek: bytes | None = encrypted_dek
        self._plaintext: bytes | None = None

    def get_key(self) -> bytes:
        if self._plaintext is None:
            try:
                import boto3
            except ImportError:
                raise ImportError(
                    "AWSKMSProvider requires boto3. "
                    "Install with: pip install permafrost-framework[kms]"
                )
            kms = boto3.client("kms", region_name=self._region)
            if self._edek is None:
                resp = kms.generate_data_key(
                    KeyId=self._cmk_id, KeySpec="AES_256"
                )
                self._plaintext = resp["Plaintext"]        # 32 raw bytes
                self._edek = resp["CiphertextBlob"]        # opaque KMS blob
            else:
                resp = kms.decrypt(
                    CiphertextBlob=self._edek,
                    KeyId=self._cmk_id,
                )
                self._plaintext = resp["Plaintext"]
        return self._plaintext

    @property
    def encrypted_dek(self) -> bytes:
        # _edek may not exist yet before get_key() is called
        return self._edek or b""

    def set_encrypted_dek(self, edek: bytes) -> None:
        if edek and not self._edek:
            self._edek = edek

    @property
    def kms_name(self) -> str:
        return "aws-kms"

    def key_id(self) -> str:
        return hashlib.sha256(self._cmk_id.encode()).hexdigest()[:16]


class GCPKMSProvider(KeyProvider):
    """KeyProvider backed by Google Cloud KMS using envelope encryption.

    On the **freeze path** (no ``encrypted_dek`` supplied): generates a
    random 32-byte DEK locally and calls ``client.encrypt`` to produce the
    KMS-encrypted EDK, which is stored in the ``.permafrost`` header.

    On the **thaw path**: ``thaw()`` injects the EDK via
    :meth:`set_encrypted_dek`, then calls ``client.decrypt`` to recover
    the DEK.

    Args:
        key_resource_name: Full KMS resource name, e.g.
            ``"projects/p/locations/global/keyRings/r/cryptoKeys/k"``.
        encrypted_dek: Pre-populated encrypted DEK (used internally by
            :func:`resolve_key` when initialising a provider for thaw).

    Requires ``google-cloud-kms``::

        pip install permafrost-framework[kms]
    """

    def __init__(
        self,
        key_resource_name: str,
        encrypted_dek: bytes | None = None,
    ):
        self._key_name = key_resource_name
        self._edek: bytes | None = encrypted_dek
        self._plaintext: bytes | None = None

    def get_key(self) -> bytes:
        if self._plaintext is None:
            try:
                from google.cloud import kms as gkms
            except ImportError:
                raise ImportError(
                    "GCPKMSProvider requires google-cloud-kms. "
                    "Install with: pip install permafrost-framework[kms]"
                )
            client = gkms.KeyManagementServiceClient()
            if self._edek is None:
                plain = os.urandom(32)
                resp = client.encrypt(
                    request={"name": self._key_name, "plaintext": plain}
                )
                self._plaintext = plain
                self._edek = bytes(resp.ciphertext)
            else:
                resp = client.decrypt(
                    request={"name": self._key_name, "ciphertext": self._edek}
                )
                self._plaintext = bytes(resp.plaintext)[:32]
        return self._plaintext

    @property
    def encrypted_dek(self) -> bytes:
        return self._edek or b""

    def set_encrypted_dek(self, edek: bytes) -> None:
        if edek and not self._edek:
            self._edek = edek

    @property
    def kms_name(self) -> str:
        return "gcp-kms"

    def key_id(self) -> str:
        return hashlib.sha256(self._key_name.encode()).hexdigest()[:16]


def resolve_key(
    key,
    edek: bytes = b"",
) -> tuple[bytes | None, str, str, bytes]:
    """Resolves a key argument to ``(raw_bytes, kms_name, key_id, encrypted_dek)``.

    Accepts:
    - ``None``: checks ``PERMAFROST_KEY`` env var (64-char hex or 32 raw bytes)
    - ``bytes`` / ``bytearray``: wraps in :class:`LocalKeyProvider`
    - :class:`KeyProvider` subclass: used directly

    Args:
        key: Key material or provider.
        edek: Encrypted DEK from the file header (used in the thaw path to
            initialise KMS providers that don't yet have their EDK).

    Returns:
        ``(None, '', '', b'')`` when no key is configured anywhere.
    """
    if key is None:
        env = os.environ.get("PERMAFROST_KEY", "")
        if not env:
            return None, "", "", b""
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

    # Inject the file's stored EDK into KMS providers that need it for thaw
    if edek:
        key.set_encrypted_dek(edek)

    raw_key = key.get_key()
    enc_dek = key.encrypted_dek
    return raw_key, key.kms_name, key.key_id(), enc_dek


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
