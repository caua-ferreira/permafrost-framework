"""
permafrost.catalog_backends — Backend abstraction for PermafrostCatalog
=======================================================================

Backends disponíveis:

- LocalCatalogBackend  — padrão, sistema de arquivos local
- S3CatalogBackend     — Amazon S3 com cache local transparente (requires boto3)
- GCSCatalogBackend    — Google Cloud Storage (requires google-cloud-storage)
- AzureCatalogBackend  — Azure Blob Storage (requires azure-storage-blob)

Uso::

    from permafrost.catalog_backends import S3CatalogBackend

    backend = S3CatalogBackend(
        bucket="minha-empresa-data",
        prefix="permafrost/",
        region="us-east-1",
        cache_ttl=3600,
        max_cache_size_gb=10,
    )
    cat = pf.PermafrostCatalog(".catalog.db", backend=backend)
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import shutil
import time
from abc import ABC, abstractmethod
from typing import Optional

_DEFAULT_CACHE_DIR = os.path.expanduser("~/.permafrost/cache")

_REMOTE_SCHEMES = ("s3://", "gs://", "az://")


def _is_remote(path: str) -> bool:
    return any(path.startswith(s) for s in _REMOTE_SCHEMES)


class CatalogBackend(ABC):
    """Interface de backend para resolução de paths e storage remoto."""

    @abstractmethod
    def resolve_path(self, path: str) -> str:
        """Retorna um path localmente legível.

        Para paths locais retorna o path absoluto.
        Para URIs remotas (s3://, gs://, az://) baixa para cache local
        e retorna o path do arquivo em cache.
        """

    @abstractmethod
    def upload(self, local_path: str, remote_path: str) -> None:
        """Envia um arquivo local para o destino remoto."""

    def is_remote(self, path: str) -> bool:
        return _is_remote(path)


# ── Local ─────────────────────────────────────────────────────────────────────

class LocalCatalogBackend(CatalogBackend):
    """Backend padrão — sistema de arquivos local. Sem dependências extras."""

    def resolve_path(self, path: str) -> str:
        return os.path.abspath(path)

    def upload(self, local_path: str, remote_path: str) -> None:
        dest_dir = os.path.dirname(os.path.abspath(remote_path))
        if dest_dir:
            os.makedirs(dest_dir, exist_ok=True)
        shutil.copy2(local_path, remote_path)


# ── S3 ────────────────────────────────────────────────────────────────────────

class S3CatalogBackend(CatalogBackend):
    """Backend S3 com cache local transparente por SHA-256/ETag.

    Args:
        bucket: Nome do bucket S3.
        prefix: Prefixo das keys (ex: ``"datasets/"``).
        region: Região AWS (padrão: ``"us-east-1"``).
        cache_dir: Diretório local de cache.  Padrão: ``~/.permafrost/cache``.
        cache_ttl: Segundos antes de revalidar com S3 (padrão: 3600).
        max_cache_size_gb: Limite do cache em GB; evicção LRU quando exceder.
        **boto_kwargs: Argumentos extras para ``boto3.client`` (endpoint_url, etc.).

    Raises:
        ImportError: Se ``boto3`` não estiver instalado.
    """

    def __init__(
        self,
        bucket: str,
        prefix: str = "",
        region: str = "us-east-1",
        cache_dir: Optional[str] = None,
        cache_ttl: int = 3600,
        max_cache_size_gb: float = 5.0,
        **boto_kwargs,
    ):
        try:
            import boto3 as _boto3
        except ImportError:
            raise ImportError(
                "S3 backend requires boto3. Install with: pip install permafrost[s3]"
            )
        self.bucket = bucket
        self.prefix = prefix.rstrip("/")
        self.region = region
        self.cache_dir = os.path.expanduser(cache_dir or _DEFAULT_CACHE_DIR)
        self.cache_ttl = cache_ttl
        self.max_cache_bytes = int(max_cache_size_gb * 1024 ** 3)
        self._s3 = _boto3.client("s3", region_name=region, **boto_kwargs)
        os.makedirs(self.cache_dir, exist_ok=True)

    # ── helpers ───────────────────────────────────────────────────────────────

    def _parse_uri(self, uri: str) -> tuple[str, str]:
        path = uri[5:]  # strip "s3://"
        parts = path.split("/", 1)
        return parts[0], parts[1] if len(parts) > 1 else ""

    def _cache_paths(self, uri: str) -> tuple[str, str]:
        key = hashlib.sha256(uri.encode()).hexdigest()[:24]
        local = os.path.join(self.cache_dir, key + ".permafrost")
        return local, local + ".meta.json"

    def _get_etag(self, bucket: str, key: str) -> str:
        try:
            head = self._s3.head_object(Bucket=bucket, Key=key)
            return head.get("ETag", "").strip('"')
        except Exception:
            return ""

    def _download(self, bucket: str, key: str, uri: str) -> str:
        local_path, meta_path = self._cache_paths(uri)
        self._s3.download_file(bucket, key, local_path)
        etag = self._get_etag(bucket, key)
        with open(meta_path, "w") as f:
            json.dump({"etag": etag, "cached_at": time.time(), "uri": uri}, f)
        self._evict_lru()
        return local_path

    def _is_cache_valid(self, local_path: str, meta_path: str, bucket: str, key: str) -> bool:
        if not (os.path.exists(local_path) and os.path.exists(meta_path)):
            return False
        try:
            with open(meta_path) as f:
                meta = json.load(f)
            if time.time() - meta.get("cached_at", 0) >= self.cache_ttl:
                return False
            return meta.get("etag") == self._get_etag(bucket, key)
        except Exception:
            return False

    def _evict_lru(self) -> None:
        files = [
            (p, os.path.getatime(p), os.path.getsize(p))
            for p in glob.glob(os.path.join(self.cache_dir, "*.permafrost"))
        ]
        files.sort(key=lambda x: x[1])  # oldest atime first
        total = sum(s for _, _, s in files)
        for path, _, size in files:
            if total <= self.max_cache_bytes:
                break
            os.remove(path)
            meta = path + ".meta.json"
            if os.path.exists(meta):
                os.remove(meta)
            total -= size

    # ── public ────────────────────────────────────────────────────────────────

    def resolve_path(self, path: str) -> str:
        if not path.startswith("s3://"):
            return os.path.abspath(path)
        bucket, key = self._parse_uri(path)
        local_path, meta_path = self._cache_paths(path)
        if self._is_cache_valid(local_path, meta_path, bucket, key):
            return local_path
        return self._download(bucket, key, path)

    def upload(self, local_path: str, remote_path: str) -> None:
        if not remote_path.startswith("s3://"):
            raise ValueError(f"Expected s3:// path for S3Backend, got: {remote_path!r}")
        bucket, key = self._parse_uri(remote_path)
        self._s3.upload_file(local_path, bucket, key)


# ── GCS ───────────────────────────────────────────────────────────────────────

class GCSCatalogBackend(CatalogBackend):
    """Backend Google Cloud Storage com cache local transparente.

    Raises:
        ImportError: Se ``google-cloud-storage`` não estiver instalado.
    """

    def __init__(
        self,
        bucket: str,
        prefix: str = "",
        credentials: Optional[str] = None,
        cache_dir: Optional[str] = None,
        cache_ttl: int = 3600,
        max_cache_size_gb: float = 5.0,
    ):
        try:
            from google.cloud import storage as _gcs
        except ImportError:
            raise ImportError(
                "GCS backend requires google-cloud-storage. "
                "Install with: pip install permafrost[gcs]"
            )
        from google.cloud import storage as _gcs
        self.bucket_name = bucket
        self.prefix = prefix.rstrip("/")
        self.cache_dir = os.path.expanduser(cache_dir or _DEFAULT_CACHE_DIR)
        self.cache_ttl = cache_ttl
        self.max_cache_bytes = int(max_cache_size_gb * 1024 ** 3)
        if credentials:
            self._client = _gcs.Client.from_service_account_json(credentials)
        else:
            self._client = _gcs.Client()
        os.makedirs(self.cache_dir, exist_ok=True)

    def _parse_uri(self, uri: str) -> tuple[str, str]:
        path = uri[5:]  # strip "gs://"
        parts = path.split("/", 1)
        return parts[0], parts[1] if len(parts) > 1 else ""

    def _cache_paths(self, uri: str) -> tuple[str, str]:
        key = hashlib.sha256(uri.encode()).hexdigest()[:24]
        local = os.path.join(self.cache_dir, key + ".permafrost")
        return local, local + ".meta.json"

    def resolve_path(self, path: str) -> str:
        if not path.startswith("gs://"):
            return os.path.abspath(path)
        bucket_name, blob_name = self._parse_uri(path)
        local_path, meta_path = self._cache_paths(path)

        if os.path.exists(local_path) and os.path.exists(meta_path):
            try:
                with open(meta_path) as f:
                    meta = json.load(f)
                if time.time() - meta.get("cached_at", 0) < self.cache_ttl:
                    return local_path
            except Exception:
                pass

        bucket = self._client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        blob.download_to_filename(local_path)
        with open(meta_path, "w") as f:
            json.dump({"cached_at": time.time(), "uri": path}, f)
        return local_path

    def upload(self, local_path: str, remote_path: str) -> None:
        if not remote_path.startswith("gs://"):
            raise ValueError(f"Expected gs:// path for GCSBackend, got: {remote_path!r}")
        bucket_name, blob_name = self._parse_uri(remote_path)
        bucket = self._client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        blob.upload_from_filename(local_path)


# ── Azure ─────────────────────────────────────────────────────────────────────

class AzureCatalogBackend(CatalogBackend):
    """Backend Azure Blob Storage com cache local transparente.

    Raises:
        ImportError: Se ``azure-storage-blob`` não estiver instalado.
    """

    def __init__(
        self,
        container: str,
        account_name: Optional[str] = None,
        account_key: Optional[str] = None,
        connection_string: Optional[str] = None,
        cache_dir: Optional[str] = None,
        cache_ttl: int = 3600,
        max_cache_size_gb: float = 5.0,
    ):
        try:
            from azure.storage.blob import BlobServiceClient
        except ImportError:
            raise ImportError(
                "Azure backend requires azure-storage-blob. "
                "Install with: pip install permafrost[azure]"
            )
        from azure.storage.blob import BlobServiceClient
        self.container = container
        self.cache_dir = os.path.expanduser(cache_dir or _DEFAULT_CACHE_DIR)
        self.cache_ttl = cache_ttl
        self.max_cache_bytes = int(max_cache_size_gb * 1024 ** 3)

        if connection_string:
            self._client = BlobServiceClient.from_connection_string(connection_string)
        elif account_name and account_key:
            url = f"https://{account_name}.blob.core.windows.net"
            from azure.core.credentials import AzureNamedKeyCredential
            self._client = BlobServiceClient(
                url, credential=AzureNamedKeyCredential(account_name, account_key)
            )
        else:
            raise ValueError(
                "Provide either connection_string or both account_name and account_key."
            )
        os.makedirs(self.cache_dir, exist_ok=True)

    def _parse_uri(self, uri: str) -> tuple[str, str]:
        path = uri[5:]  # strip "az://"
        parts = path.split("/", 1)
        return parts[0], parts[1] if len(parts) > 1 else ""

    def _cache_paths(self, uri: str) -> tuple[str, str]:
        key = hashlib.sha256(uri.encode()).hexdigest()[:24]
        local = os.path.join(self.cache_dir, key + ".permafrost")
        return local, local + ".meta.json"

    def resolve_path(self, path: str) -> str:
        if not path.startswith("az://"):
            return os.path.abspath(path)
        container, blob_name = self._parse_uri(path)
        local_path, meta_path = self._cache_paths(path)

        if os.path.exists(local_path) and os.path.exists(meta_path):
            try:
                with open(meta_path) as f:
                    meta = json.load(f)
                if time.time() - meta.get("cached_at", 0) < self.cache_ttl:
                    return local_path
            except Exception:
                pass

        blob_client = self._client.get_blob_client(container=container, blob=blob_name)
        with open(local_path, "wb") as f:
            blob_client.download_blob().readinto(f)
        with open(meta_path, "w") as f:
            json.dump({"cached_at": time.time(), "uri": path}, f)
        return local_path

    def upload(self, local_path: str, remote_path: str) -> None:
        if not remote_path.startswith("az://"):
            raise ValueError(f"Expected az:// path for AzureBackend, got: {remote_path!r}")
        container, blob_name = self._parse_uri(remote_path)
        blob_client = self._client.get_blob_client(container=container, blob=blob_name)
        with open(local_path, "rb") as f:
            blob_client.upload_blob(f, overwrite=True)
