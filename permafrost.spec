# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for permafrost standalone binary.

Build:
    pip install pyinstaller
    pyinstaller permafrost.spec

Output:
    dist/permafrost          (Linux/macOS)
    dist/permafrost.exe      (Windows)
"""
import sys
from PyInstaller.utils.hooks import collect_all, collect_submodules

# Collect all data/binaries from packages that ship native extensions or
# data files alongside their Python code.
datas_zstd, binaries_zstd, hiddenimports_zstd = collect_all('zstandard')
datas_duckdb, binaries_duckdb, hiddenimports_duckdb = collect_all('duckdb')
datas_pyarrow, binaries_pyarrow, hiddenimports_pyarrow = collect_all('pyarrow')
datas_cryptography, binaries_cryptography, hiddenimports_cryptography = collect_all('cryptography')

a = Analysis(
    ['src/permafrost/cli.py'],
    pathex=['src'],
    binaries=(
        binaries_zstd
        + binaries_duckdb
        + binaries_pyarrow
        + binaries_cryptography
    ),
    datas=(
        datas_zstd
        + datas_duckdb
        + datas_pyarrow
        + datas_cryptography
    ),
    hiddenimports=[
        # permafrost internals (lazy-imported inside functions)
        'permafrost',
        'permafrost.codec',
        'permafrost.schema_detector',
        'permafrost.chunk_mode',
        'permafrost.catalog',
        'permafrost.storage',
        'permafrost.cluster',
        'permafrost.rbac',
        'permafrost.crypto',
        'permafrost.schema_evolution',
        'permafrost.auto_codec',
        # zstandard C extension
        'zstandard',
        'zstandard._cffi',
        *hiddenimports_zstd,
        # duckdb
        'duckdb',
        *hiddenimports_duckdb,
        # pyarrow
        'pyarrow',
        'pyarrow.lib',
        *hiddenimports_pyarrow,
        # cryptography
        'cryptography',
        'cryptography.hazmat.primitives.ciphers.aead',
        *hiddenimports_cryptography,
        # numpy / pandas
        'numpy',
        'numpy.core',
        'pandas',
        'pandas.core',
        # fastapi / uvicorn / httpx (cluster commands)
        'fastapi',
        'uvicorn',
        'uvicorn.main',
        'uvicorn.logging',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'httpx',
        'anyio',
        'anyio._backends._asyncio',
        # typer / rich
        'typer',
        'rich',
        'rich.console',
        'rich.table',
        'rich.progress',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Not needed in the binary
        'pyspark',
        'pytest',
        'sphinx',
        'IPython',
        'jupyter',
        'notebook',
        'boto3',
        'botocore',
        'google',
        'azure',
        'tkinter',
        'matplotlib',
        'scipy',
        'sklearn',
        'sqlalchemy',
    ],
    noarchive=False,
    optimize=2,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='permafrost',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
