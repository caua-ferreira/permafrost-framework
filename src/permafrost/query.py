"""
permafrost.query — SQL engine over .permafrost / .pf files
===========================================================

Allows running SQL queries that JOIN and filter across multiple .permafrost
or .pf files, using DuckDB as the execution engine.

Sparse-index pushdown
---------------------
For simple equality / IN / BETWEEN conditions in the WHERE clause that
reference a column that was used as ``partition_by`` at freeze time, the
engine avoids decompressing chunks that cannot match — same behaviour as
``unfreeze(filter=...)``.

Usage
-----
Direct file references in SQL (no registration required):

    import permafrost as pf

    sql = (
        "SELECT c.nome, SUM(p.valor) AS total "
        "FROM 'clientes.permafrost' c "
        "JOIN 'pedidos.pf' p ON c.id = p.cliente_id "
        "WHERE c.regiao = 'Sul' "
        "GROUP BY c.nome ORDER BY total DESC"
    )
    df = pf.query(sql)

Or register aliases first:

    pf.register('clientes', 'dados/clientes.permafrost')
    pf.register('pedidos',  'dados/pedidos.pf')
    df = pf.query("SELECT * FROM clientes JOIN pedidos ON clientes.id = pedidos.cliente_id")

    # Unregister
    pf.unregister('clientes')

    # List all registered aliases
    pf.registered()
"""

from __future__ import annotations

import os
import re
import threading
from typing import Optional

import pandas as pd

from permafrost.codec import PERMAFROST_EXTENSIONS, audit
from permafrost.chunk_mode import peek

# ---------------------------------------------------------------------------
# Global alias registry (thread-safe)
# ---------------------------------------------------------------------------
_registry_lock = threading.Lock()
_registry: dict[str, str] = {}   # alias -> absolute path

# ---------------------------------------------------------------------------
# Global backend for remote path resolution
# ---------------------------------------------------------------------------
_backend_lock = threading.Lock()
_global_backend = None   # CatalogBackend instance or None

_REMOTE_PREFIXES = ('s3://', 'gs://', 'az://')


def set_query_backend(backend) -> None:
    """Set the global backend used to resolve remote paths in queries.

    Args:
        backend: A ``CatalogBackend`` instance (e.g. ``S3CatalogBackend``).
            Pass ``None`` to revert to local-only mode.

    Example::

        pf.set_query_backend(S3CatalogBackend(bucket="my-bucket"))
        pf.register('sales', 's3://my-bucket/sales.permafrost')
        df = pf.query("SELECT * FROM sales")
    """
    global _global_backend
    with _backend_lock:
        _global_backend = backend


def _get_backend():
    with _backend_lock:
        return _global_backend


def _is_remote(path: str) -> bool:
    return any(path.startswith(p) for p in _REMOTE_PREFIXES)


def _resolve_path(path: str) -> str:
    backend = _get_backend()
    if backend is not None:
        return backend.resolve_path(path)
    if _is_remote(path):
        raise ValueError(
            f"Remote path {path!r} requires a backend. "
            "Call pf.set_query_backend(...) first."
        )
    return os.path.abspath(path)


def register(alias_or_path: str, path: Optional[str] = None) -> None:
    """Register a .permafrost/.pf file under a SQL alias.

    Two calling conventions::

        pf.register('clientes', 'dados/clientes.permafrost')  # explicit alias
        pf.register('dados/clientes.permafrost')              # stem becomes alias

    Args:
        alias_or_path: Either an alias string (when ``path`` is also given) or
            the file path (alias is derived from the file stem).
        path: File path when ``alias_or_path`` is an alias.

    Raises:
        FileNotFoundError: If the resolved path does not exist.
        ValueError: If the file extension is not ``.permafrost`` or ``.pf``.
    """
    if path is None:
        # Single-arg form: derive alias from stem
        file_path = alias_or_path
        alias = os.path.splitext(os.path.basename(file_path))[0]
    else:
        alias = alias_or_path
        file_path = path

    # Keep remote URIs as-is; make local paths absolute
    stored_path = file_path if _is_remote(file_path) else os.path.abspath(file_path)
    ext = os.path.splitext(stored_path)[1].lower()
    if ext not in PERMAFROST_EXTENSIONS:
        raise ValueError(
            f"Expected a .permafrost or .pf file, got: {file_path!r}"
        )
    if not _is_remote(stored_path) and not os.path.exists(stored_path):
        raise FileNotFoundError(f"File not found: {stored_path}")

    with _registry_lock:
        _registry[alias] = stored_path


