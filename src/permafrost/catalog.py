"""
PermafrostCatalog v1.0
Índice centralizado de arquivos .permafrost usando DuckDB.

Features:
  - register(path)        → indexa um arquivo lendo apenas header+footer
  - register_dir(dir)     → registra todos os .permafrost de um diretório
  - search(...)           → query SQL-like com filtros
  - thaw(...)             → thaw seletivo usando o catalog como roteador
  - cost_report()         → custo estimado por tier de storage
  - integrity_check()     → verifica SHA-256 de todos os arquivos registrados
  - stats()               → métricas gerais do catalog
"""

import os, json, hashlib, time, re
import duckdb
import pandas as pd
import numpy as np

# Importar o codec

from permafrost.codec import audit as pf_audit, thaw as pf_thaw

# ── STORAGE PRICING ($/GB/mês) ────────────────────────────────────────────────
STORAGE_PRICES = {
    's3_standard': 0.023,
    's3_ia':       0.0125,
    'glacier':     0.004,
    'glacier_deep':0.00099,
}

CATALOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS datasets (
    id              INTEGER PRIMARY KEY,
    name            VARCHAR NOT NULL,
    path            VARCHAR NOT NULL UNIQUE,
    registered_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    freeze_date     TIMESTAMP,
    codec           VARCHAR,
    quant_level     INTEGER,
    orig_rows       BIGINT,
    n_chunks        INTEGER,
    chunk_rows      INTEGER,
    file_size_bytes BIGINT,
    file_size_mb    DOUBLE,
    partition_col   VARCHAR,
    partition_keys  VARCHAR,     -- JSON array
    columns         VARCHAR,     -- JSON array
    comment         VARCHAR,
    tags            VARCHAR,     -- JSON array
    schema_hash     VARCHAR,     -- SHA-256 dos nomes das colunas
    last_verified   TIMESTAMP,
    verified_ok     BOOLEAN
);

CREATE TABLE IF NOT EXISTS chunks (
    id          INTEGER PRIMARY KEY,
    dataset_id  INTEGER REFERENCES datasets(id),
    chunk_id    INTEGER,
    row_start   BIGINT,
    row_end     BIGINT,
    part_key    VARCHAR,
    part_col    VARCHAR,
    byte_offset BIGINT,
    byte_len    BIGINT,
    sha256      VARCHAR
);

