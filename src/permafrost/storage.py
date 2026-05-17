"""
PermafrostStorageAdapter v1.0
Interface plugável para cloud storage — S3, GCS, Azure Blob, Local.

Uso:
  adapter = storage_from_uri("s3://meu-bucket/dados/")
  adapter.upload("arquivo.permafrost", "s3://meu-bucket/dados/arquivo.permafrost")
  df = thaw_from("s3://meu-bucket/dados/vendas.permafrost", filter={"ano": 2024})
  metrics = freeze_to(df, "s3://meu-bucket/dados/vendas.permafrost", codec=CODEC_LZMA2)
"""

import os, io, re, time, hashlib, json, math, random
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Iterator, Tuple
from dataclasses import dataclass

# ── C4: RESUMABLE UPLOAD PRIMITIVES ──────────────────────────────────────────

class ResumableUploadError(Exception):
    """Raised when an upload fails after all retries are exhausted."""


def _retry(fn, max_retries: int = 3, max_delay: float = 60.0):
    """Call fn() up to max_retries times with exponential backoff + jitter."""
    for attempt in range(max_retries):
        try:
            return fn()
        except ResumableUploadError:
            raise
        except Exception as exc:
            if attempt == max_retries - 1:
                raise ResumableUploadError(
                    f"Falhou após {max_retries} tentativas: {exc}"
                ) from exc
            delay = min(max_delay, (2 ** attempt) + random.uniform(0, 1))
            time.sleep(delay)


def _state_path(local_path: str, state_file: Optional[str] = None) -> str:
    if state_file is not None:
        return state_file
    return local_path + ".upload_state"


