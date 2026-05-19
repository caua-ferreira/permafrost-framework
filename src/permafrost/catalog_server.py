"""
PermafrostCatalogServer — REST API sobre o PermafrostCatalog.

Expõe todas as operações do catálogo via HTTP/JSON, permitindo que dashboards
e ferramentas externas se integrem sem instalar o SDK Python.

Iniciar via CLI:
    permafrost catalog serve --db catalog.db --host 0.0.0.0 --port 8800

Ou programaticamente:
    import uvicorn
    from permafrost.catalog_server import PermafrostCatalogServer
    srv = PermafrostCatalogServer("catalog.db")
    uvicorn.run(srv.app, host="0.0.0.0", port=8800)

Endpoints
---------
GET  /health
POST /datasets/register
POST /datasets/register_dir
GET  /datasets
GET  /datasets/{name}
GET  /datasets/{name}/versions
GET  /datasets/{name}/chunks
GET  /datasets/{name}/integrity
DELETE /datasets/{name}
GET  /search
GET  /stats
GET  /cost_report
POST /sql
"""

from __future__ import annotations

import math
import os
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from permafrost.catalog import PermafrostCatalog, STORAGE_PRICES
from permafrost.catalog_backends import CatalogBackend


# ── helpers ───────────────────────────────────────────────────────────────────

def _df_to_records(df) -> List[Dict[str, Any]]:
    """Converte DataFrame para lista de dicts, substituindo NaN por None."""
    records = df.to_dict(orient="records")
    for row in records:
        for k, v in row.items():
            if isinstance(v, float) and math.isnan(v):
                row[k] = None
    return records


# ── request/response models ───────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    path: str
    name: Optional[str] = None
    version: Optional[str] = None
    tags: Optional[List[str]] = None


class RegisterDirRequest(BaseModel):
    directory: str
    tags: Optional[List[str]] = None
    recursive: bool = False


class SqlRequest(BaseModel):
    query: str


class UnregisterRequest(BaseModel):
    name: str


# ── server ────────────────────────────────────────────────────────────────────

