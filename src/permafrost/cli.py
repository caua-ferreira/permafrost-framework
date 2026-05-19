#!/usr/bin/env python3
"""
Permafrost CLI — interface de linha de comando
Uso:
  permafrost freeze dados.csv
  permafrost freeze dados.jsonl --codec=lzma2 --partition-by=ano
  permafrost thaw dados.permafrost
  permafrost audit dados.permafrost
  permafrost verify dados.permafrost
  permafrost catalog register ./dados/
  permafrost catalog search --name=vendas
  permafrost catalog cost --tier=glacier
"""
import sys, os


import typer
from typing import Optional
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
from rich.panel import Panel
from rich.text import Text
from rich import box
import time

app         = typer.Typer(help="❄  Permafrost — compressão extrema para arquivamento de longo prazo", add_completion=False)
cat_app     = typer.Typer(help="Gerenciar o PermafrostCatalog")
cluster_app = typer.Typer(help="Gerenciar usuários e RBAC do cluster")
app.add_typer(cat_app, name="catalog")
app.add_typer(cluster_app, name="cluster")

console = Console(highlight=False)

CODECS = {"lzma2": 0x02, "zstd": 0x01}
QUANTS = {"none": 0x00, "high": 0x01, "medium": 0x02, "low": 0x03}
TIER_PRICES = {"s3": 0.023, "s3-ia": 0.0125, "glacier": 0.004, "glacier-deep": 0.00099}

def _load():
    from permafrost.codec import freeze as pf_freeze, unfreeze as pf_thaw, audit as pf_audit
    from permafrost.codec import CODEC_LZMA2, CODEC_ZSTD, QUANT_NONE, QUANT_MEDIUM
    from permafrost.schema_detector import SchemaDetector
    return pf_freeze, pf_thaw, pf_audit, SchemaDetector

def _header():
    from permafrost import __version__
    try:
        console.print(Panel.fit(
            f"[bold cyan]*  Permafrost Data Framework[/] [dim]v{__version__}[/]",
            border_style="cyan", padding=(0,2)
        ))
    except UnicodeEncodeError:
        console.print(f"[bold cyan]* Permafrost Data Framework[/] [dim]v{__version__}[/]")

