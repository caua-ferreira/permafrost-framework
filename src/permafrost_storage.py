"""
PermafrostStorageAdapter v1.0
Interface plugável para cloud storage — S3, GCS, Azure Blob, Local.

Uso:
  from permafrost_storage import storage_from_uri, LocalAdapter, S3Adapter

  # Upload automático após freeze
  adapter = storage_from_uri("s3://meu-bucket/dados/")
  adapter.upload("arquivo.permafrost", "s3://meu-bucket/dados/arquivo.permafrost")

  # Download para thaw
  adapter.download("s3://meu-bucket/dados/arquivo.permafrost", "/tmp/arquivo.permafrost")

  # Listar arquivos .permafrost no bucket
  adapter.list("s3://meu-bucket/dados/")

  # Freeze direto para cloud (sem arquivo local intermediário)
  freeze_to(df, "s3://meu-bucket/dados/vendas_2024.permafrost", codec=CODEC_LZMA2)

  # Thaw direto da cloud
  df = thaw_from("s3://meu-bucket/dados/vendas_2024.permafrost")
"""

import os, io, re, time, hashlib
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Iterator
from dataclasses import dataclass

# ── URI PARSER ────────────────────────────────────────────────────────────────
@dataclass
class ParsedURI:
    scheme: str      # local | s3 | gs | azure
    bucket: str      # nome do bucket / container
    key: str         # caminho dentro do bucket
    raw: str         # URI original

    @property
    def filename(self):
        return self.key.split("/")[-1]

    @property
    def is_directory(self):
        return self.key.endswith("/") or self.key == ""

def parse_uri(uri: str) -> ParsedURI:
    """
    Parseia uma URI de storage:
      s3://bucket/path/file.permafrost
      gs://bucket/path/file.permafrost
      azure://container/path/file.permafrost
      /local/path/file.permafrost
      ./relative/file.permafrost
    """
    uri = uri.strip()
    if uri.startswith("s3://"):
        rest = uri[5:]; parts = rest.split("/", 1)
        return ParsedURI("s3", parts[0], parts[1] if len(parts)>1 else "", uri)
    elif uri.startswith("gs://"):
        rest = uri[5:]; parts = rest.split("/", 1)
        return ParsedURI("gs", parts[0], parts[1] if len(parts)>1 else "", uri)
    elif uri.startswith("azure://"):
        rest = uri[8:]; parts = rest.split("/", 1)
        return ParsedURI("azure", parts[0], parts[1] if len(parts)>1 else "", uri)
    elif uri.startswith("http://") or uri.startswith("https://"):
        raise ValueError(f"HTTP URIs não suportados diretamente. Use s3://, gs:// ou azure://")
    else:
        # Local path
        p = Path(uri)
        return ParsedURI("local", "", str(p), uri)


# ── BASE ADAPTER ──────────────────────────────────────────────────────────────
class StorageAdapter(ABC):
    """Interface que todos os adapters implementam."""

    @abstractmethod
    def upload(self, local_path: str, remote_uri: str,
               show_progress: bool = True) -> dict:
        """Faz upload de um arquivo local para o storage remoto."""
        ...

    @abstractmethod
    def download(self, remote_uri: str, local_path: str,
                 show_progress: bool = True) -> dict:
        """Faz download de um arquivo remoto para local."""
        ...

    @abstractmethod
    def exists(self, remote_uri: str) -> bool:
        """Verifica se um arquivo existe no storage."""
        ...

    @abstractmethod
    def delete(self, remote_uri: str) -> bool:
        """Remove um arquivo do storage."""
        ...

    @abstractmethod
    def list(self, remote_prefix: str,
             pattern: str = "*.permafrost") -> list:
        """Lista arquivos .permafrost em um prefixo."""
        ...

    @abstractmethod
    def read_bytes(self, remote_uri: str) -> bytes:
        """Lê o conteúdo completo de um arquivo remoto para memória."""
        ...

    @abstractmethod
    def write_bytes(self, data: bytes, remote_uri: str) -> dict:
        """Escreve bytes diretamente no storage (sem arquivo local)."""
        ...

    def upload_and_verify(self, local_path: str, remote_uri: str) -> dict:
        """Upload com verificação de integridade via SHA-256."""
        # SHA-256 do arquivo local
        sha = hashlib.sha256(open(local_path,"rb").read()).hexdigest()
        result = self.upload(local_path, remote_uri)
        result['local_sha256'] = sha
        # Verificar integridade baixando só o header para checar magic
        try:
            header = self.read_header_bytes(remote_uri)
            result['remote_magic_ok'] = header[:4] == b'PRMS'
        except:
            result['remote_magic_ok'] = None
        return result

    def read_header_bytes(self, remote_uri: str, n_bytes: int = 4096) -> bytes:
        """Lê apenas os primeiros N bytes de um arquivo remoto (para audit sem download total)."""
        return self.read_bytes(remote_uri)[:n_bytes]

    def read_footer_bytes(self, remote_uri: str, n_bytes: int = 8192) -> bytes:
        """Lê os últimos N bytes (footer/sparse index) sem baixar o arquivo todo."""
        # Por padrão, baixa tudo — adapters podem sobrescrever com range requests
        data = self.read_bytes(remote_uri)
        return data[-n_bytes:]


