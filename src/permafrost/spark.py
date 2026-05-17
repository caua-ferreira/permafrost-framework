"""
PermafrostDataSource — Apache Spark DataSource API v2 (Python)
Disponível desde PySpark 4.0+.

Uso:
  spark.read.format("permafrost").load("dados.permafrost")
  spark.read.format("permafrost").load("s3://bucket/dados.permafrost")
  spark.read.format("permafrost") \\
      .option("partition_col", "ano") \\
      .option("partition_val", "2023") \\
      .load("vendas.permafrost")

  df.write.format("permafrost") \\
      .option("codec", "lzma2") \\
      .option("partition_by", "ano") \\
      .save("saida.permafrost")

Registro:
  from permafrost.spark import register
  register(spark)
  # Depois:
  spark.read.format("permafrost").load("dados.permafrost")
"""
from __future__ import annotations
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

import os

# ── Verificar PySpark disponível ─────────────────────────────────────────────
try:
    from pyspark.sql import SparkSession
    from pyspark.sql.datasource import (
        DataSource, DataSourceReader, DataSourceWriter,
        InputPartition, WriterCommitMessage,
    )
    from pyspark.sql.types import (
        StructType, StructField, StringType, LongType,
        DoubleType, TimestampType, BooleanType, IntegerType,
    )
    import pyspark.sql.functions as F
    HAS_PYSPARK = True
except ImportError:
    HAS_PYSPARK = False
    DataSource = object
    DataSourceReader = object
    DataSourceWriter = object


# ── Partition ─────────────────────────────────────────────────────────────────
class _PermafrostPartition(InputPartition if HAS_PYSPARK else object):
    """Representa um chunk do arquivo .permafrost como partição Spark."""
    def __init__(self, path: str, chunk_id: int, byte_offset: int,
                 byte_len: int, sha256: str, row_start: int, row_end: int):
        self.path        = path
        self.chunk_id    = chunk_id
        self.byte_offset = byte_offset
        self.byte_len    = byte_len
        self.sha256      = sha256
        self.row_start   = row_start
        self.row_end     = row_end

    def __repr__(self):
        return (f"PermafrostPartition(chunk={self.chunk_id}, "
                f"rows={self.row_start}-{self.row_end})")


# ── WriterCommitMessage ───────────────────────────────────────────────────────
class _PermafrostCommit(WriterCommitMessage if HAS_PYSPARK else object):
    def __init__(self, path: str, rows: int, stored_mb: float):
        self.path      = path
        self.rows      = rows
        self.stored_mb = stored_mb