# ── FREEZE ────────────────────────────────────────────────────────────────────
@app.command()
def freeze(
    input: str = typer.Argument(..., help="Arquivo de entrada (.csv .jsonl .json .parquet .xlsx)"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Caminho de saída (.permafrost)"),
    codec: str = typer.Option("lzma2", "--codec", "-c", help="Codec: lzma2 | zstd"),
    quant: str = typer.Option("none",  "--quant", "-q", help="Quantização: none | high | medium | low"),
    partition_by: Optional[str] = typer.Option(None, "--partition-by", "-p", help="Coluna para particionar"),
    chunk_rows: int = typer.Option(10_000, "--chunk-rows", help="Linhas por chunk"),
    comment: str = typer.Option("", "--comment", help="Comentário livre embutido no arquivo"),
):
    """Comprime um arquivo de dados para o formato .permafrost"""
    _header()
    pf_freeze, _, _, SchemaDetector = _load()

    # Validar codec e quant
    if codec not in CODECS:
        console.print(f"[red]✗ Codec inválido: {codec}. Use: lzma2 | zstd[/]"); raise typer.Exit(1)
    if quant not in QUANTS:
        console.print(f"[red]✗ Quant inválido: {quant}. Use: none | high | medium | low[/]"); raise typer.Exit(1)

    if not os.path.exists(input):
        console.print(f"[red]✗ Arquivo não encontrado: {input}[/]"); raise typer.Exit(1)

    output = output or os.path.splitext(input)[0] + ".permafrost"
    ext = os.path.splitext(input)[1].lower()

    console.print(f"\n[bold]Entrada:[/]  [cyan]{input}[/]  ({os.path.getsize(input)/1e6:.2f} MB)")
    console.print(f"[bold]Saída:[/]    [cyan]{output}[/]")
    console.print(f"[bold]Codec:[/]    [yellow]{codec}[/]  [bold]Quant:[/] [yellow]{quant}[/]")
    if partition_by:
        console.print(f"[bold]Partição:[/] [yellow]{partition_by}[/]")
    console.print()

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
                  BarColumn(), TimeElapsedColumn(), console=console) as prog:

        task1 = prog.add_task("Detectando schema...", total=None)
        det = SchemaDetector()
        df, dtype, manifest = det.detect(input)
        prog.update(task1, description=f"[green]Schema: {dtype.value} — {len(df):,} linhas × {len(df.columns)} colunas[/]", completed=True)

        # Se partition_by mas dado não ordenado → ordenar
        if partition_by and partition_by in df.columns:
            task_sort = prog.add_task("Ordenando por partição...", total=None)
            df = df.sort_values(partition_by).reset_index(drop=True)
            prog.update(task_sort, description=f"[green]Ordenado por '{partition_by}'[/]", completed=True)

        task2 = prog.add_task("Comprimindo...", total=None)
        t0 = time.time()
        metrics = pf_freeze(
            df, output,
            codec=CODECS[codec], quant=QUANTS[quant],
            chunk_rows=chunk_rows, partition_by=partition_by, comment=comment
        )
        elapsed = time.time()-t0
        prog.update(task2, description=f"[green]Compressão concluída[/]", completed=True)

    # Resultado
    console.print()
    tbl = Table(box=box.SIMPLE, show_header=False, padding=(0,1))
    tbl.add_column(style="dim"); tbl.add_column(style="bold")
    tbl.add_row("Linhas",          f"{metrics['rows']:,}")
    tbl.add_row("Colunas",         str(metrics['cols']))
    tbl.add_row("Tamanho original",f"{metrics['original_mb']:.3f} MB")
    tbl.add_row("Tamanho final",   f"[cyan]{metrics['stored_mb']:.3f} MB[/]")
    tbl.add_row("Ratio",           f"[green bold]{metrics['ratio']:.2f}×[/]")
    tbl.add_row("Redução",         f"[green bold]{metrics['reduction_pct']:.1f}%[/]")
    tbl.add_row("Chunks",          f"{metrics['n_chunks']} × {chunk_rows:,} linhas")
    tbl.add_row("Tempo",           f"{elapsed:.2f}s")

    console.print(Panel(tbl, title="[bold cyan]✓ Freeze concluído[/]", border_style="cyan"))
    console.print(f"[dim]→ {output}[/]\n")


