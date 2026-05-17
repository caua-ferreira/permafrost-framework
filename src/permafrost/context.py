"""
PermafrostContext — API de alto nível para v1.0

Unifica catalog + storage + cluster em um único objeto configurável.

Uso típico:

    import permafrost as pf

    # Apenas local (mais simples)
    ctx = pf.PermafrostContext(catalog="catalog.db")
    ctx.freeze(df, "vendas_2024.permafrost")
    df = ctx.thaw("vendas_2024.permafrost")

    # Com cloud storage
    ctx = pf.PermafrostContext(
        catalog="catalog.db",
        storage="s3://meu-bucket/cold/",
    )
    ctx.freeze(df, "vendas_2024")          # → s3://meu-bucket/cold/vendas_2024.permafrost
    df = ctx.thaw("vendas_2024")

    # Com cluster distribuído
    ctx = pf.PermafrostContext(
        catalog="catalog.db",
        storage="s3://meu-bucket/cold/",
        cluster="http://master:8700",
        token="eyJ...",
    )
    job_id = ctx.freeze_async(df, "vendas_2024")
    ctx.wait(job_id)
"""

from __future__ import annotations

import os
import tempfile
import uuid
from typing import Optional


class PermafrostContext:
    """API de alto nível unificando catalog, storage e cluster.

    Todos os parâmetros são opcionais — use só o que precisar.

    Args:
        catalog: Caminho do banco DuckDB do catalog. ``None`` desativa o catalog.
        storage: URI base do storage (``s3://bucket/prefix/``, ``gs://...``,
            ``azure://...``, ou caminho local). ``None`` usa o diretório atual.
        cluster: URL do PermafrostMaster (``http://master:8700``).
            ``None`` desativa integração com cluster.
        codec: Codec padrão para freeze (:data:`CODEC_LZMA2`, :data:`CODEC_ZSTD`,
            :data:`CODEC_ZPAQ`, ``"auto"``). Padrão: :data:`CODEC_LZMA2`.
        quant: Nível de quantização padrão (:data:`QUANT_NONE`, :data:`QUANT_HIGH`,
            etc.). Padrão: :data:`QUANT_NONE` (lossless).
        key: Chave de criptografia AES-256 (32 bytes) ou instância de
            :class:`~permafrost.crypto.KeyProvider`. ``None`` = sem criptografia.
        token: Token JWT para autenticação no cluster. Requerido quando ``cluster``
            está configurado e RBAC está habilitado.
        **storage_kwargs: Parâmetros extras passados ao adapter de storage
            (ex.: ``region="us-east-1"`` para S3Adapter).

    Examples::

        # Configuração mínima
        ctx = PermafrostContext(catalog="cat.db")

        # Cloud completo
        ctx = PermafrostContext(
            catalog="cat.db",
            storage="s3://meu-bucket/cold/",
            cluster="http://master:8700",
            codec=pf.CODEC_ZSTD,
        )

        # Usar como context manager para fechar conexões automaticamente
        with PermafrostContext(catalog="cat.db") as ctx:
            ctx.freeze(df, "vendas.permafrost")
    """

    def __init__(
        self,
        catalog: Optional[str] = None,
        storage: Optional[str] = None,
        cluster: Optional[str] = None,
        codec=None,
        quant=None,
        key=None,
        token: Optional[str] = None,
        **storage_kwargs,
    ) -> None:
        from permafrost.codec import CODEC_LZMA2, QUANT_NONE
        self.catalog_path  = catalog
        self.storage_uri   = storage
        self.cluster_url   = cluster
        self.default_codec = codec if codec is not None else CODEC_LZMA2
        self.default_quant = quant if quant is not None else QUANT_NONE
        self.key           = key
        self.token         = token
        self._storage_kwargs = storage_kwargs
        self._catalog  = None
        self._adapter  = None
        self._client   = None

    # ── Lazy properties ───────────────────────────────────────────────────────

    @property
    def catalog(self):
        """PermafrostCatalog ativo (criado na primeira chamada)."""
        if self._catalog is None:
            if self.catalog_path is None:
                raise RuntimeError(
                    "Catalog não configurado. Passe catalog='caminho.db' ao criar PermafrostContext."
                )
            from permafrost.catalog import PermafrostCatalog
            self._catalog = PermafrostCatalog(self.catalog_path)
        return self._catalog

    @property
    def adapter(self):
        """StorageAdapter ativo (criado na primeira chamada)."""
        if self._adapter is None:
            if self.storage_uri:
                from permafrost.storage import storage_from_uri
                self._adapter = storage_from_uri(self.storage_uri, **self._storage_kwargs)
            else:
                from permafrost.storage import LocalAdapter
                self._adapter = LocalAdapter(".")
        return self._adapter

    @property
    def client(self):
        """PermafrostClient ativo (criado na primeira chamada)."""
        if self._client is None:
            if self.cluster_url is None:
                raise RuntimeError(
                    "Cluster não configurado. Passe cluster='http://master:8700' ao criar PermafrostContext."
                )
            from permafrost.cluster import PermafrostClient
            self._client = PermafrostClient(self.cluster_url)
        return self._client

    # ── URI helpers ───────────────────────────────────────────────────────────

    def _resolve_uri(self, name: str) -> str:
        """Constrói a URI completa a partir de um nome de arquivo."""
        if not name.endswith(".permafrost"):
            name = name + ".permafrost"
        if self.storage_uri:
            return self.storage_uri.rstrip("/") + "/" + name.lstrip("/")
        return name

    def _is_remote(self) -> bool:
        if not self.storage_uri:
            return False
        return self.storage_uri.startswith(("s3://", "gs://", "azure://"))

    # ── FREEZE ────────────────────────────────────────────────────────────────

    def freeze(self, df, name: str, **kwargs) -> dict:
        """Comprime um DataFrame e armazena no storage configurado.

        Se o catalog estiver configurado, registra automaticamente.
        Se o cluster estiver configurado, delega o job ao master.

        Args:
            df: DataFrame pandas a comprimir.
            name: Nome do arquivo (sem ou com ``.permafrost``).
                Resolvido em relação ao ``storage`` configurado.
            **kwargs: Parâmetros extras para :func:`~permafrost.freeze`
                (``codec``, ``quant``, ``key``, ``partition_by``, ``chunk_rows``, etc.).

        Returns:
            Dicionário de métricas com ``ratio``, ``rows``, ``stored_mb``,
            ``uri`` e demais campos de :func:`~permafrost.freeze`.

        Examples::

            ctx.freeze(df, "vendas_2024", partition_by="ano")
            ctx.freeze(df, "vendas_2024", codec=pf.CODEC_ZSTD, quant=pf.QUANT_HIGH)
        """
        from permafrost.codec import freeze as _freeze

        codec = kwargs.pop("codec", self.default_codec)
        quant = kwargs.pop("quant", self.default_quant)
        key   = kwargs.pop("key",   self.key)

        uri = self._resolve_uri(name)

        # ── via cluster ───────────────────────────────────────────────────────
        if self.cluster_url:
            job_id = self.client.freeze(
                uri, uri, token=self.token,
                codec=codec, quant=quant,
            )
            result = self.client.wait(job_id, token=self.token)
            result["uri"] = uri
            return result

        # ── local freeze → upload → register ─────────────────────────────────
        fname    = os.path.basename(uri) if "/" in uri or "\\" in uri else uri
        local_tmp = os.path.join(tempfile.gettempdir(),
                                 f"_pf_{uuid.uuid4().hex[:8]}_{fname}")
        try:
            metrics = _freeze(df, local_tmp, codec=codec, quant=quant, key=key, **kwargs)

            if self._is_remote():
                self.adapter.upload(local_tmp, uri, show_progress=False)
            else:
                import shutil
                dst = uri
                os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
                shutil.copy2(local_tmp, dst)

            if self.catalog_path:
                try:
                    self.catalog.register(local_tmp)
                except Exception:
                    pass  # catalog registration is best-effort

            metrics["uri"] = uri
            return metrics

        finally:
            if os.path.exists(local_tmp):
                os.remove(local_tmp)

    def freeze_async(self, df, name: str, **kwargs) -> str:
        """Submete um job de freeze ao cluster e retorna o job_id imediatamente.

        Use :meth:`wait` para aguardar a conclusão.

        Args:
            df: DataFrame pandas (salvo localmente antes de enviar ao cluster).
            name: Nome do arquivo de saída.
            **kwargs: Parâmetros extras (``codec``, ``quant``, etc.).

        Returns:
            ``job_id`` string para monitoramento via :meth:`wait` ou
            :meth:`~permafrost.cluster.PermafrostClient.status`.

        Raises:
            RuntimeError: Se ``cluster`` não estiver configurado.
        """
        codec = kwargs.pop("codec", self.default_codec)
        quant = kwargs.pop("quant", self.default_quant)
        uri   = self._resolve_uri(name)
        return self.client.freeze(uri, uri, token=self.token, codec=codec, quant=quant)

    def wait(self, job_id: str, poll_interval: float = 2.0) -> dict:
        """Aguarda a conclusão de um job do cluster.

        Args:
            job_id: ID retornado por :meth:`freeze_async`.
            poll_interval: Intervalo de polling em segundos.

        Returns:
            Dicionário com status final do job (``ratio``, ``workers_used``, etc.).
        """
        return self.client.wait(job_id, token=self.token, poll_interval=poll_interval)

    # ── UNFREEZE ──────────────────────────────────────────────────────────────

    def unfreeze(self, name: str, **kwargs):
        """Descomprime um arquivo do storage configurado.

        Args:
            name: Nome do arquivo (com ou sem ``.permafrost``).
            **kwargs: Parâmetros extras para :func:`~permafrost.unfreeze`
                (``filter``, ``verify``, ``key``, etc.).

        Returns:
            ``pd.DataFrame`` com os dados descomprimidos.

        Examples::

            df = ctx.unfreeze("vendas_2024")
            df_2023 = ctx.unfreeze("vendas_2024", filter={"ano": 2023})
        """
        from permafrost.storage import thaw_from
        uri = self._resolve_uri(name)
        key = kwargs.pop("key", self.key)
        if key is not None:
            kwargs["key"] = key
        return thaw_from(uri, adapter=self.adapter, **kwargs)

    def thaw(self, *args, **kwargs):
        """Deprecated: use ``unfreeze()`` instead. Will be removed in v2.0."""
        import warnings
        warnings.warn(
            "PermafrostContext.thaw() is deprecated. Use unfreeze() instead.",
            DeprecationWarning, stacklevel=2,
        )
        return self.unfreeze(*args, **kwargs)

    # ── AUDIT ─────────────────────────────────────────────────────────────────

    def audit(self, name: str) -> dict:
        """Inspeciona metadados de um arquivo sem descomprimir.

        Usa range requests para storage remoto — sem download completo.

        Args:
            name: Nome do arquivo.

        Returns:
            Dicionário com ``codec``, ``quant``, ``orig_rows``, ``n_chunks``,
            ``columns``, ``partition_col``, etc.

        Examples::

            info = ctx.audit("vendas_2024")
            print(f"Ratio: {info['ratio']:.2f}×  Codec: {info['codec']}")
        """
        from permafrost.storage import audit_remote
        uri = self._resolve_uri(name)
        return audit_remote(uri, adapter=self.adapter)

    # ── LIST ──────────────────────────────────────────────────────────────────

    def list(self, pattern: str = "*.permafrost") -> list[str]:
        """Lista arquivos ``.permafrost`` no storage configurado.

        Args:
            pattern: Glob pattern para filtrar nomes.

        Returns:
            Lista de URIs ordenadas.
        """
        base = self.storage_uri or "."
        return self.adapter.list(base, pattern=pattern)

    # ── CATALOG DELEGATION ────────────────────────────────────────────────────

    def register(self, path: str, tags: list = None) -> dict:
        """Registra um arquivo ``.permafrost`` no catalog.

        Args:
            path: Caminho local do arquivo.
            tags: Lista de tags para categorização.

        Returns:
            Dicionário com ``status``, ``name``, ``rows``, etc.
        """
        return self.catalog.register(path, tags=tags)

    def search(self, **kwargs):
        """Busca datasets no catalog com filtros opcionais.

        Delega para :meth:`~permafrost.catalog.PermafrostCatalog.search`.

        Args:
            **kwargs: Filtros: ``name``, ``codec``, ``partition_key``,
                ``lossless_only``, ``min_rows``, ``max_mb``, ``tags_contain``.

        Returns:
            ``pd.DataFrame`` com os resultados.

        Examples::

            ctx.search(name="vendas", lossless_only=True)
            ctx.search(codec="zstd", min_rows=10_000)
        """
        return self.catalog.search(**kwargs)

    def cost_report(self, tier: str = "glacier_deep"):
        """Relatório de custo estimado por tier de storage.

        Delega para :meth:`~permafrost.catalog.PermafrostCatalog.cost_report`.

        Args:
            tier: Tier de storage (``"s3_standard"``, ``"s3_ia"``,
                ``"glacier"``, ``"glacier_deep"``).

        Returns:
            ``pd.DataFrame`` com custo por dataset e totais.
        """
        return self.catalog.cost_report(tier)

    def integrity_check(self, name_filter: str = None):
        """Verifica SHA-256 de todos os datasets no catalog.

        Delega para :meth:`~permafrost.catalog.PermafrostCatalog.integrity_check`.

        Args:
            name_filter: Filtrar por nome (substring match).

        Returns:
            ``pd.DataFrame`` com status por dataset.
        """
        return self.catalog.integrity_check(name_filter=name_filter)

    def stats(self) -> dict:
        """Métricas agregadas de todos os datasets no catalog.

        Returns:
            Dicionário com ``total_datasets``, ``total_rows``, ``total_mb``,
            ``lossless_count``, etc.
        """
        return self.catalog.stats()

    def sql(self, query: str):
        """Executa SQL direto no DuckDB do catalog.

        Args:
            query: Query SQL a executar.

        Returns:
            ``pd.DataFrame`` com os resultados.

        Examples::

            ctx.sql("SELECT codec, COUNT(*) FROM datasets GROUP BY codec")
        """
        return self.catalog.sql(query)

    # ── CONTEXT MANAGER ───────────────────────────────────────────────────────

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self) -> None:
        """Fecha conexões abertas (catalog DuckDB, cluster httpx)."""
        if self._catalog is not None:
            try:
                self._catalog.con.close()
            except Exception:
                pass
        if self._client is not None:
            try:
                self._client.__del__()
            except Exception:
                pass

    # ── REPR ──────────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        parts = []
        if self.catalog_path:
            parts.append(f"catalog={self.catalog_path!r}")
        if self.storage_uri:
            parts.append(f"storage={self.storage_uri!r}")
        if self.cluster_url:
            parts.append(f"cluster={self.cluster_url!r}")
        codec_name = {0x01: "ZSTD", 0x02: "LZMA2", 0x03: "ZPAQ"}.get(
            self.default_codec, str(self.default_codec)
        )
        parts.append(f"codec={codec_name}")
        return f"PermafrostContext({', '.join(parts)})"
