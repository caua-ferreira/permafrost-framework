"""
permafrost.ice — .ice file format: parser, validator, watcher

.ice files are YAML-formatted declarative freeze recipes discovered
automatically by the cluster master from a watched directory or S3 bucket.

Usage (cluster master):
    master = PermafrostMaster(watch_path="s3://bucket/ice/", poll_interval=30)

Usage (CLI):
    permafrost freeze --recipe dataset.ice

File format (YAML, extension .ice):
    name: climate-daily
    source: s3://raw-data/climate/
    output: s3://frozen/climate.permafrost
    codec: zstd
    quant: 0
    chunk_rows: 250000
    schedule: "0 2 * * *"
    partition_by: date
    owner: data-eng@acme.com
    tags: [climate, daily]
"""

from __future__ import annotations

import os
import hashlib
import threading
import time
import logging
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any, Callable

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

VALID_CODECS = {"zstd", "lz4", "snappy", "fp16", "bf16", "int8", "vault", "lzma2"}
VALID_PRIORITIES = {"low", "normal", "high"}
DEFAULT_CHUNK_ROWS = 100_000
DEFAULT_RETRY = 2
DEFAULT_PRIORITY = "normal"
ICE_VERSION = "1"


# ── Schema dataclass ──────────────────────────────────────────────────────────

@dataclass
class IceRecipe:
    """Parsed and validated .ice recipe."""

    # Required
    name:   str
    source: str
    output: str
    codec:  str

    # Compression tuning
    quant:        int  = 0
    chunk_rows:   int  = DEFAULT_CHUNK_ROWS
    partition_by: Optional[str] = None

    # Scheduling
    schedule: Optional[str] = None
    enabled:  bool = True
    timezone: Optional[str] = None

    # Execution control
    workers:         Optional[int] = None
    priority:        str = DEFAULT_PRIORITY
    retry:           int = DEFAULT_RETRY
    timeout_minutes: int = 0

    # Metadata
    description: Optional[str] = None
    owner:       Optional[str] = None
    tags:        List[str] = field(default_factory=list)
    version:     str = ICE_VERSION

    # Internal (set by watcher, not from file)
    source_file:  Optional[str] = None   # path to the .ice file that produced this
    source_etag:  Optional[str] = None   # ETag / mtime hash for change detection
    discovered_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["source_type"] = "watcher" if self.source_file else "api"
        return d

    def to_job_payload(self) -> Dict[str, Any]:
        """Returns the dict expected by POST /jobs."""
        return {
            "source_path":  self.source,
            "output_path":  self.output,
            "codec":        self.codec,
            "quant":        self.quant,
            "chunk_rows":   self.chunk_rows,
            "partition_by": self.partition_by,
        }


# ── Validation ────────────────────────────────────────────────────────────────

@dataclass
class ValidationError:
    field:   str
    message: str

    def __str__(self) -> str:
        return f"{self.field}: {self.message}"


def validate(raw: Dict[str, Any]) -> List[ValidationError]:
    errors: List[ValidationError] = []

    for f in ("name", "source", "output", "codec"):
        if not raw.get(f):
            errors.append(ValidationError(f, f'"{f}" is required'))

    codec = raw.get("codec", "")
    if codec and codec not in VALID_CODECS:
        errors.append(ValidationError("codec",
            f'"{codec}" is not valid — use: {", ".join(sorted(VALID_CODECS))}'))

    quant = raw.get("quant", 0)
    if not isinstance(quant, (int, float)) or quant < 0:
        errors.append(ValidationError("quant", '"quant" must be a non-negative number'))

    chunk_rows = raw.get("chunk_rows", DEFAULT_CHUNK_ROWS)
    if not isinstance(chunk_rows, int) or chunk_rows < 1000:
        errors.append(ValidationError("chunk_rows", '"chunk_rows" must be an integer >= 1000'))

    priority = raw.get("priority", DEFAULT_PRIORITY)
    if priority not in VALID_PRIORITIES:
        errors.append(ValidationError("priority",
            f'"priority" must be one of: {", ".join(VALID_PRIORITIES)}'))

    return errors