# ── UNFREEZE ──────────────────────────────────────────────────────────────────
@app.command()
def unfreeze(
    input: str = typer.Argument(..., help="Arquivo .permafrost"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Arquivo de saída (.csv .parquet)"),
    filter_col: Optional[str] = typer.Option(None, "--filter-col", help="Coluna de filtro (ex: ano)"),
    filter_val: Optional[str] = typer.Option(None, "--filter-val", help="Valor do filtro (ex: 2023)"),
    no_verify: bool = typer.Option(False, "--no-verify", help="Pular verificação SHA-256"),
):
    """Descomprime um arquivo .permafrost"""
    _header()
    _, pf_thaw, pf_audit, _ = _load()

    if not os.path.exists(input):
        console.print(f"[red]✗ Arquivo não encontrado: {input}[/]"); raise typer.Exit(1)

    info = pf_audit(input)
    output = output or os.path.splitext(input)[0] + "_unfrozen.csv"
    ext_out = os.path.splitext(output)[1].lower()

    console.print(f"\n[bold]Arquivo:[/]  [cyan]{input}[/]  ({info['file_size_mb']:.3f} MB)")
    console.print(f"[bold]Saída:[/]    [cyan]{output}[/]")
    console.print(f"[bold]Linhas:[/]   {info['orig_rows']:,}  [bold]Codec:[/] {info['codec']}  [bold]Chunks:[/] {info['n_chunks']}")

    filter_dict = {filter_col: filter_val} if filter_col and filter_val else None
    if filter_dict:
        console.print(f"[bold]Filtro:[/]   {filter_col}={filter_val}")
    console.print()

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
                  TimeElapsedColumn(), console=console) as prog:
        task = prog.add_task("Descomprimindo...", total=None)
        t0 = time.time()
        df = pf_thaw(input, verify=not no_verify, filter=filter_dict)
        elapsed = time.time()-t0
        prog.update(task, description=f"[green]{len(df):,} linhas recuperadas em {elapsed:.3f}s[/]", completed=True)

    # Salvar
    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
                  TimeElapsedColumn(), console=console) as prog:
        task2 = prog.add_task(f"Salvando {ext_out}...", total=None)
        if ext_out == '.parquet':
            df.to_parquet(output, index=False)
        else:
            df.to_csv(output, index=False)
        out_mb = os.path.getsize(output)/1e6
        prog.update(task2, description=f"[green]Salvo: {output} ({out_mb:.2f} MB)[/]", completed=True)

    tbl = Table(box=box.SIMPLE, show_header=False, padding=(0,1))
    tbl.add_column(style="dim"); tbl.add_column(style="bold")
    tbl.add_row("Linhas recuperadas", f"[cyan]{len(df):,}[/]")
    tbl.add_row("Colunas",            str(len(df.columns)))
    tbl.add_row("Saída",              f"{out_mb:.2f} MB")
    tbl.add_row("Tempo",              f"{elapsed:.3f}s")
    tbl.add_row("Verificação SHA-256",f"[green]✓[/]" if not no_verify else "[yellow]pulada[/]")
    console.print(Panel(tbl, title="[bold green]✓ Unfreeze concluído[/]", border_style="green"))
    console.print()


@app.command(hidden=True, deprecated=True)
def thaw(
    input: str = typer.Argument(..., help="Arquivo .permafrost"),
    output: Optional[str] = typer.Option(None, "--output", "-o"),
    filter_col: Optional[str] = typer.Option(None, "--filter-col"),
    filter_val: Optional[str] = typer.Option(None, "--filter-val"),
    no_verify: bool = typer.Option(False, "--no-verify"),
):
    """Deprecated: use 'unfreeze' instead."""
    console.print("[yellow]⚠ 'thaw' is deprecated. Use 'unfreeze' instead.[/]")
    unfreeze(input=input, output=output, filter_col=filter_col,
             filter_val=filter_val, no_verify=no_verify)