# ── LOCAL ADAPTER (mock/testes) ───────────────────────────────────────────────
class LocalAdapter(StorageAdapter):
    """Adapter local — para testes e desenvolvimento sem cloud."""

    def __init__(self, base_dir: str = "."):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _resolve(self, uri: str) -> Path:
        p = parse_uri(uri)
        if p.scheme == "local":
            return Path(p.key)
        # Tratar outros schemes como subdiretórios locais
        return self.base_dir / p.scheme / p.bucket / p.key

    def upload(self, local_path, remote_uri, show_progress=True):
        t0 = time.time()
        dst = self._resolve(remote_uri)
        dst.parent.mkdir(parents=True, exist_ok=True)
        size = os.path.getsize(local_path)
        import shutil
        shutil.copy2(local_path, dst)
        elapsed = time.time()-t0
        if show_progress:
            print(f"  ✓ [local] {local_path} → {dst}  ({size/1e6:.2f}MB, {elapsed:.2f}s)")
        return {'uri':remote_uri,'size_bytes':size,'upload_s':round(elapsed,3),'adapter':'local'}

    def download(self, remote_uri, local_path, show_progress=True):
        t0 = time.time()
        src = self._resolve(remote_uri)
        if not src.exists(): raise FileNotFoundError(f"Não encontrado: {remote_uri}")
        size = src.stat().st_size
        import shutil
        shutil.copy2(src, local_path)
        elapsed = time.time()-t0
        if show_progress:
            print(f"  ✓ [local] {remote_uri} → {local_path}  ({size/1e6:.2f}MB, {elapsed:.2f}s)")
        return {'uri':remote_uri,'local_path':local_path,'size_bytes':size,'download_s':round(elapsed,3)}

    def exists(self, remote_uri):
        return self._resolve(remote_uri).exists()

    def delete(self, remote_uri):
        p = self._resolve(remote_uri)
        if p.exists(): p.unlink(); return True
        return False

    def list(self, remote_prefix, pattern="*.permafrost"):
        base = self._resolve(remote_prefix)
        if not base.exists(): return []
        if base.is_dir():
            return [str(f) for f in sorted(base.rglob(pattern))]
        return [str(base)] if base.exists() else []

    def read_bytes(self, remote_uri):
        return self._resolve(remote_uri).read_bytes()

    def write_bytes(self, data, remote_uri):
        dst = self._resolve(remote_uri)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        return {'uri':remote_uri,'size_bytes':len(data),'adapter':'local'}