# ── DataSourceReader ──────────────────────────────────────────────────────────
class _PermafrostReader(DataSourceReader if HAS_PYSPARK else object):
    """
    Leitor do Permafrost para Spark.

    Cada chunk do arquivo vira uma partição Spark independente.
    Suporta pushdown de filtros por partition_key (sparse index).
    """

    def __init__(self, schema: "StructType", options: Dict[str, str]):
        self.schema    = schema
        self.path      = options.get("path", options.get("paths", ""))
        self.verify    = options.get("verify", "true").lower() == "true"
        # Filtros de partição (pushdown)
        self._filter_col: Optional[str] = None
        self._filter_val: Optional[str] = None
        # Partições calculadas no pushFilters/partitions
        self._index_entries: Optional[list] = None

    def pushFilters(self, filters: list) -> list:
        """
        Recebe filtros do Spark e tenta empurrar para o sparse index.
        Filtros suportados: EqualTo sobre a coluna de partição.
        Retorna os filtros não suportados (Spark avalia esses).
        """
        from pyspark.sql.datasource import EqualTo
        unsupported = []
        audit_info = _audit_cached(self.path)
        partition_col = audit_info.get("partition_col")

        for f in filters:
            if isinstance(f, EqualTo):
                # .attribute é ColumnPath — tuple de strings (ex: ("ano",))
                attr = f.attribute
                col_name = attr[-1] if isinstance(attr, (tuple, list)) else str(attr)
                if partition_col and col_name == partition_col:
                    self._filter_col = col_name
                    self._filter_val = str(f.value)
                    continue   # filtro tratado — não repassar para o Spark
            unsupported.append(f)
        return unsupported

    def partitions(self) -> Sequence[_PermafrostPartition]:
        """
        Mapeia cada chunk do sparse index como uma partição Spark.
        Aplica o filtro de partition_key se disponível.
        """
        from permafrost.codec import _read_header, _read_sparse_index, MAGIC, EOF_MAGIC

        # Suporte a múltiplos paths (glob)
        paths = _resolve_paths(self.path)
        parts = []

        for path in paths:
            with open(path, "rb") as f:
                raw = f.read()
            index = _read_sparse_index(raw)

            for entry in index:
                # Filtro por partition key (pushdown)
                if self._filter_col and self._filter_val:
                    if self._filter_col == entry["part_col"]:
                        if self._filter_val not in entry["part_key"]:
                            continue   # pular este chunk

                parts.append(_PermafrostPartition(
                    path=path,
                    chunk_id=entry["chunk_id"],
                    byte_offset=entry["byte_offset"],
                    byte_len=entry["byte_len"],
                    sha256=entry["sha256"],
                    row_start=entry["row_start"],
                    row_end=entry["row_end"],
                ))

        return parts if parts else [_PermafrostPartition(
            path=paths[0] if paths else self.path,
            chunk_id=-1, byte_offset=0, byte_len=0,
            sha256="", row_start=0, row_end=0,
        )]

    def read(self, partition: _PermafrostPartition) -> Iterator[Tuple]:
        """
        Lê um chunk do arquivo e retorna como iterator de RecordBatches (PyArrow).
        Cada chunk = 1 tarefa Spark executada em paralelo.
        """
        import pyarrow as pa
        from permafrost.codec import (
            _read_header, _read_sparse_index, _decompress, _parse_chunk,
            _sha256,
        )

        # Chunk inválido (edge case: arquivo vazio)
        if partition.chunk_id == -1:
            return

        with open(partition.path, "rb") as f:
            raw = f.read()

        h = _read_header(raw)

        blob = raw[partition.byte_offset: partition.byte_offset + partition.byte_len]

        if self.verify:
            computed = _sha256(blob).hex()
            if computed != partition.sha256:
                raise ValueError(
                    f"Chunk {partition.chunk_id} corrompido — SHA-256 não confere"
                )

        chunk_raw = _decompress(blob, h["codec"])
        n_rows    = partition.row_end - partition.row_start + 1
        df_chunk  = _parse_chunk(chunk_raw, h["manifests"], n_rows)

        # Retornar como PyArrow RecordBatch normalizado para Spark 4
        # Spark requer: timestamp[us], string (não large_string), sem dictionary
        table = pa.Table.from_pandas(df_chunk, preserve_index=False)
        table = _normalize_for_spark(table)
        for batch in table.to_batches():
            yield batch