# ── AUDIT ─────────────────────────────────────────────────────────────────────
@app.command()
def audit(
    input: str = typer.Argument(..., help="Arquivo .permafrost"),
    show_chunks: bool = typer.Option(False, "--chunks", help="Mostrar detalhes de cada chunk"),
):
    """Inspeciona um arquivo .permafrost sem descomprimir"""
    _header()
    _, _, pf_audit, _ = _load()

    if not os.path.exists(input):
        console.print(f"[red]✗ Arquivo não encontrado: {input}[/]"); raise typer.Exit(1)

    info = pf_audit(input)

    console.print(f"\n[bold cyan]❄ {os.path.basename(input)}[/]\n")
    tbl = Table(box=box.SIMPLE, show_header=False, padding=(0,1))
    tbl.add_column(style="dim", width=22); tbl.add_column(style="bold")
    quant_labels = {0:"lossless",1:"high",2:"medium",3:"low"}
    tbl.add_row("Versão",        info['version'])
    tbl.add_row("Codec",         f"[yellow]{info['codec']}[/]")
    tbl.add_row("Quantização",   f"[yellow]{quant_labels.get(info['quant'],'?')}[/]")
    tbl.add_row("Freeze em",     info['freeze_date'])
    tbl.add_row("Linhas",        f"[cyan]{info['orig_rows']:,}[/]")
    tbl.add_row("Chunks",        f"{info['n_chunks']} × {info['chunk_rows']:,} linhas")
    tbl.add_row("Tamanho",       f"[cyan]{info['file_size_mb']:.3f} MB[/]")
    tbl.add_row("Partição",      f"[yellow]{info['partition_col']}[/]" if info['partition_col'] != '__rows__' else "[dim]nenhuma[/]")
    tbl.add_row("Comentário",    info.get('comment','') or "[dim]—[/]")
    tbl.add_row("Colunas",       f"[dim]{', '.join(info['columns'][:6])}{'...' if len(info['columns'])>6 else ''}[/]")

    console.print(Panel(tbl, title="[bold]Metadados[/]", border_style="cyan"))

    if show_chunks:
        console.print()
        ctbl = Table("Chunk","Rows","Part Key","Bytes","SHA-256",box=box.SIMPLE,show_lines=True)
        for e in info['index_entries']:
            ctbl.add_row(
                str(e['chunk_id']),
                f"{e['row_start']:,}–{e['row_end']:,}",
                f"[yellow]{e['part_key']}[/]",
                f"{e['byte_len']/1e3:.1f} KB",
                f"[dim]{e['sha256'][:16]}...[/]"
            )
        console.print(ctbl)
    console.print()


# ── VERIFY ────────────────────────────────────────────────────────────────────
@app.command()
def verify(
    input: str = typer.Argument(..., help="Arquivo .permafrost"),
):
    """Verifica integridade SHA-256 sem descomprimir"""
    _header()
    _, pf_thaw, pf_audit, _ = _load()
    import hashlib

    if not os.path.exists(input):
        console.print(f"[red]✗ Arquivo não encontrado: {input}[/]"); raise typer.Exit(1)

    console.print(f"\n[bold]Verificando:[/] [cyan]{input}[/]\n")
    with open(input,'rb') as f: raw = f.read()

    from permafrost.codec import MAGIC, EOF_MAGIC, _sha256, _read_header, _read_sparse_index

    checks = []
    # 1. Magic
    magic_ok = raw[:4] == MAGIC
    checks.append(("Magic bytes (PRMS)", magic_ok))

    # 2. EOF magic
    eof_ok = raw[-4:] == EOF_MAGIC
    checks.append(("EOF magic (SMRP)", eof_ok))

    # 3. Header SHA-256
    try:
        h = _read_header(raw)
        hdr_ok = _sha256(raw[:h['hdr_end']]) == h['hdr_sha_stored']
        checks.append(("Header SHA-256", hdr_ok))
    except Exception as e:
        checks.append(("Header SHA-256", False))
        h = None

    # 4. Index SHA-256
    try:
        idx = _read_sparse_index(raw)
        checks.append(("Sparse index SHA-256", True))
    except:
        checks.append(("Sparse index SHA-256", False))
        idx = []

    # 5. Chunk SHA-256s
    chunk_results = []
    if h and idx:
        for entry in idx:
            offset = entry['byte_offset']; blk_len = entry['byte_len']
            blob = raw[offset:offset+blk_len]
            ok = _sha256(blob).hex() == entry['sha256']
            chunk_results.append((entry['chunk_id'], ok))
        chunks_ok = all(ok for _,ok in chunk_results)
        checks.append((f"Chunks SHA-256 ({len(chunk_results)} chunks)", chunks_ok))

    # Exibir
    for label, ok in checks:
        icon = "[green]✓[/]" if ok else "[red]✗[/]"
        console.print(f"  {icon}  {label}")

    all_ok = all(ok for _,ok in checks)
    console.print()
    if all_ok:
        console.print(Panel("[bold green]✓ Arquivo íntegro — todos os SHA-256 verificados[/]", border_style="green"))
    else:
        console.print(Panel("[bold red]✗ Falha de integridade detectada[/]", border_style="red"))
        console.print()
        raise typer.Exit(1)
    console.print()