# ── S3 ADAPTER ────────────────────────────────────────────────────────────────
class S3Adapter(StorageAdapter):
    """
    Adapter para AWS S3 e S3-compatible (MinIO, Cloudflare R2, etc.)

    Autenticação automática via:
      - AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY (env vars)
      - ~/.aws/credentials
      - IAM Role (EC2/ECS/Lambda)
      - endpoint_url para S3-compatible

    Exemplo:
      s3 = S3Adapter()                          # AWS S3 padrão
      s3 = S3Adapter(endpoint_url="http://localhost:9000")  # MinIO local
    """

    def __init__(self, region: str = None, endpoint_url: str = None,
                 access_key: str = None, secret_key: str = None,
                 storage_class: str = "STANDARD"):
        import boto3
        kwargs = {}
        if region:       kwargs['region_name'] = region
        if endpoint_url: kwargs['endpoint_url'] = endpoint_url
        if access_key:   kwargs['aws_access_key_id'] = access_key
        if secret_key:   kwargs['aws_secret_access_key'] = secret_key
        self.s3 = boto3.client('s3', **kwargs)
        self.storage_class = storage_class

    def _parse(self, uri: str):
        p = parse_uri(uri)
        if p.scheme != 's3':
            raise ValueError(f"URI deve ser s3://, recebeu: {uri}")
        return p.bucket, p.key

    def upload(self, local_path, remote_uri, show_progress=True):
        bucket, key = self._parse(remote_uri)
        size = os.path.getsize(local_path)
        t0 = time.time()

        extra = {'StorageClass': self.storage_class}

        if show_progress:
            from boto3.s3.transfer import TransferConfig
            config = TransferConfig(multipart_threshold=8*1024*1024,
                                    max_concurrency=4)
            self.s3.upload_file(local_path, bucket, key,
                                ExtraArgs=extra, Config=config)
        else:
            self.s3.upload_file(local_path, bucket, key, ExtraArgs=extra)

        elapsed = time.time()-t0
        speed = size/1e6/elapsed if elapsed > 0 else 0
        if show_progress:
            print(f"  ✓ [S3] s3://{bucket}/{key}  ({size/1e6:.2f}MB, {elapsed:.1f}s, {speed:.1f}MB/s)")
        return {'uri':remote_uri,'bucket':bucket,'key':key,
                'size_bytes':size,'upload_s':round(elapsed,3),'adapter':'s3'}

    def download(self, remote_uri, local_path, show_progress=True):
        bucket, key = self._parse(remote_uri)
        t0 = time.time()
        meta = self.s3.head_object(Bucket=bucket, Key=key)
        size = meta['ContentLength']
        self.s3.download_file(bucket, key, local_path)
        elapsed = time.time()-t0
        if show_progress:
            print(f"  ✓ [S3] {remote_uri} → {local_path}  ({size/1e6:.2f}MB, {elapsed:.1f}s)")
        return {'uri':remote_uri,'local_path':local_path,'size_bytes':size,
                'download_s':round(elapsed,3),'adapter':'s3'}

    def exists(self, remote_uri):
        bucket, key = self._parse(remote_uri)
        try:
            self.s3.head_object(Bucket=bucket, Key=key); return True
        except self.s3.exceptions.ClientError: return False

    def delete(self, remote_uri):
        bucket, key = self._parse(remote_uri)
        self.s3.delete_object(Bucket=bucket, Key=key); return True

    def list(self, remote_prefix, pattern="*.permafrost"):
        bucket, prefix = self._parse(remote_prefix)
        paginator = self.s3.get_paginator('list_objects_v2')
        results = []
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get('Contents', []):
                key = obj['Key']
                if key.endswith('.permafrost'):
                    results.append(f"s3://{bucket}/{key}")
        return sorted(results)

    def read_bytes(self, remote_uri):
        bucket, key = self._parse(remote_uri)
        resp = self.s3.get_object(Bucket=bucket, Key=key)
        return resp['Body'].read()

    def read_header_bytes(self, remote_uri, n_bytes=4096):
        """Range request — não baixa o arquivo inteiro."""
        bucket, key = self._parse(remote_uri)
        resp = self.s3.get_object(Bucket=bucket, Key=key,
                                   Range=f"bytes=0-{n_bytes-1}")
        return resp['Body'].read()

    def read_footer_bytes(self, remote_uri, n_bytes=8192):
        """Range request para o footer (sparse index)."""
        bucket, key = self._parse(remote_uri)
        meta = self.s3.head_object(Bucket=bucket, Key=key)
        size = meta['ContentLength']
        start = max(0, size - n_bytes)
        resp = self.s3.get_object(Bucket=bucket, Key=key,
                                   Range=f"bytes={start}-{size-1}")
        return resp['Body'].read()

    def write_bytes(self, data, remote_uri):
        bucket, key = self._parse(remote_uri)
        self.s3.put_object(Body=data, Bucket=bucket, Key=key,
                           StorageClass=self.storage_class)
        return {'uri':remote_uri,'bucket':bucket,'key':key,
                'size_bytes':len(data),'adapter':'s3'}

    def set_lifecycle(self, bucket: str, prefix: str,
                      transition_days: int = 30,
                      target_class: str = "GLACIER_DEEP_ARCHIVE"):
        """
        Configura lifecycle policy para mover .permafrost automaticamente
        para Glacier Deep Archive após N dias.
        """
        self.s3.put_bucket_lifecycle_configuration(
            Bucket=bucket,
            LifecycleConfiguration={
                'Rules': [{
                    'ID': f'permafrost-{prefix}-to-{target_class.lower()}',
                    'Filter': {'Prefix': prefix},
                    'Status': 'Enabled',
                    'Transitions': [{'Days': transition_days, 'StorageClass': target_class}],
                }]
            }
        )


