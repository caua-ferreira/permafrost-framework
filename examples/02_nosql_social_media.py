"""
Exemplo 02 — Dados NoSQL: JSONL de redes sociais
Demonstra o SchemaDetector com dados semi-estruturados.
Executar: python examples/02_nosql_social_media.py
"""
import permafrost as pf
import json, os, lzma, random
import numpy as np

print("❄  Permafrost — Dados NoSQL (Social Media)\n")

random.seed(42); np.random.seed(42)

# Gerar posts simulados
hashtags_pool = ["#python","#ia","#tech","#data","#cloud","#dev","#ml","#opensource"]
mentions_pool = [f"@user{i:03d}" for i in range(50)]
cities = [{"lat":-23.5,"lon":-46.6,"city":"São Paulo"},
          {"lat":-22.9,"lon":-43.2,"city":"Rio de Janeiro"}, None]

posts = []
for i in range(5000):
    posts.append({
        "id": str(1_000_000+i),
        "user_id": random.randint(1, 5000),
        "text": f"Post {i} sobre tecnologia e inovação #exemplo",
        "hashtags": random.sample(hashtags_pool, random.randint(0,3)),
        "mentions": random.sample(mentions_pool, random.randint(0,2)),
        "likes": int(np.random.exponential(50)),
        "shares": int(np.random.exponential(10)),
        "created_at": f"2024-0{random.randint(1,9)}-{random.randint(10,28)}T{random.randint(0,23):02d}:00:00Z",
        "platform": random.choice(["web","ios","android"]),
        "location": random.choice(cities),
        "verified": random.random() < 0.05,
    })

# Salvar como JSONL
jsonl_path = "/tmp/posts.jsonl"
with open(jsonl_path, "w") as f:
    for p in posts: f.write(json.dumps(p, ensure_ascii=False)+"\n")

jsonl_mb = os.path.getsize(jsonl_path)/1e6
print(f"JSONL raw: {len(posts):,} posts = {jsonl_mb:.3f} MB")

# Detectar schema
print("\n[1] SchemaDetector...")
det = pf.SchemaDetector()
df, dtype, manifest = det.detect(jsonl_path)
print(f"  Tipo detectado: {dtype}")
print(f"  Colunas: {list(df.columns)}")
print(f"  Linhas: {len(df):,}")
print("\n  Estratégia por campo:")
for field, m in manifest.items():
    print(f"    {field:15s}: {m.get('strategy','?'):20s} ({m.get('kind','?')})")

# Freeze
print("\n[2] Freeze...")
m_pf = pf.freeze(df, "/tmp/posts.permafrost", codec=pf.CODEC_LZMA2)

# Comparar com JSONL+LZMA2 direto
jsonl_lzma = lzma.compress(open(jsonl_path,"rb").read(), format=lzma.FORMAT_XZ, preset=9)
pf_mb = m_pf["stored_mb"]
lzma_mb = len(jsonl_lzma)/1e6

print(f"\n  JSONL raw:              {jsonl_mb:.3f} MB")
print(f"  JSONL + LZMA2 direto:   {lzma_mb:.3f} MB  (ratio={jsonl_mb/lzma_mb:.1f}×)")
print(f"  Permafrost LZMA2:       {pf_mb:.3f} MB  (ratio={m_pf['ratio']:.1f}×)")
print(f"  Vantagem Permafrost:    +{(lzma_mb-pf_mb)/lzma_mb*100:.1f}% + thaw seletivo")

# Thaw e verificar
df_back = pf.unfreeze("/tmp/posts.permafrost", verify=True)
print(f"\n[3] Thaw: {len(df_back):,} posts recuperados ✓")
print("\n✓ Exemplo NoSQL concluído!")