# ── CATALOG REGISTER ──────────────────────────────────────────────────────────
@cat_app.command("register")
def catalog_register(
    path: str = typer.Argument(..., help="Arquivo .permafrost ou diretório"),
    catalog_db: str = typer.Option(".permafrost_catalog.db", "--db", help="Caminho do catalog DuckDB"),
    tags: Optional[str] = typer.Option(None, "--tags", help="Tags separadas por vírgula"),
    recursive: bool = typer.Option(False, "--recursive", "-r", help="Buscar recursivamente"),
):
    """Registra arquivos .permafrost no catalog"""
    _header()
    from permafrost.catalog import PermafrostCatalog
    tag_list = [t.strip() for t in tags.split(",")] if tags else []
    cat = PermafrostCatalog(catalog_db)
    console.print()
    if os.path.isdir(path):
        results = cat.register_dir(path, tags=tag_list, recursive=recursive)
        registered = [r for r in results if r['status']=='registered']
        console.print(f"\n[green]✓ {len(registered)} arquivo(s) registrado(s)[/] no catalog [cyan]{catalog_db}[/]\n")
    else:
        r = cat.register(path, tags=tag_list)
        if r['status'] == 'registered':
            console.print(f"\n[green]✓ Registrado:[/] {r['name']} — {r.get('rows',0):,} linhas | {r.get('file_mb',0):.3f} MB\n")
        else:
            console.print(f"\n[yellow]~ Já registrado:[/] {path}\n")


# ── CATALOG SEARCH ────────────────────────────────────────────────────────────
@cat_app.command("search")
def catalog_search(
    name: Optional[str] = typer.Option(None, "--name", "-n"),
    codec: Optional[str] = typer.Option(None, "--codec"),
    partition_key: Optional[str] = typer.Option(None, "--partition-key", "-k"),
    lossless_only: bool = typer.Option(False, "--lossless"),
    catalog_db: str = typer.Option(".permafrost_catalog.db","--db"),
):
    """Busca datasets no catalog"""
    _header()
    from permafrost.catalog import PermafrostCatalog
    cat = PermafrostCatalog(catalog_db)
    df = cat.search(name=name, codec=codec, partition_key=partition_key, lossless_only=lossless_only)
    console.print()
    if df.empty:
        console.print("[yellow]Nenhum dataset encontrado.[/]\n"); return

    tbl = Table("Nome","Codec","Quant","Linhas","MB","Chunks","Partição","Freeze",
                box=box.SIMPLE, show_lines=False)
    for _, row in df.iterrows():
        quant_labels = {0:"lossless",1:"high",2:"medium",3:"low"}
        tbl.add_row(
            f"[cyan]{row['name']}[/]",
            f"[yellow]{row['codec']}[/]",
            quant_labels.get(int(row['quant']),'?'),
            f"{int(row['rows']):,}",
            f"{row['mb']:.3f}",
            str(int(row['n_chunks'])),
            row['partition_col'] or "—",
            str(row['freeze_date'])[:10],
        )
    console.print(tbl)
    console.print(f"[dim]{len(df)} resultado(s)[/]\n")