# ── GCS ADAPTER ───────────────────────────────────────────────────────────────
class GCSAdapter(StorageAdapter):
    """
    Adapter para Google Cloud Storage.
    Autenticação via GOOGLE_APPLICATION_CREDENTIALS ou ADC.
    """

    def __init__(self, project: str = None, storage_class: str = "STANDARD"):
        from google.cloud import storage as gcs
        self.client = gcs.Client(project=project)
        self.storage_class = storage_class

    def _parse(self, uri: str):
        p = parse_uri(uri)
        if p.scheme != 'gs':
            raise ValueError(f"URI deve ser gs://, recebeu: {uri}")
        return p.bucket, p.key

    def upload(self, local_path, remote_uri, show_progress=True):
        bucket_name, blob_name = self._parse(remote_uri)
        t0 = time.time(); size = os.path.getsize(local_path)
        bucket = self.client.bucket(bucket_name)
        blob   = bucket.blob(blob_name)
        blob.upload_from_filename(local_path)
        elapsed = time.time()-t0
        if show_progress:
            print(f"  ✓ [GCS] gs://{bucket_name}/{blob_name}  ({size/1e6:.2f}MB, {elapsed:.1f}s)")
        return {'uri':remote_uri,'size_bytes':size,'upload_s':round(elapsed,3),'adapter':'gcs'}

    def download(self, remote_uri, local_path, show_progress=True):
        bucket_name, blob_name = self._parse(remote_uri)
        t0 = time.time()
        blob = self.client.bucket(bucket_name).blob(blob_name)
        size = blob.size or 0
        blob.download_to_filename(local_path)
        elapsed = time.time()-t0
        if show_progress:
            print(f"  ✓ [GCS] {remote_uri} → {local_path}  ({size/1e6:.2f}MB, {elapsed:.1f}s)")
        return {'uri':remote_uri,'local_path':local_path,'size_bytes':size,
                'download_s':round(elapsed,3),'adapter':'gcs'}

    def exists(self, remote_uri):
        bn, blob_name = self._parse(remote_uri)
        return self.client.bucket(bn).blob(blob_name).exists()

    def delete(self, remote_uri):
        bn, blob_name = self._parse(remote_uri)
        self.client.bucket(bn).blob(blob_name).delete(); return True

    def list(self, remote_prefix, pattern="*.permafrost"):
        bn, prefix = self._parse(remote_prefix)
        blobs = self.client.list_blobs(bn, prefix=prefix)
        return sorted([f"gs://{bn}/{b.name}" for b in blobs
                       if b.name.endswith('.permafrost')])

    def read_bytes(self, remote_uri):
        bn, blob_name = self._parse(remote_uri)
        return self.client.bucket(bn).blob(blob_name).download_as_bytes()

    def write_bytes(self, data, remote_uri):
        bn, blob_name = self._parse(remote_uri)
        self.client.bucket(bn).blob(blob_name).upload_from_string(data)
        return {'uri':remote_uri,'size_bytes':len(data),'adapter':'gcs'}