def unregister(alias: str) -> None:
    """Remove a previously registered alias.

    Args:
        alias: The alias to remove.

    Raises:
        KeyError: If the alias is not registered.
    """
    with _registry_lock:
        if alias not in _registry:
            raise KeyError(f"Alias not registered: {alias!r}")
        del _registry[alias]


def registered() -> dict[str, str]:
    """Return a copy of the current alias registry.

    Returns:
        Dict mapping alias -> absolute path.
    """
    with _registry_lock:
        return dict(_registry)


# ---------------------------------------------------------------------------
# SQL parsing helpers
# ---------------------------------------------------------------------------

# Matches quoted file paths in SQL, e.g. 'clientes.permafrost' or "pedidos.pf"
_FILE_REF_RE = re.compile(
    r'''(?i)['"]((?:[^'"]*?)\.(?:permafrost|pf))['"]''',
    re.IGNORECASE,
)


def _find_file_refs(sql: str) -> list[str]:
    """Extract all .permafrost/.pf file path strings from a SQL query."""
    return _FILE_REF_RE.findall(sql)


# SQL reserved words that must not be used as table aliases
_SQL_KEYWORDS = frozenset({
    'SELECT', 'FROM', 'WHERE', 'JOIN', 'LEFT', 'RIGHT', 'INNER', 'OUTER',
    'FULL', 'CROSS', 'ON', 'AS', 'AND', 'OR', 'NOT', 'IN', 'BETWEEN',
    'GROUP', 'BY', 'ORDER', 'HAVING', 'LIMIT', 'OFFSET', 'UNION', 'ALL',
    'DISTINCT', 'INTO', 'INSERT', 'UPDATE', 'DELETE', 'SET', 'VALUES',
    'CREATE', 'DROP', 'ALTER', 'TABLE', 'WITH', 'CASE', 'WHEN', 'THEN',
    'ELSE', 'END', 'NULL', 'TRUE', 'FALSE', 'IS', 'LIKE', 'ILIKE',
    'COUNT', 'SUM', 'AVG', 'MIN', 'MAX', 'ASC', 'DESC',
})


def _sql_alias_for_path(sql: str, file_path: str) -> Optional[str]:
    """Return the SQL table alias given to a file path, or None.
    Returns None if the captured word is a SQL keyword (no alias present).
    """
    escaped = re.escape(file_path)
    m = re.search(
        rf"(?:FROM|JOIN)\s+['\"]" + escaped + r"['\"]\s+(?:AS\s+)?(\w+)",
        sql, re.IGNORECASE,
    )
    if m:
        candidate = m.group(1)
        if candidate.upper() not in _SQL_KEYWORDS:
            return candidate
    return None