# ── CATALOG COST ──────────────────────────────────────────────────────────────
@cat_app.command("cost")
def catalog_cost(
    tier: str = typer.Option("glacier-deep","--tier", help="s3 | s3-ia | glacier | glacier-deep"),
    catalog_db: str = typer.Option(".permafrost_catalog.db","--db"),
):
    """Relatório de custo estimado por tier de storage"""
    _header()
    from permafrost.catalog import PermafrostCatalog
    cat = PermafrostCatalog(catalog_db)
    tier_key = tier.replace("-","_")
    cr = cat.cost_report(tier_key)
    console.print()

    tbl = Table("Dataset","MB","$/mês","$/ano","3 anos", box=box.SIMPLE)
    for _, row in cr.iterrows():
        tbl.add_row(
            f"[cyan]{row['name']}[/]",
            f"{row['size_mb']:.3f}",
            f"${row['cost_monthly_usd']:.6f}",
            f"${row['cost_annual_usd']:.5f}",
            f"[green]${row['cost_3yr_usd']:.5f}[/]",
        )

    console.print(tbl)
    total_m = cr['cost_monthly_usd'].sum()
    total_3 = cr['cost_3yr_usd'].sum()
    console.print(f"\n[bold]Total:[/] [cyan]${total_m:.6f}/mês[/]  [green]${total_3:.5f} em 3 anos[/]")
    console.print(f"[dim]Tier: {tier} (${TIER_PRICES.get(tier, 0.00099)}/GB/mês)[/]\n")


# ── CATALOG INTEGRITY ─────────────────────────────────────────────────────────
@cat_app.command("verify")
def catalog_verify(
    catalog_db: str = typer.Option(".permafrost_catalog.db","--db"),
    name: Optional[str] = typer.Option(None, "--name", "-n"),
):
    """Verifica integridade de todos os arquivos no catalog"""
    _header()
    from permafrost.catalog import PermafrostCatalog
    cat = PermafrostCatalog(catalog_db)
    console.print("\n[bold]Verificando integridade...[/]\n")
    ic = cat.integrity_check(name_filter=name)
    for _, row in ic.iterrows():
        icon = "[green]✓[/]" if row['status']=='OK' else "[red]✗[/]"
        console.print(f"  {icon}  [cyan]{row['name']}[/]  chunks: {row['chunks_ok']} OK / {row['chunks_fail']} falha")
    all_ok = (ic['status']=='OK').all()
    console.print()
    if all_ok:
        console.print(Panel(f"[bold green]✓ Todos os {len(ic)} dataset(s) íntegros[/]", border_style="green"))
    else:
        console.print(Panel("[bold red]✗ Falhas detectadas[/]", border_style="red"))
    console.print()


@cat_app.command("serve")
def catalog_serve(
    catalog_db: str = typer.Option(".permafrost_catalog.db", "--db", "-d",
                                   help="Caminho do arquivo DuckDB do catálogo"),
    host: str       = typer.Option("127.0.0.1", "--host",
                                   help="Endereço de bind (use 0.0.0.0 para rede)"),
    port: int       = typer.Option(8800, "--port", "-p",
                                   help="Porta TCP do servidor"),
    log_level: str  = typer.Option("info", "--log-level",
                                   help="Nível de log: debug | info | warning | error"),
):
    """Sobe um servidor REST (FastAPI/uvicorn) para o PermafrostCatalog.

    Expõe todas as operações do catálogo via HTTP/JSON.
    Documentação interativa disponível em http://<host>:<port>/docs após iniciar.

    Exemplos:

        permafrost catalog serve

        permafrost catalog serve --db /data/prod.db --host 0.0.0.0 --port 8800
    """
    _header()
    try:
        import uvicorn
    except ImportError:
        console.print("[red]uvicorn não encontrado. Instale com: pip install uvicorn[/]")
        raise typer.Exit(1)

    from permafrost.catalog_server import PermafrostCatalogServer
    srv = PermafrostCatalogServer(catalog_db)

    console.print(f"\n[bold cyan]Permafrost Catalog Server[/]")
    console.print(f"  Catálogo : [yellow]{catalog_db}[/]")
    console.print(f"  Endereço : [yellow]http://{host}:{port}[/]")
    console.print(f"  Docs     : [yellow]http://{host}:{port}/docs[/]")
    console.print(f"  Health   : [yellow]http://{host}:{port}/health[/]")
    console.print("\n[dim]Pressione Ctrl+C para parar.[/]\n")

    uvicorn.run(srv.app, host=host, port=port, log_level=log_level)