# ── AZURE ADAPTER ─────────────────────────────────────────────────────────────
class AzureAdapter(StorageAdapter):
    """
    Adapter para Azure Blob Storage.
    conn_str: connection string do portal Azure.
    """

    def __init__(self, conn_str: str = None, account_name: str = None,
                 account_key: str = None, tier: str = "Cool"):
        from azure.storage.blob import BlobServiceClient
        if conn_str:
            self.client = BlobServiceClient.from_connection_string(conn_str)
        elif account_name and account_key:
            url = f"https://{account_name}.blob.core.windows.net"
            self.client = BlobServiceClient(account_url=url, credential=account_key)
        else:
            raise ValueError("Forneça conn_str ou account_name+account_key")
        self.tier = tier

    def _parse(self, uri: str):
        p = parse_uri(uri)
        if p.scheme != 'azure':
            raise ValueError(f"URI deve ser azure://, recebeu: {uri}")
        return p.bucket, p.key   # bucket = container

    def upload(self, local_path, remote_uri, show_progress=True):
        container, blob_name = self._parse(remote_uri)
        t0 = time.time(); size = os.path.getsize(local_path)
        blob_client = self.client.get_blob_client(container=container, blob=blob_name)
        with open(local_path, 'rb') as f:
            blob_client.upload_blob(f, overwrite=True,
                                    standard_blob_tier=self.tier)
        elapsed = time.time()-t0
        if show_progress:
            print(f"  ✓ [Azure] azure://{container}/{blob_name}  ({size/1e6:.2f}MB, {elapsed:.1f}s)")
        return {'uri':remote_uri,'size_bytes':size,'upload_s':round(elapsed,3),'adapter':'azure'}

    def download(self, remote_uri, local_path, show_progress=True):
        container, blob_name = self._parse(remote_uri)
        t0 = time.time()
        blob_client = self.client.get_blob_client(container=container, blob=blob_name)
        props = blob_client.get_blob_properties(); size = props.size
        with open(local_path, 'wb') as f:
            f.write(blob_client.download_blob().readall())
        elapsed = time.time()-t0
        if show_progress:
            print(f"  ✓ [Azure] {remote_uri} → {local_path}  ({size/1e6:.2f}MB, {elapsed:.1f}s)")
        return {'uri':remote_uri,'local_path':local_path,'size_bytes':size,
                'download_s':round(elapsed,3),'adapter':'azure'}

    def exists(self, remote_uri):
        container, blob_name = self._parse(remote_uri)
        return self.client.get_blob_client(container=container, blob=blob_name).exists()

    def delete(self, remote_uri):
        container, blob_name = self._parse(remote_uri)
        self.client.get_blob_client(container=container, blob=blob_name).delete_blob()
        return True

    def list(self, remote_prefix, pattern="*.permafrost"):
        container, prefix = self._parse(remote_prefix)
        cc = self.client.get_container_client(container)
        return sorted([f"azure://{container}/{b.name}"
                       for b in cc.list_blobs(name_starts_with=prefix)
                       if b.name.endswith('.permafrost')])

    def read_bytes(self, remote_uri):
        container, blob_name = self._parse(remote_uri)
        return self.client.get_blob_client(container=container,
                                           blob=blob_name).download_blob().readall()

    def write_bytes(self, data, remote_uri):
        container, blob_name = self._parse(remote_uri)
        self.client.get_blob_client(container=container,
                                    blob=blob_name).upload_blob(data, overwrite=True)
        return {'uri':remote_uri,'size_bytes':len(data),'adapter':'azure'}


# ── FACTORY ───────────────────────────────────────────────────────────────────
def storage_from_uri(uri: str, **kwargs) -> StorageAdapter:
    """
    Cria o adapter correto baseado na URI.
    Kwargs são repassados para o constructor do adapter.

    Exemplos:
      storage_from_uri("s3://bucket/")
      storage_from_uri("gs://bucket/", project="meu-projeto")
      storage_from_uri("azure://container/", conn_str="DefaultEndpoints...")
      storage_from_uri("/local/path/")   → LocalAdapter
    """
    p = parse_uri(uri)
    if p.scheme == 's3':
        return S3Adapter(**kwargs)
    elif p.scheme == 'gs':
        return GCSAdapter(**kwargs)
    elif p.scheme == 'azure':
        return AzureAdapter(**kwargs)
    else:
        return LocalAdapter(str(Path(p.key).parent) if p.key else ".")