def _extract_simple_filters(sql: str, table_alias: Optional[str]) -> dict:
    """Best-effort extraction of simple WHERE conditions for one table.

    Handles:
      - ``alias.col = value``
      - ``col = value``  (only when table_alias is None)
      - ``alias.col IN (v1, v2, ...)``
      - ``alias.col BETWEEN lo AND hi``

    Returns a dict suitable for ``peek(filter=...)``.
    """
    filters: dict = {}
    if table_alias:
        prefix = re.escape(table_alias) + r'\.'
    else:
        prefix = r''

    # Equality:  alias.col = 'value'  or  alias.col = 123
    for m in re.finditer(
        rf"""{prefix}(\w+)\s*=\s*(?:'([^']*)'|"([^"]*)"|([-\d.]+))""",
        sql, re.IGNORECASE,
    ):
        col = m.group(1)
        val = m.group(2) or m.group(3) or m.group(4)
        if val is not None:
            try:
                val = int(val)
            except (ValueError, TypeError):
                try:
                    val = float(val)
                except (ValueError, TypeError):
                    pass
            filters[col] = val

    # IN list:  alias.col IN (1, 2, 3)  or  alias.col IN ('a', 'b')
    for m in re.finditer(
        rf"""{prefix}(\w+)\s+IN\s*\(([^)]+)\)""",
        sql, re.IGNORECASE,
    ):
        col = m.group(1)
        raw_vals = m.group(2)
        vals = []
        for v in re.split(r',\s*', raw_vals):
            v = v.strip().strip("'\"")
            try:
                v = int(v)
            except (ValueError, TypeError):
                try:
                    v = float(v)
                except (ValueError, TypeError):
                    pass
            vals.append(v)
        if vals:
            filters[col] = vals

    # BETWEEN:  alias.col BETWEEN lo AND hi
    for m in re.finditer(
        rf"""{prefix}(\w+)\s+BETWEEN\s+([-\d.'\"]+)\s+AND\s+([-\d.'\"]+)""",
        sql, re.IGNORECASE,
    ):
        col = m.group(1)
        lo = m.group(2).strip("'\"")
        hi = m.group(3).strip("'\"")
        for conv in (int, float, str):
            try:
                lo, hi = conv(lo), conv(hi)
                break
            except (ValueError, TypeError):
                pass
        filters[col] = (lo, hi)

    return filters


# ---------------------------------------------------------------------------
# Main query function
# ---------------------------------------------------------------------------