# ── Parser ────────────────────────────────────────────────────────────────────

def _require_yaml():
    try:
        import yaml
        return yaml
    except ImportError:
        raise ImportError(
            "PyYAML is required to parse .ice files. "
            "Install it with: pip install pyyaml"
        )


def parse_file(path: str) -> IceRecipe:
    """Parse a single .ice file from disk. Raises ValueError on validation failure."""
    yaml = _require_yaml()
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: .ice file must be a YAML mapping, got {type(raw).__name__}")
    return parse_dict(raw, source_file=path)


def parse_dict(raw: Dict[str, Any], source_file: Optional[str] = None) -> IceRecipe:
    """Parse and validate a dict (already loaded from YAML/JSON). Raises ValueError."""
    errors = validate(raw)
    if errors:
        msg = "; ".join(str(e) for e in errors)
        raise ValueError(f"Invalid .ice recipe: {msg}")

    tags = raw.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",")]

    return IceRecipe(
        name         = str(raw["name"]),
        source       = str(raw["source"]),
        output       = str(raw["output"]),
        codec        = str(raw["codec"]),
        quant        = int(raw.get("quant", 0)),
        chunk_rows   = int(raw.get("chunk_rows", DEFAULT_CHUNK_ROWS)),
        partition_by = raw.get("partition_by"),
        schedule     = raw.get("schedule"),
        enabled      = bool(raw.get("enabled", True)),
        timezone     = raw.get("timezone"),
        workers      = raw.get("workers"),
        priority     = str(raw.get("priority", DEFAULT_PRIORITY)),
        retry        = int(raw.get("retry", DEFAULT_RETRY)),
        timeout_minutes = int(raw.get("timeout_minutes", 0)),
        description  = raw.get("description"),
        owner        = raw.get("owner"),
        tags         = list(tags),
        version      = str(raw.get("version", ICE_VERSION)),
        source_file  = source_file,
    )


def _etag(path: str) -> str:
    """Fingerprint a local file by mtime+size (cheap, no read)."""
    st = os.stat(path)
    return hashlib.md5(f"{st.st_mtime}:{st.st_size}".encode()).hexdigest()


# ── Local directory watcher ───────────────────────────────────────────────────

class LocalWatcher:
    """Polls a local directory for .ice files and notifies on changes.

    Args:
        watch_path:    Directory path to watch (recursive=False).
        poll_interval: Seconds between scans.
        on_add:        Called with IceRecipe when a new/modified file is found.
        on_remove:     Called with recipe name when a file is deleted.
    """

    def __init__(
        self,
        watch_path: str,
        poll_interval: float = 30.0,
        on_add:    Optional[Callable[[IceRecipe], None]] = None,
        on_remove: Optional[Callable[[str], None]]       = None,
    ) -> None:
        self.watch_path    = watch_path
        self.poll_interval = poll_interval
        self.on_add        = on_add    or (lambda r: None)
        self.on_remove     = on_remove or (lambda n: None)
        self._known: Dict[str, str] = {}   # filename → etag
        self._stop  = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="ice-watcher")
        self._thread.start()
        log.info("[IceWatcher] watching %s every %ss", self.watch_path, self.poll_interval)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _scan(self) -> None:
        if not os.path.isdir(self.watch_path):
            return

        current: Dict[str, str] = {}
        for fname in os.listdir(self.watch_path):
            if not fname.endswith(".ice"):
                continue
            fpath = os.path.join(self.watch_path, fname)
            try:
                etag = _etag(fpath)
                current[fname] = etag
                if self._known.get(fname) != etag:
                    recipe = parse_file(fpath)
                    recipe.source_etag = etag
                    log.info("[IceWatcher] loaded: %s", recipe.name)
                    self.on_add(recipe)
            except Exception as exc:
                log.warning("[IceWatcher] skipping %s: %s", fname, exc)

        # Detect removals
        for fname in list(self._known):
            if fname not in current:
                name = fname.removesuffix(".ice")
                log.info("[IceWatcher] removed: %s", name)
                self.on_remove(name)

        self._known = current

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._scan()
            except Exception as exc:
                log.error("[IceWatcher] scan error: %s", exc)
            self._stop.wait(self.poll_interval)