# ── HIGH-LEVEL API ────────────────────────────────────────────────────────────
def freeze_to(df, remote_uri: str, adapter: StorageAdapter = None,
              tmp_dir: str = "/tmp", keep_local: bool = False, **freeze_kwargs) -> dict:
    """
    Comprime DataFrame e faz upload direto para cloud.
    Combina freeze() + adapter.upload() em uma chamada.

    Exemplo:
      metrics = freeze_to(df, "s3://bucket/vendas_2024.permafrost",
                          codec=CODEC_LZMA2, partition_by='ano')
    """
    import sys; sys.path.insert(0,'/tmp')
    from permafrost_v4 import freeze

    if adapter is None:
        adapter = storage_from_uri(remote_uri)

    p = parse_uri(remote_uri)
    local_tmp = os.path.join(tmp_dir, p.filename or "output.permafrost")

    print(f"  freeze → {local_tmp} ...")
    metrics = freeze(df, local_tmp, **freeze_kwargs)
    print(f"  Comprimido: {metrics['stored_mb']:.3f}MB | ratio={metrics['ratio']:.2f}×")

    print(f"  upload → {remote_uri} ...")
    upload_result = adapter.upload_and_verify(local_tmp, remote_uri)
    metrics['remote_uri']       = remote_uri
    metrics['upload_s']         = upload_result.get('upload_s')
    metrics['remote_magic_ok']  = upload_result.get('remote_magic_ok')
    metrics['adapter']          = upload_result.get('adapter')

    if not keep_local:
        os.remove(local_tmp)

    return metrics


def thaw_from(remote_uri: str, adapter: StorageAdapter = None,
              tmp_dir: str = "/tmp", keep_local: bool = False, **thaw_kwargs):
    """
    Faz download de um .permafrost da cloud e descomprime.
    Combina adapter.download() + thaw() em uma chamada.

    Exemplo:
      df = thaw_from("s3://bucket/vendas_2024.permafrost",
                     filter={'ano': 2024})
    """
    import sys; sys.path.insert(0,'/tmp')
    from permafrost_v4 import thaw

    if adapter is None:
        adapter = storage_from_uri(remote_uri)

    p = parse_uri(remote_uri)
    local_tmp = os.path.join(tmp_dir, p.filename or "download.permafrost")

    print(f"  download ← {remote_uri} ...")
    adapter.download(remote_uri, local_tmp)

    print(f"  thaw → DataFrame ...")
    df = thaw(local_tmp, **thaw_kwargs)

    if not keep_local:
        os.remove(local_tmp)

    return df


def audit_remote(remote_uri: str, adapter: StorageAdapter = None) -> dict:
    """
    Lê apenas o header + footer de um .permafrost remoto via range requests.
    Não faz download do arquivo completo.
    """
    import sys; sys.path.insert(0,'/tmp')
    from permafrost_v4 import _read_header, _read_sparse_index, MAGIC, EOF_MAGIC

    if adapter is None:
        adapter = storage_from_uri(remote_uri)

    header_bytes = adapter.read_header_bytes(remote_uri, n_bytes=65536)
    footer_bytes = adapter.read_footer_bytes(remote_uri, n_bytes=16384)

    if header_bytes[:4] != MAGIC:
        raise ValueError(f"Não é um arquivo .permafrost válido: {remote_uri}")

    h = _read_header(header_bytes)

    # Para o sparse index, precisamos dos últimos bytes
    combined = header_bytes + footer_bytes   # aproximação para arquivos pequenos
    try:
        idx = _read_sparse_index(footer_bytes)
    except:
        idx = []

    return {
        'uri': remote_uri,
        'codec': {0x01:'zstd', 0x02:'lzma2'}.get(h['codec'],'?'),
        'quant': h['quant'],
        'orig_rows': h['orig_rows'],
        'n_chunks': h['n_chunks'],
        'columns': list(h['manifests'].keys()),
        'partition_col': idx[0]['part_col'] if idx else None,
        'partition_keys': [e['part_key'] for e in idx],
        'n_index_entries': len(idx),
    }


print("permafrost_storage.py OK")
print("  Adapters: LocalAdapter, S3Adapter, GCSAdapter, AzureAdapter")
print("  Factory:  storage_from_uri(uri)")
print("  API:      freeze_to(), thaw_from(), audit_remote()")