def query(
    sql: str,
    key=None,
    verify: bool = True,
) -> pd.DataFrame:
    """Execute a SQL query over .permafrost/.pf files.

    File references can appear in the SQL as:

    - Quoted paths: ``FROM 'clientes.permafrost' c``
    - Registered aliases: ``FROM clientes`` (after ``pf.register(...)``)

    WHERE conditions on indexed columns are pushed down to the sparse index
    before decompression, reducing I/O for selective queries.

    Args:
        sql: SQL query string. Use standard DuckDB SQL syntax.
        key: Decryption key for encrypted files (same as ``unfreeze(key=...)``).
        verify: Validate SHA-256 of each chunk (default True).

    Returns:
        ``pd.DataFrame`` with the query result.

    Raises:
        ImportError: If DuckDB is not installed.
        ValueError: If a referenced file path is not found and not registered.
        FileNotFoundError: If a referenced file does not exist.

    Examples:
        Direct path::

            df = pf.query(
                "SELECT * FROM 'vendas.permafrost' WHERE ano = 2023"
            )

        JOIN two files::

            df = pf.query(\"\"\"
                SELECT c.nome, SUM(p.valor) AS total
                FROM 'clientes.permafrost' c
                JOIN 'pedidos.pf' p ON c.id = p.cliente_id
                WHERE c.regiao = 'Sul'
                GROUP BY c.nome
            \"\"\")

        With registered aliases::

            pf.register('clientes', 'dados/clientes.permafrost')
            pf.register('pedidos',  'dados/pedidos.pf')
            df = pf.query(
                "SELECT * FROM clientes JOIN pedidos ON clientes.id = pedidos.cliente_id"
            )
    """
    try:
        import duckdb
    except ImportError as e:
        raise ImportError(
            "DuckDB is required for pf.query(). Install it with: pip install duckdb"
        ) from e

    con = duckdb.connect()

    # ── Collect all file sources to load ─────────────────────────────────────
    # 1. Quoted file paths in SQL
    file_refs = _find_file_refs(sql)

    # 2. Registered aliases that appear as unquoted table names
    with _registry_lock:
        reg_snapshot = dict(_registry)

    # Build a working SQL — we'll substitute quoted paths with view names
    working_sql = sql

    views_loaded: dict[str, pd.DataFrame] = {}  # view_name -> DataFrame

    # ── Load files referenced directly in SQL ────────────────────────────────
    for file_path in file_refs:
        if _is_remote(file_path):
            abs_path = file_path  # backend resolves it inside _load_file
        else:
            abs_path = os.path.abspath(file_path)
            if not os.path.exists(abs_path):
                raise FileNotFoundError(f"Permafrost file not found: {file_path!r}")

        alias = _sql_alias_for_path(sql, file_path)
        # Use the alias as view name; fall back to stem
        view_name = alias or os.path.splitext(os.path.basename(file_path))[0]
        # Make view name safe for DuckDB
        view_name = re.sub(r'[^a-zA-Z0-9_]', '_', view_name)
        # Avoid collision
        base_vn = view_name
        i = 0
        while view_name in views_loaded:
            i += 1
            view_name = f"{base_vn}_{i}"

        filter_dict = _extract_simple_filters(sql, alias)
        df = _load_file(abs_path, filter_dict=filter_dict, key=key, verify=verify)
        views_loaded[view_name] = df

        # Replace quoted path with view_name in the SQL, removing any alias.
        _fp = file_path  # local ref for clarity
        if alias:
            # Remove quoted path + optional "AS alias" / bare alias
            _pat = re.compile(
                r"['\"]" + re.escape(_fp) + r"['\"]" +
                r"\s+(?:AS\s+)?" + re.escape(alias),
                re.IGNORECASE,
            )
            working_sql = _pat.sub(view_name, working_sql)
        else:
            # No alias — just swap the quoted path for view_name
            working_sql = working_sql.replace("'" + _fp + "'", view_name)
            working_sql = working_sql.replace('"' + _fp + '"', view_name)

    # ── Load registered aliases ───────────────────────────────────────────────
    for alias, abs_path in reg_snapshot.items():
        if alias in views_loaded:
            continue  # already loaded via direct reference
        # Only load if the alias actually appears in the (remaining) SQL
        if not re.search(rf'\b{re.escape(alias)}\b', working_sql, re.IGNORECASE):
            continue
        if not _is_remote(abs_path) and not os.path.exists(abs_path):
            raise FileNotFoundError(
                f"Registered file for alias {alias!r} not found: {abs_path}"
            )
        filter_dict = _extract_simple_filters(sql, alias)
        df = _load_file(abs_path, filter_dict=filter_dict, key=key, verify=verify)
        views_loaded[alias] = df

    # ── Register all DataFrames as DuckDB views ───────────────────────────────
    for view_name, df in views_loaded.items():
        con.register(view_name, df)

    # ── Execute ───────────────────────────────────────────────────────────────
    try:
        result = con.execute(working_sql).df()
    except Exception as e:
        raise RuntimeError(
            f"DuckDB query failed.\n"
            f"Rewritten SQL:\n{working_sql}\n"
            f"Views available: {list(views_loaded.keys())}\n"
            f"Original error: {e}"
        ) from e
    finally:
        con.close()

    return result


# ---------------------------------------------------------------------------
# Internal loader
# ---------------------------------------------------------------------------

def _load_file(
    path: str,
    filter_dict: Optional[dict] = None,
    key=None,
    verify: bool = True,
) -> pd.DataFrame:
    """Load a .permafrost/.pf file into a DataFrame, applying filter pushdown."""
    local = _resolve_path(path)
    chunks = list(peek(local, filter=filter_dict or None, verify=verify, key=key))
    if not chunks:
        info = audit(local)
        return pd.DataFrame(columns=info['columns'])
    return pd.concat(chunks, ignore_index=True)