# ── CLUSTER RBAC ──────────────────────────────────────────────────────────────
@cluster_app.command("add-user")
def cluster_add_user(
    username:   str           = typer.Argument(...,            help="Nome do usuário"),
    can_freeze: bool          = typer.Option(False, "--can-freeze",  help="Permite freeze"),
    can_thaw:   bool          = typer.Option(False, "--can-thaw",    help="Permite thaw"),
    namespace:  str           = typer.Option("default", "--namespace", "-n", help="Namespace"),
    expires_in: int           = typer.Option(0, "--expires-in",     help="Segundos até expirar (0=nunca)"),
    master_url: str           = typer.Option("http://localhost:8700", "--master-url", "-m"),
    admin_key:  str           = typer.Option(..., "--admin-key", "-k", help="Chave-mestra do cluster", envvar="PERMAFROST_ADMIN_KEY"),
):
    """Cria um usuário no cluster e exibe seu token JWT."""
    _header()
    from permafrost.cluster import PermafrostClient
    client = PermafrostClient(master_url)
    try:
        token = client.add_user(username, can_freeze=can_freeze, can_thaw=can_thaw,
                                namespace=namespace, expires_in=expires_in, admin_key=admin_key)
        perms = []
        if can_freeze: perms.append("freeze")
        if can_thaw:   perms.append("thaw")
        console.print(f"\n[bold green]✓ Usuário criado:[/] [cyan]{username}[/]")
        console.print(f"  Namespace : [yellow]{namespace}[/]")
        console.print(f"  Permissões: [yellow]{', '.join(perms) or 'nenhuma'}[/]")
        console.print(f"  Token     : [dim]{token}[/]\n")
    except Exception as e:
        console.print(f"[red]Erro ao criar usuário: {e}[/]")
        raise typer.Exit(1)


@cluster_app.command("list-users")
def cluster_list_users(
    master_url: str = typer.Option("http://localhost:8700", "--master-url", "-m"),
    admin_key:  str = typer.Option(..., "--admin-key", "-k", envvar="PERMAFROST_ADMIN_KEY"),
):
    """Lista usuários registrados no cluster."""
    _header()
    from permafrost.cluster import PermafrostClient
    client = PermafrostClient(master_url)
    try:
        users = client.list_users(admin_key=admin_key)
        if not users:
            console.print("[dim]Nenhum usuário registrado.[/]")
            return
        t = Table(show_header=True, box=box.SIMPLE)
        t.add_column("Usuário",    style="cyan")
        t.add_column("can_freeze", justify="center")
        t.add_column("can_thaw",   justify="center")
        t.add_column("Namespace",  style="yellow")
        for u in users:
            t.add_row(u["username"],
                      "[green]✓[/]" if u["can_freeze"] else "[red]✗[/]",
                      "[green]✓[/]" if u["can_thaw"]   else "[red]✗[/]",
                      u["namespace"])
        console.print(t)
    except Exception as e:
        console.print(f"[red]Erro: {e}[/]")
        raise typer.Exit(1)


@cluster_app.command("remove-user")
def cluster_remove_user(
    username:   str = typer.Argument(...),
    master_url: str = typer.Option("http://localhost:8700", "--master-url", "-m"),
    admin_key:  str = typer.Option(..., "--admin-key", "-k", envvar="PERMAFROST_ADMIN_KEY"),
):
    """Remove um usuário do cluster."""
    _header()
    from permafrost.cluster import PermafrostClient
    client = PermafrostClient(master_url)
    try:
        result = client.remove_user(username, admin_key=admin_key)
        if result.get("existed"):
            console.print(f"[bold green]✓ Usuário removido:[/] [cyan]{username}[/]")
        else:
            console.print(f"[yellow]Usuário não encontrado:[/] {username}")
    except Exception as e:
        console.print(f"[red]Erro: {e}[/]")
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