class PermafrostCatalogServer:
    """REST API para o PermafrostCatalog.

    Expõe todas as operações do catálogo como endpoints HTTP/JSON via FastAPI,
    permitindo integração com dashboards, scripts e ferramentas externas sem
    instalar o SDK Python no lado do cliente.

    Args:
        catalog_path: Caminho do arquivo DuckDB (ou ``":memory:"`` para testes).
        backend: Backend de storage opcional para resolução de URIs remotas.
        title: Título exibido no Swagger UI (``/docs``).
    """

    def __init__(
        self,
        catalog_path: str = ".permafrost_catalog.db",
        backend: Optional[CatalogBackend] = None,
        title: str = "Permafrost Catalog API",
    ) -> None:
        self.catalog = PermafrostCatalog(catalog_path, backend=backend)
        self.app = FastAPI(
            title=title,
            version="1.3.0",
            description=(
                "REST API for PermafrostCatalog — register, search, "
                "inspect and manage .permafrost datasets."
            ),
        )
        self._register_routes()

    # ── route registration ────────────────────────────────────────────────────

    def _register_routes(self) -> None:
        app = self.app
        cat = self.catalog

        # ── health ────────────────────────────────────────────────────────────
        @app.get("/health", tags=["system"])
        def health() -> dict:
            """Retorna status do servidor e número de datasets registrados."""
            s = cat.stats()
            return {
                "status": "ok",
                "catalog_path": cat.catalog_path,
                "total_datasets": s.get("total_datasets") or 0,
                "total_rows": int(s.get("total_rows") or 0),
                "total_mb": round(float(s.get("total_mb") or 0), 3),
            }

        # ── register ──────────────────────────────────────────────────────────
        @app.post("/datasets/register", tags=["datasets"])
        def register(req: RegisterRequest) -> dict:
            """Registra um arquivo .permafrost pelo caminho (local ou URI remota)."""
            try:
                return cat.register(
                    req.path,
                    name=req.name,
                    version=req.version,
                    tags=req.tags,
                )
            except FileNotFoundError as exc:
                raise HTTPException(status_code=404, detail=str(exc))
            except Exception as exc:
                raise HTTPException(status_code=500, detail=str(exc))

        @app.post("/datasets/register_dir", tags=["datasets"])
        def register_dir(req: RegisterDirRequest) -> List[dict]:
            """Registra todos os .permafrost de um diretório."""
            if not os.path.isdir(req.directory):
                raise HTTPException(
                    status_code=404,
                    detail=f"Diretório não encontrado: {req.directory}",
                )
            return cat.register_dir(
                req.directory,
                tags=req.tags,
                recursive=req.recursive,
            )

        # ── list / get ────────────────────────────────────────────────────────
        @app.get("/datasets", tags=["datasets"])
        def list_datasets(
            name: Optional[str] = Query(None),
            codec: Optional[str] = Query(None),
            tags_contain: Optional[str] = Query(None),
            lossless_only: bool = Query(False),
            min_rows: Optional[int] = Query(None),
            max_mb: Optional[float] = Query(None),
            partition_col: Optional[str] = Query(None),
        ) -> List[dict]:
            """Lista datasets com filtros opcionais."""
            df = cat.search(
                name=name,
                codec=codec,
                tags_contain=tags_contain,
                lossless_only=lossless_only,
                min_rows=min_rows,
                max_mb=max_mb,
                partition_col=partition_col,
            )
            return _df_to_records(df)

        @app.get("/datasets/{name}", tags=["datasets"])
        def get_dataset(name: str) -> dict:
            """Retorna metadados de um dataset pelo nome exato."""
            df = cat.search(name=name)
            if df.empty:
                raise HTTPException(
                    status_code=404, detail=f"Dataset '{name}' não encontrado."
                )
            return _df_to_records(df)[0]

        # ── versions ──────────────────────────────────────────────────────────
        @app.get("/datasets/{name}/versions", tags=["datasets"])
        def get_versions(name: str) -> List[dict]:
            """Lista todas as versões registradas de um dataset."""
            df = cat.versions(name)
            if df.empty:
                raise HTTPException(
                    status_code=404, detail=f"Dataset '{name}' não encontrado."
                )
            return _df_to_records(df)

        # ── chunks ────────────────────────────────────────────────────────────
        @app.get("/datasets/{name}/chunks", tags=["datasets"])
        def get_chunks(
            name: str,
            part_key: Optional[str] = Query(None),
        ) -> List[dict]:
            """Retorna o sparse index (chunks) de um dataset."""
            df = cat.search_chunks(name, part_key=part_key)
            return _df_to_records(df)

        # ── integrity ─────────────────────────────────────────────────────────
        @app.get("/datasets/{name}/integrity", tags=["datasets"])
        def integrity(name: str) -> List[dict]:
            """Verifica SHA-256 de todos os chunks do dataset."""
            df = cat.integrity_check(name_filter=name)
            if df.empty:
                raise HTTPException(
                    status_code=404, detail=f"Dataset '{name}' não encontrado."
                )
            return _df_to_records(df)

        # ── delete (unregister) ───────────────────────────────────────────────
        @app.delete("/datasets/{name}", tags=["datasets"])
        def delete_dataset(name: str) -> dict:
            """Remove um dataset do catálogo (não apaga o arquivo físico)."""
            with cat._lock:
                rows = cat.con.execute(
                    "SELECT id, name FROM datasets WHERE name = ?", [name]
                ).fetchall()
                if not rows:
                    raise HTTPException(
                        status_code=404, detail=f"Dataset '{name}' não encontrado."
                    )
                ids = [r[0] for r in rows]
                for ds_id in ids:
                    cat.con.execute("DELETE FROM chunks WHERE dataset_id = ?", [ds_id])
                cat.con.execute(
                    f"DELETE FROM datasets WHERE id IN ({','.join('?' * len(ids))})",
                    ids,
                )
            return {"status": "deleted", "name": name, "removed": len(ids)}

        # ── stats ─────────────────────────────────────────────────────────────
        @app.get("/stats", tags=["catalog"])
        def stats() -> dict:
            """Métricas agregadas do catálogo (total de datasets, linhas, MB)."""
            raw = cat.stats()
            return {
                k: (None if (v is None or (isinstance(v, float) and math.isnan(v)))
                    else v)
                for k, v in raw.items()
            }

        # ── cost report ───────────────────────────────────────────────────────
        @app.get("/cost_report", tags=["catalog"])
        def cost_report(
            tier: str = Query(
                "glacier_deep",
                description=f"Tier de storage: {', '.join(STORAGE_PRICES.keys())}",
            )
        ) -> List[dict]:
            """Estimativa de custo mensal/anual por dataset no tier solicitado."""
            if tier not in STORAGE_PRICES:
                raise HTTPException(
                    status_code=400,
                    detail=f"Tier inválido. Use: {', '.join(STORAGE_PRICES.keys())}",
                )
            return _df_to_records(cat.cost_report(tier=tier))

        # ── sql ───────────────────────────────────────────────────────────────
        @app.post("/sql", tags=["catalog"])
        def execute_sql(req: SqlRequest) -> List[dict]:
            """Executa SQL direto no DuckDB do catálogo (somente tabelas internas)."""
            try:
                return _df_to_records(cat.sql(req.query))
            except Exception as exc:
                raise HTTPException(status_code=400, detail=str(exc))

        # ── search alias (conveniente para GETs com query params) ─────────────
        @app.get("/search", tags=["catalog"])
        def search(
            name: Optional[str] = Query(None),
            codec: Optional[str] = Query(None),
            tags_contain: Optional[str] = Query(None),
            lossless_only: bool = Query(False),
            min_rows: Optional[int] = Query(None),
            max_mb: Optional[float] = Query(None),
        ) -> List[dict]:
            """Atalho de busca — equivalente a GET /datasets com filtros."""
            df = cat.search(
                name=name,
                codec=codec,
                tags_contain=tags_contain,
                lossless_only=lossless_only,
                min_rows=min_rows,
                max_mb=max_mb,
            )
            return _df_to_records(df)