# ── S3 watcher ────────────────────────────────────────────────────────────────

class S3Watcher:
    """Polls an S3 prefix for .ice files using ETags for change detection.

    Requires boto3: pip install boto3

    Args:
        bucket:        S3 bucket name.
        prefix:        Key prefix to scan (e.g. "ice/").
        poll_interval: Seconds between scans.
        on_add:        Called with IceRecipe when a file is new/modified.
        on_remove:     Called with recipe name when a key is deleted.
        endpoint_url:  Custom endpoint (MinIO, LocalStack, etc.).
    """

    def __init__(
        self,
        bucket: str,
        prefix: str = "ice/",
        poll_interval: float = 30.0,
        on_add:    Optional[Callable[[IceRecipe], None]] = None,
        on_remove: Optional[Callable[[str], None]]       = None,
        endpoint_url: Optional[str] = None,
        **boto_kwargs,
    ) -> None:
        self.bucket        = bucket
        self.prefix        = prefix
        self.poll_interval = poll_interval
        self.on_add        = on_add    or (lambda r: None)
        self.on_remove     = on_remove or (lambda n: None)
        self.endpoint_url  = endpoint_url
        self.boto_kwargs   = boto_kwargs
        self._known: Dict[str, str] = {}   # key → ETag
        self._stop  = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._s3 = None

    def _client(self):
        if self._s3 is None:
            import boto3
            self._s3 = boto3.client("s3", endpoint_url=self.endpoint_url, **self.boto_kwargs)
        return self._s3

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="ice-s3-watcher")
        self._thread.start()
        log.info("[IceS3Watcher] watching s3://%s/%s every %ss",
                 self.bucket, self.prefix, self.poll_interval)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _scan(self) -> None:
        yaml = _require_yaml()
        s3 = self._client()
        paginator = s3.get_paginator("list_objects_v2")
        current: Dict[str, str] = {}

        for page in paginator.paginate(Bucket=self.bucket, Prefix=self.prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if not key.endswith(".ice"):
                    continue
                etag = obj["ETag"].strip('"')
                current[key] = etag
                if self._known.get(key) != etag:
                    try:
                        resp = s3.get_object(Bucket=self.bucket, Key=key)
                        raw = yaml.safe_load(resp["Body"].read())
                        recipe = parse_dict(raw, source_file=f"s3://{self.bucket}/{key}")
                        recipe.source_etag = etag
                        log.info("[IceS3Watcher] loaded: %s from %s", recipe.name, key)
                        self.on_add(recipe)
                    except Exception as exc:
                        log.warning("[IceS3Watcher] skipping %s: %s", key, exc)

        for key in list(self._known):
            if key not in current:
                name = key.split("/")[-1].removesuffix(".ice")
                log.info("[IceS3Watcher] removed: %s", name)
                self.on_remove(name)

        self._known = current

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._scan()
            except Exception as exc:
                log.error("[IceS3Watcher] scan error: %s", exc)
            self._stop.wait(self.poll_interval)


# ── Factory ───────────────────────────────────────────────────────────────────

def make_watcher(
    watch_path: str,
    poll_interval: float = 30.0,
    on_add:    Optional[Callable[[IceRecipe], None]] = None,
    on_remove: Optional[Callable[[str], None]]       = None,
    **kwargs,
) -> "LocalWatcher | S3Watcher":
    """Create the right watcher based on the path scheme.

    Args:
        watch_path: ``s3://bucket/prefix/``, ``gs://...`` (future), or local path.
        poll_interval: Seconds between scans.
        on_add / on_remove: Callbacks.

    Returns:
        A started watcher instance.
    """
    if watch_path.startswith("s3://"):
        parts  = watch_path[5:].split("/", 1)
        bucket = parts[0]
        prefix = (parts[1] if len(parts) > 1 else "").rstrip("/") + "/"
        return S3Watcher(bucket, prefix, poll_interval, on_add, on_remove, **kwargs)
    else:
        return LocalWatcher(watch_path, poll_interval, on_add, on_remove)