# ── DataSourceWriter ──────────────────────────────────────────────────────────
class _PermafrostWriter(DataSourceWriter if HAS_PYSPARK else object):
    """
    Escritor do Permafrost para Spark.
    Cada executor escreve um arquivo .permafrost parcial;
    o driver faz merge dos metadados no commit().
    """

    def __init__(self, options: Dict[str, str]):
        from permafrost.codec import CODEC_LZMA2, CODEC_ZSTD, QUANT_NONE, QUANT_MEDIUM
        codec_map = {"lzma2": CODEC_LZMA2, "zstd": CODEC_ZSTD}
        quant_map = {"none": QUANT_NONE, "medium": QUANT_MEDIUM, "high": 0x01, "low": 0x03}

        self.output_path   = options.get("path", "output.permafrost")
        self.codec         = codec_map.get(options.get("codec", "lzma2"), CODEC_LZMA2)
        self.quant         = quant_map.get(options.get("quant", "none"), QUANT_NONE)
        self.partition_by  = options.get("partition_by")
        self.chunk_rows    = int(options.get("chunk_rows", "10000"))
        self.comment       = options.get("comment", "Written by Spark")

    def write(self, iterator: Iterator) -> _PermafrostCommit:
        """
        Executado em cada executor Spark.
        Recebe todas as linhas desta partição e grava um .permafrost parcial.
        """
        import pandas as pd
        from permafrost.codec import freeze

        rows = list(iterator)
        if not rows:
            return _PermafrostCommit(path="", rows=0, stored_mb=0)

        df = pd.DataFrame([r.asDict() for r in rows])

        # Path único por tarefa (evita conflito entre executores)
        import os, uuid
        task_path = self.output_path + f".part_{uuid.uuid4().hex[:8]}.permafrost"

        if self.partition_by and self.partition_by in df.columns:
            df = df.sort_values(self.partition_by).reset_index(drop=True)

        m = freeze(df, task_path,
                   codec=self.codec, quant=self.quant,
                   partition_by=self.partition_by,
                   chunk_rows=self.chunk_rows,
                   comment=self.comment)

        return _PermafrostCommit(path=task_path, rows=m["rows"], stored_mb=m["stored_mb"])

    def commit(self, messages: list) -> None:
        """
        Executado no driver após todas as tarefas concluírem.
        Mescla os arquivos parciais em um único .permafrost.
        """
        import pandas as pd
        from permafrost.codec import freeze, unfreeze

        valid = [m for m in messages if m and m.path and os.path.exists(m.path)]
        if not valid:
            return

        if len(valid) == 1:
            # Apenas 1 parte — renomear diretamente
            os.rename(valid[0].path, self.output_path)
            return

        # Múltiplas partes — unfreeze e re-freeze para arquivo único
        dfs = [unfreeze(m.path, verify=False) for m in valid]
        import pandas as pd
        df_merged = pd.concat(dfs, ignore_index=True)

        if self.partition_by and self.partition_by in df_merged.columns:
            df_merged = df_merged.sort_values(self.partition_by).reset_index(drop=True)

        freeze(df_merged, self.output_path,
               codec=self.codec, quant=self.quant,
               partition_by=self.partition_by,
               chunk_rows=self.chunk_rows,
               comment=self.comment)

        # Limpar partes temporárias
        for m in valid:
            try: os.remove(m.path)
            except: pass

    def abort(self, messages: list) -> None:
        """Limpa arquivos parciais em caso de falha."""
        for m in messages:
            if m and m.path and os.path.exists(m.path):
                try: os.remove(m.path)
                except: pass



def _normalize_for_spark(table: "pa.Table") -> "pa.Table":
    """
    Normaliza tipos PyArrow para compatibilidade com Spark 4.

    Conversões aplicadas:
    - ``timestamp[s]`` → ``timestamp[us]`` (Spark não aceita timestamp[s])
    - ``large_string`` → ``string`` (Spark não aceita LargeStringArray)
    - ``large_binary`` → ``binary``
    - ``dictionary<values=string>`` → ``string`` (sem dict encoding)
    """
    import pyarrow as pa
    new_cols = {}
    new_fields = []
    for i, field in enumerate(table.schema):
        col  = table.column(i)
        ftype = field.type
        # timestamp[s] → timestamp[us]
        if pa.types.is_timestamp(ftype) and ftype.unit == 's':
            col   = col.cast(pa.timestamp('us'))
            ftype = pa.timestamp('us')
        # large_string → string
        elif pa.types.is_large_string(ftype):
            col   = col.cast(pa.string())
            ftype = pa.string()
        # large_binary → binary
        elif pa.types.is_large_binary(ftype):
            col   = col.cast(pa.binary())
            ftype = pa.binary()
        # dictionary → valores concretos
        elif pa.types.is_dictionary(ftype):
            col   = col.cast(ftype.value_type)
            ftype = ftype.value_type
        new_cols[field.name] = col
        new_fields.append(pa.field(field.name, ftype))
    return pa.table(new_cols, schema=pa.schema(new_fields))

