# Especificação Formal — Formato `.permafrost` v1.0

> **Status:** Draft · Implementação de referência: `src/permafrost_codec.py`

---

## 1. Identificação

| Campo | Valor |
|---|---|
| Extensão | `.permafrost` |
| Magic bytes | `50 52 4D 53` ("PRMS") |
| EOF magic | `53 4D 52 50` ("SMRP" — PRMS invertido) |
| Versão atual | 1.0 |
| Endianness | Big-endian em todos os campos multi-byte |

## 2. Layout Geral

```
┌──────────────────────────────────────────┐
│  HEADER BODY                             │
│  (todos os campos abaixo)                │
├──────────────────────────────────────────┤
│  HEADER SHA-256 (32 bytes)               │
├──────────────────────────────────────────┤
│  PAYLOAD COMPRIMIDO (variável)           │
├──────────────────────────────────────────┤
│  PAYLOAD SHA-256 (32 bytes)              │
├──────────────────────────────────────────┤
│  EOF MAGIC "SMRP" (4 bytes)             │
└──────────────────────────────────────────┘
```

## 3. Campos do Header

| Offset | Tamanho | Campo | Tipo | Descrição |
|---|---|---|---|---|
| 0 | 4B | magic | bytes | `PRMS` (0x50524D53) |
| 4 | 2B | version | u8[2] | [major, minor] — atualmente [1, 0] |
| 6 | 2B | flags | u16 BE | Bitmask de features |
| 8 | 1B | codec_id | u8 | Codec do payload |
| 9 | 1B | quant_level | u8 | Nível de quantização |
| 10 | 4B | schema_len | u32 BE | Comprimento do Arrow schema |
| 14 | var | schema | bytes | Apache Arrow schema serializado |
| +0 | 4B | manifest_len | u32 BE | Comprimento do manifesto JSON |
| +4 | var | manifest | UTF-8 | JSON dos preditores por coluna |
| +0 | 1B | comment_len | u8 | Comprimento do comentário (max 255) |
| +1 | var | comment | UTF-8 | Comentário livre |
| +0 | 8B | freeze_ts | i64 BE | Unix timestamp do freeze |
| +8 | 4B | retention_days | u32 BE | 0 = permanente |
| +12 | 8B | original_rows | u64 BE | Linhas antes do dedup |
| +20 | 8B | stored_rows | u64 BE | Linhas armazenadas |
| +28 | 8B | original_bytes | u64 BE | Tamanho original em bytes |
| +36 | 8B | payload_len | u64 BE | Tamanho do payload comprimido |

## 4. Flags (bitmask u16)

| Bit | Máscara | Nome | Descrição |
|---|---|---|---|
| 0 | `0x0001` | FLAG_DELTA | Delta encoding aplicado em IDs |
| 1 | `0x0002` | FLAG_QUANTIZE | Quantização aplicada (semi-lossy) |
| 2 | `0x0004` | FLAG_SOLID | Compressão solid multi-chunk |
| 3 | `0x0008` | FLAG_PREDICTOR | Preditores colunares ativos |

## 5. Codec IDs

| ID | Nome | Descrição |
|---|---|---|
| `0x01` | Zstd | Zstandard level 19, threads=2 |
| `0x02` | LZMA2 | XZ format, preset=EXTREME\|9 |
| `0x03` | ZPAQ | ZPAQ method=5, context mixing (reservado) |

## 6. Quant Levels

| Level | Nome | Lat/Lon | Preços | Timestamps |
|---|---|---|---|---|
| `0x00` | NONE (lossless) | 6 casas | 2 decimais | exato |
| `0x01` | HIGH | 5 casas | 1 decimal | exato |
| `0x02` | MEDIUM | 4 casas | inteiro | floor(minuto) |
| `0x03` | LOW | 3 casas | dezena | floor(hora) |

## 7. Manifesto de Preditores (JSON)

Exemplo de manifesto para um dataset com 4 colunas:

```json
{
  "id": {
    "name": "id",
    "dtype": "int32",
    "predictor": "delta_zigzag",
    "scale": 1
  },
  "preco_unitario": {
    "name": "preco_unitario",
    "dtype": "float64",
    "predictor": "lag1_zigzag",
    "scale": 100
  },
  "status": {
    "name": "status",
    "dtype": "object",
    "predictor": "category_u8",
    "scale": 1,
    "categories": ["Ativo", "Cancelado", "Inativo", "Pendente"]
  },
  "data": {
    "name": "data",
    "dtype": "datetime64[ns]",
    "predictor": "ts_delta_s",
    "scale": 1
  }
}
```

## 8. Layout do Payload (antes da compressão)

```
[n_cols: u16 BE]
  [col_name_len: u8]
  [col_name: bytes]
  [stream_len: u32 BE]
  [stream_data: bytes]
  ... (repetido n_cols vezes)
```

## 9. Preditores

### 9.1 delta_zigzag
```
Encode: deltas = diff(vals, prepend=0)
        zigzag = where(d>=0, d*2, -d*2-1).astype(uint32)
        output = zigzag.tobytes()

Decode: arr = frombuffer(data, uint32).astype(uint64)
        deltas = zigzag_dec(arr)
        vals = cumsum(deltas)
```

### 9.2 lag1_zigzag
```
Encode: pred[0]=0, pred[i]=vals[i-1]
        residuals = vals - pred
        zigzag = where(r>=0, r*2, -r*2-1).astype(uint32)
        output = (zigzag * scale).tobytes()  [scale aplicado antes]

Decode: arr = frombuffer(data, uint32).astype(uint64)
        residuals = zigzag_dec(arr)
        vals = cumsum(residuals)
        result = vals / scale
```

### 9.3 ts_delta_s
```
Encode: ts_sec = datetime.astype(int64) // 1e9
        if quant>=MEDIUM: ts_sec = (ts_sec // 60) * 60
        deltas = diff(ts_sec, prepend=0)
        zigzag = zigzag_enc(deltas).astype(uint32)

Decode: arr = frombuffer(data, uint32).astype(uint64)
        deltas = zigzag_dec(arr)
        ts_sec = cumsum(deltas)
        result = pd.to_datetime(ts_sec, unit='s')
```

### 9.4 category_u8
```
Encode: codes = series.astype('category').cat.codes.astype(uint8)
        categories armazenadas no manifesto JSON

Decode: codes = frombuffer(data, uint8)
        categories lidas do manifesto
        result = Categorical.from_codes(codes, categories)
```

### 9.5 raw_text
```
Encode: text = '\x00'.join(series.astype(str).values)
        output = text.encode('utf-8')

Decode: result = data.decode('utf-8').split('\x00')
```

## 10. Verificação de Integridade

O processo de leitura deve seguir esta ordem:

1. Verificar magic bytes (primeiros 4B = `PRMS`)
2. Verificar EOF magic (últimos 4B = `SMRP`)
3. Parsear header até `payload_len`
4. Calcular SHA-256 do header body → comparar com `HEADER_SHA256`
5. Ler `payload_compressed` (exatamente `payload_len` bytes)
6. Calcular SHA-256 do payload → comparar com `PAYLOAD_SHA256`
7. Descomprimir payload com o codec indicado em `codec_id`
8. Reconstruir colunas usando os preditores do manifesto

Qualquer falha nos passos 1–6 deve abortar com erro antes de executar o passo 7.

## 11. Garantias de Compatibilidade

- Arquivos `.permafrost` v1.x devem ser legíveis por qualquer implementação v1.y (y >= x)
- Novos codec_ids podem ser adicionados sem quebrar leitores existentes (retornam erro claro)
- O campo `version` permite detectar versões incompatíveis futuras
- O schema Arrow embutido garante reconstrução sem metadados externos

## 12. Implementação de Referência

`src/permafrost_codec.py` — Python 3.10+

Dependências mínimas para um leitor standalone:
- `struct` (stdlib)
- `hashlib` (stdlib)
- `json` (stdlib)
- `lzma` (stdlib)
- `numpy`
- `pandas`
