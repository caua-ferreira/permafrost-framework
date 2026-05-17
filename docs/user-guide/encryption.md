# Encryption at Rest (AES-256-GCM)

Permafrost supports per-chunk AES-256-GCM encryption. Each chunk is independently encrypted with a unique nonce — partial reads (sparse index) remain possible even on encrypted files.

---

## Quick start

```python
import permafrost as pf
import pandas as pd

df = pd.read_csv("sensitive_data.csv")

# 32-byte key — in production, load from KMS or environment variable
key = b"my-secret-key-exactly-32-bytes!!"

# Freeze with encryption — same API, just add key=
metrics = pf.freeze(df, "sensitive.permafrost", codec=pf.CODEC_LZMA2, key=key)
print(metrics["encrypted"])   # True

# Thaw — provide the same key
df_back = pf.thaw("sensitive.permafrost", key=key)

# Selective read still works on encrypted files
df_2023 = pf.thaw("sensitive.permafrost", filter={"year": 2023}, key=key)
```

---

## Key from environment variable

```bash
export PERMAFROST_KEY="my-secret-key-exactly-32-bytes!!"
```

```python
# key= is not needed when PERMAFROST_KEY is set
metrics = pf.freeze(df, "sensitive.permafrost")
df_back = pf.thaw("sensitive.permafrost")
```

---

## KMS providers (production)

For production use, use a Key Management Service instead of a raw key in memory.

### AWS KMS

```python
from permafrost import AWSKMSProvider

provider = AWSKMSProvider(key_id="arn:aws:kms:us-east-1:123456789:key/abc-def")

metrics = pf.freeze(df, "sensitive.permafrost", key_provider=provider)
df_back = pf.thaw("sensitive.permafrost", key_provider=provider)
```

The encrypted DEK (Data Encryption Key) is stored in the file header. On `thaw()`, it is automatically sent to KMS for decryption — the raw key never leaves AWS.

### GCP Cloud KMS

```python
from permafrost import GCPKMSProvider

provider = GCPKMSProvider(
    key_name="projects/my-project/locations/global/keyRings/my-ring/cryptoKeys/my-key"
)

metrics = pf.freeze(df, "sensitive.permafrost", key_provider=provider)
```

---

## Streaming with encryption

```python
# freeze_file + encryption — processes files larger than RAM
pf.freeze_file(
    "large_sensitive.csv",
    "large_sensitive.permafrost",
    codec=pf.CODEC_ZSTD,
    key=key,
)

# thaw_iter + encryption — read in batches without loading all into memory
for batch in pf.thaw_iter("large_sensitive.permafrost", key=key, batch_size=50_000):
    process(batch)
```

---

## Cloud storage with encryption

```python
# Encrypt locally, upload encrypted bytes to S3
pf.freeze_to(df, "s3://my-bucket/sensitive.permafrost", key=key)

# Download encrypted chunks via Range Request, decrypt on the fly
df_back = pf.thaw_from("s3://my-bucket/sensitive.permafrost", key=key)
```

The data is encrypted **before** leaving the machine. S3 only ever sees encrypted bytes.

---

## Audit — inspect without decrypting

```python
info = pf.audit("sensitive.permafrost")

print(info["encrypted"])    # True
print(info["kms_name"])     # "local" or "aws-kms" or "gcp-kms"
print(info["edek_size"])    # size of the encrypted DEK in the header
# Note: audit() does not require the key — metadata is not encrypted
```

---

## Performance

Benchmarked on **210 million rows** (16.35 GB CSV → 3.25 GB ZSTD), 4,200 chunks of 50k rows each:

| Operation | Without encryption | With AES-256-GCM | Overhead |
|---|---|---|---|
| Freeze (210M rows) | 88.2 min | 77.9 min | ~0% |
| File size | 3.250 GB | 3.250 GB | **+0.00%** |
| Thaw 2022 (42M rows) | 2.0 min | 1.9 min | ~0% |
| Audit (no decryption) | — | 2.2 s | — |

**Storage overhead:** 28 bytes × 4,200 chunks = **114.8 KB** on a 3.25 GB file — unmeasurable at 0.00%.

**CPU overhead:** Negligible on any modern CPU with AES-NI instruction set (Intel ≥ Westmere, AMD ≥ Bulldozer, Apple Silicon). The slight timing variation shown above is within measurement noise.

!!! tip "Bottom line"
    AES-256-GCM encryption on Permafrost files is effectively free. You get full data-at-rest security with zero measurable impact on size or performance.

---

## How it works

Each chunk is independently encrypted with AES-256-GCM:

```
[CHUNK_LEN: 4B]
[NONCE: 12B]           ← random, unique per chunk
[CIPHERTEXT: var]      ← compressed data, encrypted
[GCM_TAG: 16B]         ← authentication tag (tamper detection)
[CHUNK_SHA256: 32B]    ← integrity check of the entire encrypted block
```

Key points:

- **Per-chunk nonces** — each chunk uses a unique random nonce, so identical plaintext chunks produce different ciphertext
- **Authenticated encryption** — the GCM tag detects any tampering with the ciphertext before decryption
- **Independent chunks** — each chunk can be decrypted independently, enabling sparse index reads on encrypted files
- **Envelope encryption** — when using KMS, a random DEK is generated per file, encrypted by the KMS, and stored in the header; the raw key never touches the application

**Storage overhead:** 28 bytes per chunk (12B nonce + 16B tag). For a file with 4,200 chunks, that is 117 KB — negligible.

---

## Security considerations

!!! warning "Key management"
    - Never hardcode keys in source code — use environment variables or KMS in production
    - A lost key means permanently lost data — there is no recovery mechanism
    - Rotate keys by re-freezing the data with a new key

!!! tip "Compliance"
    Permafrost's per-chunk encryption is compatible with LGPD, GDPR, HIPAA, and SOC 2 data-at-rest requirements. The file can be stored in any cloud without custodying plaintext data outside your own infrastructure.