# ── DataSource principal ──────────────────────────────────────────────────────
class PermafrostDataSource(DataSource if HAS_PYSPARK else object):
    """
    Spark DataSource API v2 para o formato .permafrost.

    Registrar:
        from permafrost.spark import register
        register(spark)

    Ler:
        df = spark.read.format("permafrost").load("vendas.permafrost")
        df = spark.read.format("permafrost") \\
                  .option("partition_col", "ano") \\
                  .load("s3://bucket/vendas.permafrost")

    Escrever:
        df.write.format("permafrost") \\
           .option("codec", "lzma2") \\
           .option("partition_by", "ano") \\
           .save("saida.permafrost")

    Leitura com filtro pushdown (usa o sparse index):
        df = spark.read.format("permafrost").load("vendas.permafrost")
        df.filter(df.ano == 2023).show()
        # → Spark empurra o filtro para o sparse index
        # → Apenas os chunks de 2023 são lidos
    """

    @classmethod
    def name(cls) -> str:
        return "permafrost"

    def schema(self) -> "StructType":
        """
        Infere o schema do arquivo .permafrost lendo apenas o header.
        Zero decompressão.
        """
        from permafrost.codec import audit
        from pyspark.sql.types import (
            StructType, StructField, StringType, LongType,
            DoubleType, TimestampType, BooleanType, IntegerType,
            ShortType,
        )

        path = self.options.get("path", self.options.get("paths", ""))
        paths = _resolve_paths(path)
        if not paths:
            raise ValueError(f"Nenhum arquivo .permafrost encontrado: {path}")

        info    = audit(paths[0])
        fields  = []

        for col, manifest in info.get("index_entries", [{}])[0].items() if False else []:
            pass

        # Usar os manifests do header para inferir tipos
        from permafrost.codec import _read_header, PRED_TS, PRED_LAG1, PRED_CATEGORY, PRED_DELTA, PRED_RAW
        with open(paths[0], "rb") as f:
            raw = f.read()
        h = _read_header(raw)

        PRED_TO_SPARK = {
            PRED_TS:       TimestampType(),
            PRED_LAG1:     DoubleType(),
            PRED_CATEGORY: StringType(),
            PRED_DELTA:    LongType(),
            PRED_RAW:      StringType(),
        }

        for col_name, manifest in h["manifests"].items():
            pred      = manifest.get("predictor", PRED_RAW)
            spark_type = PRED_TO_SPARK.get(pred, StringType())
            fields.append(StructField(col_name, spark_type, nullable=True))

        return StructType(fields)

    def reader(self, schema: "StructType") -> "_PermafrostReader":
        return _PermafrostReader(schema, self.options)

    def writer(self, schema: "StructType", overwrite: bool) -> "_PermafrostWriter":
        return _PermafrostWriter(self.options)


# ── Helpers ───────────────────────────────────────────────────────────────────
_audit_cache: Dict[str, dict] = {}

def _audit_cached(path: str) -> dict:
    """Cache de audit para evitar re-leitura do header em cada chamada."""
    if path not in _audit_cache:
        from permafrost.codec import audit
        _audit_cache[path] = audit(path)
    return _audit_cache[path]


def _resolve_paths(path_str: str) -> list:
    """Resolve path único, glob ou lista de paths."""
    import glob as _glob
    if not path_str:
        return []
    # Lista separada por vírgula
    if "," in path_str:
        return [p.strip() for p in path_str.split(",") if p.strip()]
    # Glob
    if "*" in path_str or "?" in path_str:
        return sorted(_glob.glob(path_str))
    # Path único
    return [path_str]


# ── Função de registro ────────────────────────────────────────────────────────
def register(spark: "SparkSession") -> None:
    """
    Registra o PermafrostDataSource no SparkSession.

    Após o registro, use ``spark.read.format("permafrost")`` para ler
    e ``df.write.format("permafrost")`` para escrever.

    Args:
        spark: SparkSession ativa.

    Example:
        >>> from permafrost.spark import register
        >>> register(spark)
        >>> df = spark.read.format("permafrost").load("vendas.permafrost")
        >>> df.filter(df.ano == 2023).show()
    """
    if not HAS_PYSPARK:
        raise ImportError(
            "PySpark não instalado. "
            "Instale com: pip install pyspark>=4.0"
        )
    spark.dataSource.register(PermafrostDataSource)
    print("✓ PermafrostDataSource registrado — use spark.read.format('permafrost')")


print("permafrost.spark OK")
print("  Classes: PermafrostDataSource, _PermafrostReader, _PermafrostWriter")
print("  Funções: register(spark)")
