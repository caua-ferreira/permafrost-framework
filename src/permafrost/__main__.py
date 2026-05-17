"""
Entrypoint para execução via `python -m permafrost`.

Uso:
  python -m permafrost master [--host HOST] [--port PORT]
  python -m permafrost worker --master URL [--host HOST] [--port PORT] [--id ID]
  python -m permafrost freeze arquivo.csv
  python -m permafrost unfreeze arquivo.permafrost
"""
import sys

def main():
    if len(sys.argv) < 2:
        print("Uso: python -m permafrost <comando>")
        print("Comandos: master | worker | freeze | thaw | audit | catalog")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "master":
        import argparse, uvicorn
        from permafrost.cluster import PermafrostMaster
        p = argparse.ArgumentParser(description="Permafrost Master node")
        p.add_argument("--host", default="0.0.0.0")
        p.add_argument("--port", type=int, default=8700)
        p.add_argument("--max-retries", type=int, default=3)
        args = p.parse_args(sys.argv[2:])
        master = PermafrostMaster(host=args.host, port=args.port)
        master.MAX_RETRIES = args.max_retries
        print(f"❄  Permafrost Master iniciando em {args.host}:{args.port}")
        uvicorn.run(master.app, host=args.host, port=args.port, log_level="info")

    elif cmd == "worker":
        import argparse, uvicorn
        from permafrost.cluster import PermafrostWorker
        p = argparse.ArgumentParser(description="Permafrost Worker node")
        p.add_argument("--master", required=True, help="URL do master (ex: http://master:8700)")
        p.add_argument("--host", default="0.0.0.0")
        p.add_argument("--port", type=int, default=8801)
        p.add_argument("--id",   default=None, help="ID único do worker")
        args = p.parse_args(sys.argv[2:])
        worker = PermafrostWorker(
            master_url=args.master,
            host=args.host,
            port=args.port,
            worker_id=args.id,
        )
        print(f"❄  Permafrost Worker {worker.worker_id} → {args.master}")
        worker.run(auto_register=True)

    elif cmd in ("freeze", "unfreeze", "thaw", "audit", "verify", "catalog"):
        # Delegar para a CLI typer (thaw é alias depreciado de unfreeze)
        from permafrost.cli import app
        sys.argv = ["permafrost"] + sys.argv[1:]
        app()

    else:
        print(f"Comando desconhecido: {cmd}")
        print("Comandos disponíveis: master | worker | freeze | unfreeze | audit | catalog")
        sys.exit(1)


if __name__ == "__main__":
    main()