CREATE SEQUENCE IF NOT EXISTS dataset_seq START 1;
CREATE SEQUENCE IF NOT EXISTS chunk_seq   START 1;
"""

class PermafrostCatalog:
    """Índice centralizado de arquivos `.permafrost` usando DuckDB embedded.

    Registra metadados de arquivos ``.permafrost`` lendo apenas o header e o
    sparse index (zero decompressão). Permite busca por schema, período, codec,
    estimativa de custo e verificação de integridade.

    O banco DuckDB tem duas tabelas:

    - ``datasets`` — um registro por arquivo, espelha o header do ``.permafrost``
    - ``chunks`` — um registro por chunk, espelha o sparse index (habilita seeks diretos)

    Example:
        >>> import permafrost as pf
        >>> cat = pf.PermafrostCatalog(".permafrost_catalog.db")
        >>> cat.register_dir("/dados/cold/", tags=["producao"])
        >>> cat.search(partition_key="2023", lossless_only=True)
        >>> cat.cost_report("glacier_deep")
        >>> cat.integrity_check()
    """

    def __init__(self, catalog_path: str = ".permafrost_catalog.db"):
        """Abre (ou cria) o catálogo DuckDB no caminho especificado.

        O catálogo indexa arquivos ``.permafrost`` lendo apenas o header e o
        sparse index — zero decompressão. Todas as consultas são SQL DuckDB.

        Args:
            catalog_path: Caminho do arquivo DuckDB. Use ``":memory:"`` para
                testes (dados não persistidos). Padrão: ``".permafrost_catalog.db"``.

        Example:
            >>> cat = PermafrostCatalog(".permafrost_catalog.db")
            >>> cat = PermafrostCatalog(":memory:")  # testes
        """
        self.catalog_path = catalog_path
        self.con = duckdb.connect(catalog_path)
        self.con.execute(CATALOG_SCHEMA)
        self._print_header()

    def _print_header(self):
        n = self.con.execute("SELECT COUNT(*) FROM datasets").fetchone()[0]
        print(f"PermafrostCatalog  →  {self.catalog_path}")
        print(f"  {n} dataset(s) registrado(s)\n")

    # ── REGISTER ──────────────────────────────────────────────────────────────
    def register(self, path: str, tags: list = None, name: str = None) -> dict:
        """
        Registra um arquivo .permafrost lendo apenas header + sparse index.
        Não descomprime nenhum chunk.
        """
        if not os.path.exists(path):
            raise FileNotFoundError(f"Arquivo não encontrado: {path}")

        # Verificar se já está registrado
        existing = self.con.execute(
            "SELECT id FROM datasets WHERE path = ?", [path]
        ).fetchone()
        if existing:
            return {'status': 'already_registered', 'path': path, 'id': existing[0]}

        # Ler metadados via audit() — zero decompressão
        info = pf_audit(path)

        # Derivar campos
        ds_name     = name or os.path.splitext(os.path.basename(path))[0]
        schema_hash = hashlib.sha256(
            json.dumps(sorted(info['columns'])).encode()
        ).hexdigest()[:16]
        part_keys   = json.dumps(info.get('partition_keys', []))
        columns_j   = json.dumps(info['columns'])
        tags_j      = json.dumps(tags or [])
        freeze_ts   = info['freeze_date']

        # Inserir dataset
        ds_id = self.con.execute("SELECT nextval('dataset_seq')").fetchone()[0]
        self.con.execute("""
            INSERT INTO datasets
            (id, name, path, freeze_date, codec, quant_level, orig_rows,
             n_chunks, chunk_rows, file_size_bytes, file_size_mb,
             partition_col, partition_keys, columns, comment, tags, schema_hash)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, [
            ds_id, ds_name, path, freeze_ts,
            info['codec'], info['quant'],
            info['orig_rows'], info['n_chunks'], info['chunk_rows'],
            int(info['file_size_mb'] * 1e6), info['file_size_mb'],
            info.get('partition_col'), part_keys,
            columns_j, info.get('comment',''), tags_j, schema_hash,
        ])

        # Inserir chunks do sparse index
        for entry in info.get('index_entries', []):
            chunk_id = self.con.execute("SELECT nextval('chunk_seq')").fetchone()[0]
            self.con.execute("""
                INSERT INTO chunks
                (id, dataset_id, chunk_id, row_start, row_end,
                 part_key, part_col, byte_offset, byte_len, sha256)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, [
                chunk_id, ds_id,
                entry['chunk_id'], entry['row_start'], entry['row_end'],
                entry['part_key'], entry['part_col'],
                entry['byte_offset'], entry['byte_len'], entry['sha256'],
            ])

        return {
            'status': 'registered', 'id': ds_id, 'name': ds_name,
            'path': path, 'rows': info['orig_rows'],
            'file_mb': info['file_size_mb'], 'n_chunks': info['n_chunks'],
        }

    def register_dir(self, directory: str, tags: list = None, recursive: bool = False) -> list:
        """Registra todos os .permafrost de um diretório."""
        results = []
        walk = os.walk(directory) if recursive else [(directory, [], os.listdir(directory))]
        for root, _, files in walk:
            for fname in sorted(files):
                if fname.endswith('.permafrost'):
                    path = os.path.join(root, fname)
                    try:
                        r = self.register(path, tags=tags)
                        results.append(r)
                        status = r['status']
                        if status == 'registered':
                            print(f"  ✓ {fname:30s} {r.get('rows',0):>8,} linhas | {r.get('file_mb',0):.3f} MB")
                        else:
                            print(f"  ~ {fname:30s} já registrado")
                    except Exception as e:
                        print(f"  ✗ {fname}: {e}")
                        results.append({'status': 'error', 'path': path, 'error': str(e)})
        return results

    # ── SEARCH ────────────────────────────────────────────────────────────────
    def search(self,
               name: str = None,
               codec: str = None,
               partition_col: str = None,
               partition_key: str = None,
               columns_contain: str = None,
               min_rows: int = None,
               max_mb: float = None,
               tags_contain: str = None,
               lossless_only: bool = False) -> pd.DataFrame:
        """
        Busca datasets no catalog com filtros opcionais.
        Retorna DataFrame com os resultados.
        """
        conditions = ["1=1"]
        params = []

        if name:
            conditions.append("name LIKE ?")
            params.append(f"%{name}%")
        if codec:
            conditions.append("codec = ?")
            params.append(codec)
        if partition_col:
            conditions.append("partition_col = ?")
            params.append(partition_col)
        if partition_key:
            conditions.append("partition_keys LIKE ?")
            params.append(f"%{partition_key}%")
        if columns_contain:
            conditions.append("columns LIKE ?")
            params.append(f"%{columns_contain}%")
        if min_rows:
            conditions.append("orig_rows >= ?")
            params.append(min_rows)
        if max_mb:
            conditions.append("file_size_mb <= ?")
            params.append(max_mb)
        if tags_contain:
            conditions.append("tags LIKE ?")
            params.append(f"%{tags_contain}%")
        if lossless_only:
            conditions.append("quant_level = 0")

        where = " AND ".join(conditions)
        sql = f"""
            SELECT id, name, codec, quant_level as quant,
                   orig_rows as rows, n_chunks, file_size_mb as mb,
                   partition_col, freeze_date, comment
            FROM datasets
            WHERE {where}
            ORDER BY freeze_date DESC
        """
        return self.con.execute(sql, params).df()

    def search_chunks(self, dataset_name: str, part_key: str = None) -> pd.DataFrame:
        """Busca chunks de um dataset com filtro por partition key."""
        sql = """
            SELECT c.chunk_id, c.row_start, c.row_end,
                   c.part_key, c.byte_offset, c.byte_len,
                   c.sha256, round(c.byte_len/1024.0, 1) as kb
            FROM chunks c
            JOIN datasets d ON c.dataset_id = d.id
            WHERE d.name LIKE ?
        """
        params = [f"%{dataset_name}%"]
        if part_key:
            sql += " AND c.part_key LIKE ?"
            params.append(f"%{part_key}%")
        sql += " ORDER BY c.chunk_id"
        return self.con.execute(sql, params).df()

    # ── THAW via CATALOG ──────────────────────────────────────────────────────
    def thaw(self, name: str, filter: dict = None, row_range: tuple = None,
             verify: bool = True) -> pd.DataFrame:
        """
        Encontra o dataset pelo nome e executa thaw com seleção via sparse index.
        """
        result = self.con.execute(
            "SELECT path, partition_col FROM datasets WHERE name LIKE ? LIMIT 1",
            [f"%{name}%"]
        ).fetchone()
        if not result:
            raise KeyError(f"Dataset '{name}' não encontrado no catalog. Use search() para listar.")
        path, part_col = result

        # Adaptar filtro para a coluna de partição correta
        if filter and part_col and part_col != '__rows__':
            # Garantir que o filtro usa a coluna correta
            pass

        print(f"  thaw: {os.path.basename(path)}", end="")
        t0 = time.time()
        df = pf_thaw(path, verify=verify, filter=filter, row_range=row_range)
        tt = time.time() - t0
        print(f" → {len(df):,} linhas em {tt:.3f}s")
        return df

    # ── COST REPORT ───────────────────────────────────────────────────────────
    def cost_report(self, tier: str = 'glacier_deep') -> pd.DataFrame:
        """
        Relatório de custo estimado de storage por dataset.
        tier: s3_standard | s3_ia | glacier | glacier_deep
        """
        price = STORAGE_PRICES.get(tier, 0.00099)
        sql = """
            SELECT
                name,
                codec,
                CASE quant_level
                    WHEN 0 THEN 'lossless'
                    WHEN 1 THEN 'high'
                    WHEN 2 THEN 'medium'
                    ELSE 'low'
                END as quant,
                orig_rows as rows,
                round(file_size_mb, 3) as size_mb,
                n_chunks,
                freeze_date
            FROM datasets
            ORDER BY file_size_mb DESC
        """
        df = self.con.execute(sql).df()
        df['cost_monthly_usd'] = (df['size_mb'] / 1024) * price
        df['cost_annual_usd']  = df['cost_monthly_usd'] * 12
        df['cost_3yr_usd']     = df['cost_monthly_usd'] * 36
        df['tier']             = tier
        return df

    # ── INTEGRITY CHECK ───────────────────────────────────────────────────────
    def integrity_check(self, name_filter: str = None) -> pd.DataFrame:
        """
        Verifica integridade (SHA-256) de todos os chunks de todos os datasets.
        Não descomprime — apenas confere os hashes dos blobs comprimidos.
        """
        sql = "SELECT id, name, path FROM datasets"
        params = []
        if name_filter:
            sql += " WHERE name LIKE ?"
            params.append(f"%{name_filter}%")

        datasets_rows = self.con.execute(sql, params).fetchall()
        results = []

        for ds_id, ds_name, path in datasets_rows:
            if not os.path.exists(path):
                results.append({'name': ds_name, 'path': path, 'status': 'FILE_MISSING',
                                'chunks_ok': 0, 'chunks_fail': 0})
                continue

            with open(path, 'rb') as f:
                raw = f.read()

            chunks = self.con.execute(
                "SELECT chunk_id, byte_offset, byte_len, sha256 FROM chunks WHERE dataset_id = ?",
                [ds_id]
            ).fetchall()

            ok_count = fail_count = 0
            for chunk_id, offset, length, sha_stored in chunks:
                blob = raw[offset: offset + length]
                sha_computed = hashlib.sha256(blob).hexdigest()
                if sha_computed == sha_stored:
                    ok_count += 1
                else:
                    fail_count += 1

            status = 'OK' if fail_count == 0 else 'CORRUPTED'
            self.con.execute("""
                UPDATE datasets SET last_verified = CURRENT_TIMESTAMP, verified_ok = ?
                WHERE id = ?
            """, [fail_count == 0, ds_id])
            results.append({
                'name': ds_name, 'status': status,
                'chunks_ok': ok_count, 'chunks_fail': fail_count,
                'path': path,
            })

        return pd.DataFrame(results)

    # ── STATS ─────────────────────────────────────────────────────────────────
    def stats(self) -> dict:
        """Retorna métricas agregadas de todos os datasets registrados.

        Returns:
            Dicionário com::

                {
                    "total_datasets":      4,
                    "total_rows":          540000,
                    "total_mb":            2.964,
                    "total_chunks":        54,
                    "avg_mb_per_1k_rows":  0.0055,
                    "distinct_codecs":     2,
                    "lossless_count":      3,
                    "vault_count":         1,
                }

        Example:
            >>> s = cat.stats()
            >>> print(f"{s['total_datasets']} datasets, {s['total_rows']:,} linhas")
        """
        r = self.con.execute("""
            SELECT
                COUNT(*)                          as total_datasets,
                SUM(orig_rows)                    as total_rows,
                SUM(file_size_mb)                 as total_mb,
                SUM(n_chunks)                     as total_chunks,
                AVG(file_size_mb/NULLIF(orig_rows/1000.0,0)) as avg_mb_per_1k_rows,
                COUNT(DISTINCT codec)             as distinct_codecs,
                COUNT(DISTINCT partition_col)     as distinct_partitions,
                SUM(CASE WHEN quant_level=0 THEN 1 ELSE 0 END) as lossless_count,
                SUM(CASE WHEN quant_level>0 THEN 1 ELSE 0 END) as vault_count
            FROM datasets
        """).fetchone()

        labels = ['total_datasets','total_rows','total_mb','total_chunks',
                  'avg_mb_per_1k_rows','distinct_codecs','distinct_partitions',
                  'lossless_count','vault_count']
        return dict(zip(labels, r))

    # ── SQL DIRETO ────────────────────────────────────────────────────────────
    def sql(self, query: str) -> pd.DataFrame:
        """Executa SQL direto no catalog DuckDB."""
        return self.con.execute(query).df()

    def __repr__(self):
        n = self.con.execute("SELECT COUNT(*) FROM datasets").fetchone()[0]
        return f"<PermafrostCatalog path='{self.catalog_path}' datasets={n}>"


print("permafrost_catalog.py carregado")
print("  Classes: PermafrostCatalog")
print("  Métodos: register, register_dir, search, search_chunks, thaw,")
print("           cost_report, integrity_check, stats, sql")