def _load_state(sf: str) -> Optional[dict]:
    try:
        with open(sf, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _save_state(sf: str, state: dict) -> None:
    with open(sf, 'w', encoding='utf-8') as f:
        json.dump(state, f)


def _clear_state(sf: str) -> None:
    try:
        os.remove(sf)
    except FileNotFoundError:
        pass


# ── URI PARSER ────────────────────────────────────────────────────────────────
@dataclass
class ParsedURI:
    scheme: str      # local | s3 | gs | azure
    bucket: str      # nome do bucket / container
    key: str         # caminho dentro do bucket
    raw: str         # URI original

    @property
    def filename(self) -> str:
        return Path(self.key).name if self.key else ""

    @property
    def is_directory(self) -> bool:
        return self.key.endswith("/") or self.key == ""

def parse_uri(uri: str) -> ParsedURI:
    """
    Parseia uma URI de storage em scheme, bucket e key.

    Exemplos::

        parse_uri("s3://bucket/path/file.permafrost")
        parse_uri("gs://bucket/path/file.permafrost")
        parse_uri("azure://container/path/file.permafrost")
        parse_uri("/local/path/file.permafrost")
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
        raise ValueError("HTTP URIs não suportados diretamente. Use s3://, gs:// ou azure://")
    else:
        p = Path(uri)
        return ParsedURI("local", "", str(p), uri)


# ── BASE ADAPTER ──────────────────────────────────────────────────────────────
class StorageAdapter(ABC):
    """Interface que todos os adapters implementam."""

    @abstractmethod
    def upload(self, local_path: str, remote_uri: str,
               show_progress: bool = True) -> dict:
        """Faz upload de um arquivo local para o storage remoto.

        Args:
            local_path: Caminho local do arquivo a enviar.
            remote_uri: URI de destino (s3://, gs://, azure://, ou local).
            show_progress: Exibe progresso no stdout quando ``True``.

        Returns:
            Dicionário com ``uri``, ``size_bytes``, ``upload_s`` e ``adapter``.
        """
        ...

    @abstractmethod
    def download(self, remote_uri: str, local_path: str,
                 show_progress: bool = True) -> dict:
        """Faz download de um arquivo remoto para local.

        Args:
            remote_uri: URI de origem.
            local_path: Caminho local de destino.
            show_progress: Exibe progresso no stdout quando ``True``.

        Returns:
            Dicionário com ``uri``, ``local_path``, ``size_bytes`` e ``download_s``.
        """
        ...

    @abstractmethod
    def exists(self, remote_uri: str) -> bool:
        """Verifica se um arquivo existe no storage.

        Args:
            remote_uri: URI a verificar.

        Returns:
            ``True`` se o arquivo existir.
        """
        ...

    @abstractmethod
    def delete(self, remote_uri: str) -> bool:
        """Remove um arquivo do storage.

        Args:
            remote_uri: URI do arquivo a remover.

        Returns:
            ``True`` se removido com sucesso.
        """
        ...

    @abstractmethod
    def list(self, remote_prefix: str,
             pattern: str = "*.permafrost") -> list:
        """Lista arquivos ``.permafrost`` em um prefixo.

        Args:
            remote_prefix: URI de prefixo / diretório a varrer.
            pattern: Glob pattern para filtrar nomes.

        Returns:
            Lista de URIs completas encontradas.
        """
        ...

    @abstractmethod
    def read_bytes(self, remote_uri: str) -> bytes:
        """Lê o conteúdo completo de um arquivo remoto para memória.

        Args:
            remote_uri: URI do arquivo.

        Returns:
            Conteúdo binário completo.
        """
        ...

    @abstractmethod
    def write_bytes(self, data: bytes, remote_uri: str) -> dict:
        """Escreve bytes diretamente no storage (sem arquivo local intermediário).

        Args:
            data: Bytes a gravar.
            remote_uri: URI de destino.

        Returns:
            Dicionário com ``uri``, ``size_bytes`` e ``adapter``.
        """
        ...

    def upload_and_verify(self, local_path: str, remote_uri: str) -> dict:
        """Upload com verificação de integridade via SHA-256.

        Args:
            local_path: Arquivo local a enviar.
            remote_uri: URI de destino.

        Returns:
            Resultado do upload enriquecido com ``local_sha256`` e ``remote_magic_ok``.
        """
        sha = hashlib.sha256(open(local_path, "rb").read()).hexdigest()
        result = self.upload(local_path, remote_uri)
        result['local_sha256'] = sha
        try:
            header = self.read_header_bytes(remote_uri)
            result['remote_magic_ok'] = header[:4] == b'PRMS'
        except Exception:
            result['remote_magic_ok'] = None
        return result

    def read_header_bytes(self, remote_uri: str, n_bytes: int = 4096) -> bytes:
        """Lê apenas os primeiros N bytes de um arquivo remoto.

        Adapters com suporte a range requests (S3, GCS) devem sobrescrever
        este método para evitar download completo.

        Args:
            remote_uri: URI do arquivo.
            n_bytes: Quantidade de bytes a ler a partir do início.

        Returns:
            Primeiros ``n_bytes`` do arquivo.
        """
        return self.read_bytes(remote_uri)[:n_bytes]

    def read_footer_bytes(self, remote_uri: str, n_bytes: int = 8192) -> bytes:
        """Lê os últimos N bytes (footer / sparse index) do arquivo.

        Adapters com suporte a range requests devem sobrescrever este método.

        Args:
            remote_uri: URI do arquivo.
            n_bytes: Quantidade de bytes a ler a partir do fim.

        Returns:
            Últimos ``n_bytes`` do arquivo.
        """
        data = self.read_bytes(remote_uri)
        return data[-n_bytes:]

    def upload_resumable(self, local_path: str, remote_uri: str,
                         chunk_size: int = 8 * 1024 * 1024,
                         state_file: Optional[str] = None,
                         max_retries: int = 3) -> dict:
        """Upload com retry automático (fallback para adapters sem suporte nativo).

        Adapters que suportam multipart (S3) ou escrita incremental (local)
        devem sobrescrever este método.

        Args:
            local_path: Arquivo local a enviar.
            remote_uri: URI de destino.
            chunk_size: Ignorado nesta implementação base.
            state_file: Ignorado nesta implementação base.
            max_retries: Número máximo de tentativas.

        Returns:
            Resultado de :meth:`upload`.
        """
        return _retry(
            lambda: self.upload(local_path, remote_uri, show_progress=False),
            max_retries=max_retries,
        )


# ── LOCAL ADAPTER (mock/testes) ───────────────────────────────────────────────
class LocalAdapter(StorageAdapter):
    """Adapter local — para testes e desenvolvimento sem cloud.

    Args:
        base_dir: Diretório base usado como raiz para caminhos relativos.
    """

    def __init__(self, base_dir: str = ".") -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _resolve(self, uri: str) -> Path:
        """Converte uma URI em caminho absoluto local.

        Args:
            uri: URI a resolver (local ou outro scheme tratado como subdir).

        Returns:
            ``Path`` absoluto correspondente.
        """
        p = parse_uri(uri)
        if p.scheme == "local":
            return Path(p.key)
        return self.base_dir / p.scheme / p.bucket / p.key

    def upload(self, local_path: str, remote_uri: str,
               show_progress: bool = True) -> dict:
        """Copia um arquivo local para o destino (simulando upload).

        Args:
            local_path: Arquivo de origem.
            remote_uri: URI de destino.
            show_progress: Exibe confirmação no stdout quando ``True``.

        Returns:
            Dicionário com métricas do upload.
        """
        t0 = time.time()
        dst = self._resolve(remote_uri)
        dst.parent.mkdir(parents=True, exist_ok=True)
        size = os.path.getsize(local_path)
        import shutil
        for attempt in range(3):
            try:
                shutil.copy2(local_path, dst)
                break
            except PermissionError:
                if attempt == 2:
                    raise
                time.sleep(0.1 * (attempt + 1))
        elapsed = time.time() - t0
        if show_progress:
            print(f"  ✓ [local] {local_path} → {dst}  ({size/1e6:.2f}MB, {elapsed:.2f}s)")
        return {'uri': remote_uri, 'size_bytes': size,
                'upload_s': round(elapsed, 3), 'adapter': 'local'}

    def download(self, remote_uri: str, local_path: str,
                 show_progress: bool = True) -> dict:
        """Copia um arquivo do destino local para um caminho de saída.

        Args:
            remote_uri: URI de origem.
            local_path: Caminho local de destino.
            show_progress: Exibe confirmação no stdout quando ``True``.

        Returns:
            Dicionário com métricas do download.

        Raises:
            FileNotFoundError: Se o arquivo de origem não existir.
        """
        t0 = time.time()
        src = self._resolve(remote_uri)
        if not src.exists():
            raise FileNotFoundError(f"Não encontrado: {remote_uri}")
        size = src.stat().st_size
        import shutil
        shutil.copy2(src, local_path)
        elapsed = time.time() - t0
        if show_progress:
            print(f"  ✓ [local] {remote_uri} → {local_path}  ({size/1e6:.2f}MB, {elapsed:.2f}s)")
        return {'uri': remote_uri, 'local_path': local_path, 'size_bytes': size,
                'download_s': round(elapsed, 3)}

    def exists(self, remote_uri: str) -> bool:
        """Verifica se o caminho local existe.

        Args:
            remote_uri: URI a verificar.

        Returns:
            ``True`` se o arquivo existir.
        """
        return self._resolve(remote_uri).exists()

    def delete(self, remote_uri: str) -> bool:
        """Remove um arquivo local.

        Args:
            remote_uri: URI do arquivo a remover.

        Returns:
            ``True`` se removido; ``False`` se não existia.
        """
        p = self._resolve(remote_uri)
        if p.exists():
            p.unlink()
            return True
        return False

    def list(self, remote_prefix: str, pattern: str = "*.permafrost") -> list:
        """Lista arquivos que correspondem ao padrão no diretório local.

        Args:
            remote_prefix: Diretório local a varrer.
            pattern: Glob pattern para filtrar nomes.

        Returns:
            Lista de caminhos absolutos como strings.
        """
        base = self._resolve(remote_prefix)
        if not base.exists():
            return []
        if base.is_dir():
            return [str(f) for f in sorted(base.rglob(pattern))]
        return [str(base)] if base.exists() else []

    def read_bytes(self, remote_uri: str) -> bytes:
        """Lê o conteúdo binário completo de um arquivo local.

        Args:
            remote_uri: URI do arquivo.

        Returns:
            Conteúdo binário.
        """
        return self._resolve(remote_uri).read_bytes()

    def write_bytes(self, data: bytes, remote_uri: str) -> dict:
        """Grava bytes em um arquivo local.

        Args:
            data: Conteúdo a gravar.
            remote_uri: URI de destino.

        Returns:
            Dicionário com ``uri``, ``size_bytes`` e ``adapter``.
        """
        dst = self._resolve(remote_uri)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        return {'uri': remote_uri, 'size_bytes': len(data), 'adapter': 'local'}

    def upload_resumable(self, local_path: str, remote_uri: str,
                         chunk_size: int = 8 * 1024 * 1024,
                         state_file: Optional[str] = None,
                         max_retries: int = 3) -> dict:
        """Upload incremental com estado persistido — retomável após interrupção.

        O estado é guardado em ``local_path + ".upload_state"`` (ou ``state_file``).
        Se o arquivo de estado existir e a fonte não mudou, retoma de onde parou.

        Args:
            local_path: Arquivo local a enviar.
            remote_uri: URI de destino (local).
            chunk_size: Bytes por bloco de escrita (padrão 8 MB).
            state_file: Caminho customizado para o arquivo de estado.
            max_retries: Tentativas por bloco em caso de erro.

        Returns:
            Dicionário com ``uri``, ``size_bytes``, ``upload_s``, ``adapter``
            e ``resumed`` (bool).
        """
        sf = _state_path(local_path, state_file)
        state = _load_state(sf)

        dst = self._resolve(remote_uri)
        dst.parent.mkdir(parents=True, exist_ok=True)

        src_stat = os.stat(local_path)
        src_size  = src_stat.st_size
        src_mtime = src_stat.st_mtime

        resumed   = False
        bytes_done = 0
        if state is not None:
            if (state.get('src_mtime') == src_mtime and
                    state.get('src_size') == src_size and
                    state.get('remote_uri') == remote_uri and
                    dst.exists()):
                bytes_done = state.get('bytes_written', 0)
                resumed = bytes_done > 0

        if not resumed:
            bytes_done = 0
            state = {
                'src_mtime': src_mtime,
                'src_size': src_size,
                'remote_uri': remote_uri,
                'bytes_written': 0,
            }
            _save_state(sf, state)

        t0   = time.time()
        mode = 'r+b' if resumed else 'wb'
        with open(local_path, 'rb') as fin, open(dst, mode) as fout:
            fin.seek(bytes_done)
            fout.seek(bytes_done)
            while True:
                chunk = fin.read(chunk_size)
                if not chunk:
                    break
                def _write(c=chunk, fp=fout):
                    fp.write(c)
                    fp.flush()
                _retry(_write, max_retries=max_retries)
                bytes_done += len(chunk)
                state['bytes_written'] = bytes_done
                _save_state(sf, state)

        _clear_state(sf)
        return {
            'uri':        remote_uri,
            'size_bytes': src_size,
            'upload_s':   round(time.time() - t0, 3),
            'adapter':    'local',
            'resumed':    resumed,
        }


# ── S3 ADAPTER ────────────────────────────────────────────────────────────────
class S3Adapter(StorageAdapter):
    """Adapter para AWS S3 e S3-compatible (MinIO, Cloudflare R2, etc.)

    Autenticação automática via:
    - ``AWS_ACCESS_KEY_ID`` + ``AWS_SECRET_ACCESS_KEY`` (env vars)
    - ``~/.aws/credentials``
    - IAM Role (EC2 / ECS / Lambda)
    - ``endpoint_url`` para storage S3-compatible

    Args:
        region: Região AWS (ex.: ``"us-east-1"``).
        endpoint_url: URL customizada para S3-compatible (ex.: MinIO).
        access_key: AWS access key explícita.
        secret_key: AWS secret key explícita.
        storage_class: Classe de armazenamento padrão (``"STANDARD"``,
            ``"GLACIER_DEEP_ARCHIVE"``, etc.).
    """

    def __init__(self, region: str = None, endpoint_url: str = None,
                 access_key: str = None, secret_key: str = None,
                 storage_class: str = "STANDARD") -> None:
        import boto3
        kwargs = {}
        if region:       kwargs['region_name'] = region
        if endpoint_url: kwargs['endpoint_url'] = endpoint_url
        if access_key:   kwargs['aws_access_key_id'] = access_key
        if secret_key:   kwargs['aws_secret_access_key'] = secret_key
        self.s3 = boto3.client('s3', **kwargs)
        self.storage_class = storage_class

    def _parse(self, uri: str) -> Tuple[str, str]:
        """Extrai bucket e key de uma URI s3://.

        Args:
            uri: URI no formato ``s3://bucket/key``.

        Returns:
            Tupla ``(bucket, key)``.

        Raises:
            ValueError: Se a URI não for ``s3://``.
        """
        p = parse_uri(uri)
        if p.scheme != 's3':
            raise ValueError(f"URI deve ser s3://, recebeu: {uri}")
        return p.bucket, p.key

    def upload(self, local_path: str, remote_uri: str,
               show_progress: bool = True) -> dict:
        """Faz upload multipart para S3.

        Args:
            local_path: Arquivo local a enviar.
            remote_uri: URI s3:// de destino.
            show_progress: Exibe velocidade de upload quando ``True``.

        Returns:
            Dicionário com ``uri``, ``bucket``, ``key``, ``size_bytes``,
            ``upload_s`` e ``adapter``.
        """
        bucket, key = self._parse(remote_uri)
        size = os.path.getsize(local_path)
        t0 = time.time()
        extra = {'StorageClass': self.storage_class}
        if show_progress:
            from boto3.s3.transfer import TransferConfig
            config = TransferConfig(multipart_threshold=8*1024*1024, max_concurrency=4)
            self.s3.upload_file(local_path, bucket, key, ExtraArgs=extra, Config=config)
        else:
            self.s3.upload_file(local_path, bucket, key, ExtraArgs=extra)
        elapsed = time.time() - t0
        speed = size / 1e6 / elapsed if elapsed > 0 else 0
        if show_progress:
            print(f"  ✓ [S3] s3://{bucket}/{key}  ({size/1e6:.2f}MB, {elapsed:.1f}s, {speed:.1f}MB/s)")
        return {'uri': remote_uri, 'bucket': bucket, 'key': key,
                'size_bytes': size, 'upload_s': round(elapsed, 3), 'adapter': 's3'}

    def download(self, remote_uri: str, local_path: str,
                 show_progress: bool = True) -> dict:
        """Faz download de um objeto S3 para um arquivo local.

        Args:
            remote_uri: URI s3:// de origem.
            local_path: Caminho local de destino.
            show_progress: Exibe tamanho e tempo quando ``True``.

        Returns:
            Dicionário com ``uri``, ``local_path``, ``size_bytes`` e ``download_s``.
        """
        bucket, key = self._parse(remote_uri)
        t0 = time.time()
        meta = self.s3.head_object(Bucket=bucket, Key=key)
        size = meta['ContentLength']
        self.s3.download_file(bucket, key, local_path)
        elapsed = time.time() - t0
        if show_progress:
            print(f"  ✓ [S3] {remote_uri} → {local_path}  ({size/1e6:.2f}MB, {elapsed:.1f}s)")
        return {'uri': remote_uri, 'local_path': local_path, 'size_bytes': size,
                'download_s': round(elapsed, 3), 'adapter': 's3'}

    def exists(self, remote_uri: str) -> bool:
        """Verifica se um objeto existe no S3 via HEAD request.

        Args:
            remote_uri: URI s3:// a verificar.

        Returns:
            ``True`` se o objeto existir.
        """
        bucket, key = self._parse(remote_uri)
        try:
            self.s3.head_object(Bucket=bucket, Key=key)
            return True
        except self.s3.exceptions.ClientError:
            return False

    def delete(self, remote_uri: str) -> bool:
        """Remove um objeto do S3.

        Args:
            remote_uri: URI s3:// do objeto a remover.

        Returns:
            ``True`` após a remoção.
        """
        bucket, key = self._parse(remote_uri)
        self.s3.delete_object(Bucket=bucket, Key=key)
        return True

    def list(self, remote_prefix: str, pattern: str = "*.permafrost") -> list:
        """Lista objetos ``.permafrost`` em um prefixo S3.

        Args:
            remote_prefix: URI s3:// de prefixo.
            pattern: Ignorado nesta implementação (filtra por extensão ``.permafrost``).

        Returns:
            Lista de URIs ``s3://`` ordenadas.
        """
        bucket, prefix = self._parse(remote_prefix)
        paginator = self.s3.get_paginator('list_objects_v2')
        results = []
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get('Contents', []):
                key = obj['Key']
                if key.endswith('.permafrost'):
                    results.append(f"s3://{bucket}/{key}")
        return sorted(results)

    def read_bytes(self, remote_uri: str) -> bytes:
        """Lê o conteúdo completo de um objeto S3.

        Args:
            remote_uri: URI s3:// do objeto.

        Returns:
            Conteúdo binário completo.
        """
        bucket, key = self._parse(remote_uri)
        resp = self.s3.get_object(Bucket=bucket, Key=key)
        return resp['Body'].read()

    def read_header_bytes(self, remote_uri: str, n_bytes: int = 4096) -> bytes:
        """Lê os primeiros N bytes via range request (sem download completo).

        Args:
            remote_uri: URI s3:// do objeto.
            n_bytes: Quantidade de bytes a ler a partir do início.

        Returns:
            Primeiros ``n_bytes`` do objeto.
        """
        bucket, key = self._parse(remote_uri)
        resp = self.s3.get_object(Bucket=bucket, Key=key,
                                  Range=f"bytes=0-{n_bytes-1}")
        return resp['Body'].read()

    def read_footer_bytes(self, remote_uri: str, n_bytes: int = 8192) -> bytes:
        """Lê os últimos N bytes via range request (sparse index sem download completo).

        Args:
            remote_uri: URI s3:// do objeto.
            n_bytes: Quantidade de bytes a ler a partir do fim.

        Returns:
            Últimos ``n_bytes`` do objeto.
        """
        bucket, key = self._parse(remote_uri)
        meta = self.s3.head_object(Bucket=bucket, Key=key)
        size = meta['ContentLength']
        start = max(0, size - n_bytes)
        resp = self.s3.get_object(Bucket=bucket, Key=key,
                                  Range=f"bytes={start}-{size-1}")
        return resp['Body'].read()

    def write_bytes(self, data: bytes, remote_uri: str) -> dict:
        """Grava bytes diretamente em um objeto S3.

        Args:
            data: Conteúdo a gravar.
            remote_uri: URI s3:// de destino.

        Returns:
            Dicionário com ``uri``, ``bucket``, ``key``, ``size_bytes`` e ``adapter``.
        """
        bucket, key = self._parse(remote_uri)
        self.s3.put_object(Body=data, Bucket=bucket, Key=key,
                           StorageClass=self.storage_class)
        return {'uri': remote_uri, 'bucket': bucket, 'key': key,
                'size_bytes': len(data), 'adapter': 's3'}

    def upload_resumable(self, local_path: str, remote_uri: str,
                         chunk_size: int = 8 * 1024 * 1024,
                         state_file: Optional[str] = None,
                         max_retries: int = 3) -> dict:
        """Upload S3 multipart com estado persistido — retomável após interrupção.

        Usa a S3 Multipart Upload API: create → upload_part × N → complete.
        O estado (upload_id + ETags das partes já enviadas) é gravado em disco
        para que uploads interrompidos possam ser retomados sem reenviar partes.

        Args:
            local_path: Arquivo local a enviar.
            remote_uri: URI s3:// de destino.
            chunk_size: Bytes por parte (mínimo 5 MB imposto pela AWS).
            state_file: Caminho customizado para o arquivo de estado.
            max_retries: Tentativas por parte em caso de erro.

        Returns:
            Dicionário com ``uri``, ``bucket``, ``key``, ``size_bytes``,
            ``upload_s``, ``adapter``, ``n_parts`` e ``resumed`` (bool).
        """
        sf = _state_path(local_path, state_file)
        state = _load_state(sf)

        bucket, key = self._parse(remote_uri)
        src_stat  = os.stat(local_path)
        src_size  = src_stat.st_size
        src_mtime = src_stat.st_mtime
        chunk_size = max(chunk_size, 5 * 1024 * 1024)  # S3 minimum per part

        upload_id  = None
        parts      = []
        bytes_done = 0
        resumed    = False

        if state is not None:
            if (state.get('src_mtime') == src_mtime and
                    state.get('src_size') == src_size and
                    state.get('remote_uri') == remote_uri and
                    state.get('upload_id')):
                upload_id  = state['upload_id']
                parts      = state.get('parts', [])
                bytes_done = sum(p['size'] for p in parts)
                resumed    = True

        if not resumed:
            resp = self.s3.create_multipart_upload(
                Bucket=bucket, Key=key, StorageClass=self.storage_class,
            )
            upload_id = resp['UploadId']
            parts     = []
            state     = {
                'src_mtime': src_mtime,
                'src_size':  src_size,
                'remote_uri': remote_uri,
                'upload_id': upload_id,
                'parts':     [],
            }
            _save_state(sf, state)

        t0       = time.time()
        part_num = len(parts) + 1

        with open(local_path, 'rb') as fin:
            fin.seek(bytes_done)
            while True:
                chunk = fin.read(chunk_size)
                if not chunk:
                    break
                pn = part_num
                def _upload(c=chunk, n=pn):
                    r = self.s3.upload_part(
                        Body=c, Bucket=bucket, Key=key,
                        PartNumber=n, UploadId=upload_id,
                    )
                    return r['ETag']
                etag = _retry(_upload, max_retries=max_retries)
                parts.append({'PartNumber': pn, 'ETag': etag, 'size': len(chunk)})
                state['parts'] = parts
                _save_state(sf, state)
                part_num += 1

        self.s3.complete_multipart_upload(
            Bucket=bucket, Key=key, UploadId=upload_id,
            MultipartUpload={'Parts': [{'PartNumber': p['PartNumber'], 'ETag': p['ETag']}
                                       for p in parts]},
        )
        _clear_state(sf)
        return {
            'uri':        remote_uri,
            'bucket':     bucket,
            'key':        key,
            'size_bytes': src_size,
            'upload_s':   round(time.time() - t0, 3),
            'adapter':    's3',
            'n_parts':    len(parts),
            'resumed':    resumed,
        }

    def set_lifecycle(self, bucket: str, prefix: str,
                      transition_days: int = 30,
                      target_class: str = "GLACIER_DEEP_ARCHIVE") -> None:
        """Configura lifecycle policy para arquivar objetos automaticamente.

        Move arquivos ``.permafrost`` para Glacier Deep Archive após N dias,
        reduzindo custo de armazenamento cold em até 88%.

        Args:
            bucket: Nome do bucket S3.
            prefix: Prefixo dos objetos a gerenciar (ex.: ``"dados/cold/"``).
            transition_days: Dias até a transição para ``target_class``.
            target_class: Classe S3 de destino (``"GLACIER_DEEP_ARCHIVE"``,
                ``"GLACIER"``, ``"INTELLIGENT_TIERING"``, etc.).
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
    """Adapter para Google Cloud Storage.

    Autenticação via ``GOOGLE_APPLICATION_CREDENTIALS`` ou Application Default Credentials.

    Args:
        project: ID do projeto GCP (opcional — usa o padrão da conta ADC).
        storage_class: Classe de armazenamento padrão (``"STANDARD"``,
            ``"NEARLINE"``, ``"COLDLINE"``, ``"ARCHIVE"``).
    """

    def __init__(self, project: str = None, storage_class: str = "STANDARD") -> None:
        from google.cloud import storage as gcs
        self.client = gcs.Client(project=project)
        self.storage_class = storage_class

    def _parse(self, uri: str) -> Tuple[str, str]:
        """Extrai bucket e blob name de uma URI gs://.

        Args:
            uri: URI no formato ``gs://bucket/blob``.

        Returns:
            Tupla ``(bucket_name, blob_name)``.

        Raises:
            ValueError: Se a URI não for ``gs://``.
        """
        p = parse_uri(uri)
        if p.scheme != 'gs':
            raise ValueError(f"URI deve ser gs://, recebeu: {uri}")
        return p.bucket, p.key

    def upload(self, local_path: str, remote_uri: str,
               show_progress: bool = True) -> dict:
        """Faz upload de um arquivo para um blob GCS.

        Args:
            local_path: Arquivo local a enviar.
            remote_uri: URI gs:// de destino.
            show_progress: Exibe confirmação quando ``True``.

        Returns:
            Dicionário com ``uri``, ``size_bytes``, ``upload_s`` e ``adapter``.
        """
        bucket_name, blob_name = self._parse(remote_uri)
        t0 = time.time()
        size = os.path.getsize(local_path)
        bucket = self.client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        blob.upload_from_filename(local_path)
        elapsed = time.time() - t0
        if show_progress:
            print(f"  ✓ [GCS] gs://{bucket_name}/{blob_name}  ({size/1e6:.2f}MB, {elapsed:.1f}s)")
        return {'uri': remote_uri, 'size_bytes': size,
                'upload_s': round(elapsed, 3), 'adapter': 'gcs'}

    def download(self, remote_uri: str, local_path: str,
                 show_progress: bool = True) -> dict:
        """Faz download de um blob GCS para um arquivo local.

        Args:
            remote_uri: URI gs:// de origem.
            local_path: Caminho local de destino.
            show_progress: Exibe confirmação quando ``True``.

        Returns:
            Dicionário com ``uri``, ``local_path``, ``size_bytes`` e ``download_s``.
        """
        bucket_name, blob_name = self._parse(remote_uri)
        t0 = time.time()
        blob = self.client.bucket(bucket_name).blob(blob_name)
        size = blob.size or 0
        blob.download_to_filename(local_path)
        elapsed = time.time() - t0
        if show_progress:
            print(f"  ✓ [GCS] {remote_uri} → {local_path}  ({size/1e6:.2f}MB, {elapsed:.1f}s)")
        return {'uri': remote_uri, 'local_path': local_path, 'size_bytes': size,
                'download_s': round(elapsed, 3), 'adapter': 'gcs'}

    def exists(self, remote_uri: str) -> bool:
        """Verifica se um blob existe no GCS.

        Args:
            remote_uri: URI gs:// a verificar.

        Returns:
            ``True`` se o blob existir.
        """
        bn, blob_name = self._parse(remote_uri)
        return self.client.bucket(bn).blob(blob_name).exists()

    def delete(self, remote_uri: str) -> bool:
        """Remove um blob do GCS.

        Args:
            remote_uri: URI gs:// do blob a remover.

        Returns:
            ``True`` após a remoção.
        """
        bn, blob_name = self._parse(remote_uri)
        self.client.bucket(bn).blob(blob_name).delete()
        return True

    def list(self, remote_prefix: str, pattern: str = "*.permafrost") -> list:
        """Lista blobs ``.permafrost`` em um prefixo GCS.

        Args:
            remote_prefix: URI gs:// de prefixo.
            pattern: Ignorado nesta implementação (filtra por extensão ``.permafrost``).

        Returns:
            Lista de URIs ``gs://`` ordenadas.
        """
        bn, prefix = self._parse(remote_prefix)
        blobs = self.client.list_blobs(bn, prefix=prefix)
        return sorted([f"gs://{bn}/{b.name}" for b in blobs
                       if b.name.endswith('.permafrost')])

    def read_bytes(self, remote_uri: str) -> bytes:
        """Lê o conteúdo completo de um blob GCS.

        Args:
            remote_uri: URI gs:// do blob.

        Returns:
            Conteúdo binário completo.
        """
        bn, blob_name = self._parse(remote_uri)
        return self.client.bucket(bn).blob(blob_name).download_as_bytes()

    def write_bytes(self, data: bytes, remote_uri: str) -> dict:
        """Grava bytes diretamente em um blob GCS.

        Args:
            data: Conteúdo a gravar.
            remote_uri: URI gs:// de destino.

        Returns:
            Dicionário com ``uri``, ``size_bytes`` e ``adapter``.
        """
        bn, blob_name = self._parse(remote_uri)
        self.client.bucket(bn).blob(blob_name).upload_from_string(data)
        return {'uri': remote_uri, 'size_bytes': len(data), 'adapter': 'gcs'}


# ── AZURE ADAPTER ─────────────────────────────────────────────────────────────
class AzureAdapter(StorageAdapter):
    """Adapter para Azure Blob Storage.

    Args:
        conn_str: Connection string completa do portal Azure.
        account_name: Nome da conta de armazenamento (alternativa ao ``conn_str``).
        account_key: Chave de acesso da conta (alternativa ao ``conn_str``).
        tier: Tier de acesso padrão (``"Hot"``, ``"Cool"``, ``"Archive"``).

    Raises:
        ValueError: Se nem ``conn_str`` nem ``account_name`` + ``account_key``
            forem fornecidos.
    """

    def __init__(self, conn_str: str = None, account_name: str = None,
                 account_key: str = None, tier: str = "Cool") -> None:
        from azure.storage.blob import BlobServiceClient
        if conn_str:
            self.client = BlobServiceClient.from_connection_string(conn_str)
        elif account_name and account_key:
            url = f"https://{account_name}.blob.core.windows.net"
            self.client = BlobServiceClient(account_url=url, credential=account_key)
        else:
            raise ValueError("Forneça conn_str ou account_name+account_key")
        self.tier = tier

    def _parse(self, uri: str) -> Tuple[str, str]:
        """Extrai container e blob name de uma URI azure://.

        Args:
            uri: URI no formato ``azure://container/blob``.

        Returns:
            Tupla ``(container, blob_name)``.

        Raises:
            ValueError: Se a URI não for ``azure://``.
        """
        p = parse_uri(uri)
        if p.scheme != 'azure':
            raise ValueError(f"URI deve ser azure://, recebeu: {uri}")
        return p.bucket, p.key   # bucket = container

    def upload(self, local_path: str, remote_uri: str,
               show_progress: bool = True) -> dict:
        """Faz upload de um arquivo para um blob Azure.

        Args:
            local_path: Arquivo local a enviar.
            remote_uri: URI azure:// de destino.
            show_progress: Exibe confirmação quando ``True``.

        Returns:
            Dicionário com ``uri``, ``size_bytes``, ``upload_s`` e ``adapter``.
        """
        container, blob_name = self._parse(remote_uri)
        t0 = time.time()
        size = os.path.getsize(local_path)
        blob_client = self.client.get_blob_client(container=container, blob=blob_name)
        with open(local_path, 'rb') as f:
            blob_client.upload_blob(f, overwrite=True, standard_blob_tier=self.tier)
        elapsed = time.time() - t0
        if show_progress:
            print(f"  ✓ [Azure] azure://{container}/{blob_name}  ({size/1e6:.2f}MB, {elapsed:.1f}s)")
        return {'uri': remote_uri, 'size_bytes': size,
                'upload_s': round(elapsed, 3), 'adapter': 'azure'}

    def download(self, remote_uri: str, local_path: str,
                 show_progress: bool = True) -> dict:
        """Faz download de um blob Azure para um arquivo local.

        Args:
            remote_uri: URI azure:// de origem.
            local_path: Caminho local de destino.
            show_progress: Exibe confirmação quando ``True``.

        Returns:
            Dicionário com ``uri``, ``local_path``, ``size_bytes`` e ``download_s``.
        """
        container, blob_name = self._parse(remote_uri)
        t0 = time.time()
        blob_client = self.client.get_blob_client(container=container, blob=blob_name)
        props = blob_client.get_blob_properties()
        size = props.size
        with open(local_path, 'wb') as f:
            f.write(blob_client.download_blob().readall())
        elapsed = time.time() - t0
        if show_progress:
            print(f"  ✓ [Azure] {remote_uri} → {local_path}  ({size/1e6:.2f}MB, {elapsed:.1f}s)")
        return {'uri': remote_uri, 'local_path': local_path, 'size_bytes': size,
                'download_s': round(elapsed, 3), 'adapter': 'azure'}

    def exists(self, remote_uri: str) -> bool:
        """Verifica se um blob existe no Azure.

        Args:
            remote_uri: URI azure:// a verificar.

        Returns:
            ``True`` se o blob existir.
        """
        container, blob_name = self._parse(remote_uri)
        return self.client.get_blob_client(
            container=container, blob=blob_name).exists()

    def delete(self, remote_uri: str) -> bool:
        """Remove um blob do Azure.

        Args:
            remote_uri: URI azure:// do blob a remover.

        Returns:
            ``True`` após a remoção.
        """
        container, blob_name = self._parse(remote_uri)
        self.client.get_blob_client(
            container=container, blob=blob_name).delete_blob()
        return True

    def list(self, remote_prefix: str, pattern: str = "*.permafrost") -> list:
        """Lista blobs ``.permafrost`` em um prefixo Azure.

        Args:
            remote_prefix: URI azure:// de prefixo.
            pattern: Ignorado nesta implementação (filtra por extensão ``.permafrost``).

        Returns:
            Lista de URIs ``azure://`` ordenadas.
        """
        container, prefix = self._parse(remote_prefix)
        cc = self.client.get_container_client(container)
        return sorted([f"azure://{container}/{b.name}"
                       for b in cc.list_blobs(name_starts_with=prefix)
                       if b.name.endswith('.permafrost')])

    def read_bytes(self, remote_uri: str) -> bytes:
        """Lê o conteúdo completo de um blob Azure.

        Args:
            remote_uri: URI azure:// do blob.

        Returns:
            Conteúdo binário completo.
        """
        container, blob_name = self._parse(remote_uri)
        return self.client.get_blob_client(
            container=container, blob=blob_name).download_blob().readall()

    def write_bytes(self, data: bytes, remote_uri: str) -> dict:
        """Grava bytes diretamente em um blob Azure.

        Args:
            data: Conteúdo a gravar.
            remote_uri: URI azure:// de destino.

        Returns:
            Dicionário com ``uri``, ``size_bytes`` e ``adapter``.
        """
        container, blob_name = self._parse(remote_uri)
        self.client.get_blob_client(
            container=container, blob=blob_name).upload_blob(data, overwrite=True)
        return {'uri': remote_uri, 'size_bytes': len(data), 'adapter': 'azure'}


# ── FACTORY ───────────────────────────────────────────────────────────────────
def storage_from_uri(uri: str, **kwargs) -> StorageAdapter:
    """Cria o adapter correto baseado na URI.

    Os kwargs são repassados para o construtor do adapter correspondente.

    Args:
        uri: URI de storage (``s3://``, ``gs://``, ``azure://`` ou caminho local).
        **kwargs: Parâmetros extras para o adapter (região, credenciais, etc.).

    Returns:
        Instância do :class:`StorageAdapter` apropriado.

    Examples::

        storage_from_uri("s3://bucket/")
        storage_from_uri("gs://bucket/", project="meu-projeto")
        storage_from_uri("azure://container/", conn_str="DefaultEndpoints...")
        storage_from_uri("/local/path/")   # → LocalAdapter
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
              tmp_dir: str = None, keep_local: bool = False, **freeze_kwargs) -> dict:
    """Comprime um DataFrame e faz upload direto para cloud.

    Combina :func:`permafrost.freeze` + :meth:`StorageAdapter.upload` em uma chamada.

    Args:
        df: DataFrame pandas a comprimir.
        remote_uri: URI de destino (s3://, gs://, azure://, ou local).
        adapter: Adapter explícito; se ``None``, detectado automaticamente pela URI.
        tmp_dir: Diretório temporário local para o arquivo intermediário;
            usa ``tempfile.gettempdir()`` quando ``None``.
        keep_local: Mantém o arquivo temporário local após o upload quando ``True``.
        **freeze_kwargs: Parâmetros extras para :func:`permafrost.freeze`
            (``codec``, ``quant``, ``partition_by``, etc.).

    Returns:
        Dicionário de métricas com campos de :func:`permafrost.freeze` mais
        ``remote_uri``, ``upload_s``, ``remote_magic_ok`` e ``adapter``.

    Examples::

        metrics = freeze_to(df, "s3://bucket/vendas_2024.permafrost",
                            codec=CODEC_LZMA2, partition_by="ano")
    """
    import uuid, tempfile
    from permafrost.codec import freeze

    if adapter is None:
        adapter = storage_from_uri(remote_uri)
    if tmp_dir is None:
        tmp_dir = tempfile.gettempdir()

    p = parse_uri(remote_uri)
    fname = p.filename or "output.permafrost"
    local_tmp = os.path.join(tmp_dir, f"_pf_{uuid.uuid4().hex[:8]}_{fname}")

    print(f"  freeze → {local_tmp} ...")
    metrics = freeze(df, local_tmp, **freeze_kwargs)
    print(f"  Comprimido: {metrics['stored_mb']:.3f}MB | ratio={metrics['ratio']:.2f}×")

    print(f"  upload → {remote_uri} ...")
    upload_result = adapter.upload_and_verify(local_tmp, remote_uri)
    metrics['remote_uri']      = remote_uri
    metrics['upload_s']        = upload_result.get('upload_s')
    metrics['remote_magic_ok'] = upload_result.get('remote_magic_ok')
    metrics['adapter']         = upload_result.get('adapter')

    if not keep_local:
        os.remove(local_tmp)

    return metrics


def thaw_from(remote_uri: str, adapter: StorageAdapter = None,
              tmp_dir: str = None, keep_local: bool = False, **thaw_kwargs):
    """Faz download de um ``.permafrost`` da cloud e descomprime.

    Combina :meth:`StorageAdapter.download` + :func:`permafrost.thaw` em uma chamada.

    Args:
        remote_uri: URI de origem (s3://, gs://, azure://, ou local).
        adapter: Adapter explícito; se ``None``, detectado automaticamente pela URI.
        tmp_dir: Diretório temporário local para o arquivo baixado;
            usa ``tempfile.gettempdir()`` quando ``None``.
        keep_local: Mantém o arquivo temporário local após o thaw quando ``True``.
        **thaw_kwargs: Parâmetros extras para :func:`permafrost.thaw`
            (``filter``, ``verify``, etc.).

    Returns:
        DataFrame pandas restaurado.

    Examples::

        df = thaw_from("s3://bucket/vendas_2024.permafrost", filter={"ano": 2024})
    """
    import uuid, tempfile
    from permafrost.codec import unfreeze as thaw

    if adapter is None:
        adapter = storage_from_uri(remote_uri)
    if tmp_dir is None:
        tmp_dir = tempfile.gettempdir()

    p = parse_uri(remote_uri)
    fname = p.filename or "download.permafrost"
    local_tmp = os.path.join(tmp_dir, f"_pf_{uuid.uuid4().hex[:8]}_{fname}")

    print(f"  download ← {remote_uri} ...")
    adapter.download(remote_uri, local_tmp)

    print(f"  unfreeze → DataFrame ...")
    df = thaw(local_tmp, **thaw_kwargs)

    if not keep_local:
        os.remove(local_tmp)

    return df


def audit_remote(remote_uri: str, adapter: StorageAdapter = None) -> dict:
    """Lê apenas o header + footer de um ``.permafrost`` remoto via range requests.

    Não faz download do arquivo completo — ideal para catálogos com milhares de
    arquivos no S3/GCS/Azure.

    Args:
        remote_uri: URI do arquivo ``.permafrost`` remoto.
        adapter: Adapter explícito; se ``None``, detectado automaticamente pela URI.

    Returns:
        Dicionário com ``uri``, ``codec``, ``quant``, ``orig_rows``, ``n_chunks``,
        ``columns``, ``partition_col``, ``partition_keys`` e ``n_index_entries``.

    Raises:
        ValueError: Se o arquivo não for um ``.permafrost`` válido.
    """
    from permafrost.codec import _read_header, _read_sparse_index, MAGIC, EOF_MAGIC

    if adapter is None:
        adapter = storage_from_uri(remote_uri)

    header_bytes = adapter.read_header_bytes(remote_uri, n_bytes=65536)
    footer_bytes = adapter.read_footer_bytes(remote_uri, n_bytes=16384)

    if header_bytes[:4] != MAGIC:
        raise ValueError(f"Não é um arquivo .permafrost válido: {remote_uri}")

    h = _read_header(header_bytes)

    try:
        idx = _read_sparse_index(footer_bytes)
    except Exception:
        idx = []

    return {
        'uri': remote_uri,
        'codec': {0x01: 'zstd', 0x02: 'lzma2'}.get(h['codec'], '?'),
        'quant': h['quant'],
        'orig_rows': h['orig_rows'],
        'n_chunks': h['n_chunks'],
        'columns': list(h['manifests'].keys()),
        'partition_col': idx[0]['part_col'] if idx else None,
        'partition_keys': [e['part_key'] for e in idx],
        'n_index_entries': len(idx),
    }
