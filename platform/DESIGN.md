# Permafrost Platform — Design Document

> **Versão do doc:** 0.1 — 2026-05-18  
> **Status:** Em desenvolvimento — MVP com 3 telas  
> **Modelo:** Open-core. O framework permanece open-source; a plataforma é produto comercial.

---

## 1. Visão geral

A plataforma é uma **interface web de gestão** para arquivos `.permafrost` armazenados na infraestrutura do próprio cliente.

**Princípio fundamental:** nenhum dado passa pela plataforma. O cliente conecta sua própria instância do `catalog server` e do `cluster master`. A plataforma é só a camada visual.

```
┌─────────────────────────────────────────────────┐
│                  PLATAFORMA WEB                  │
│              (Next.js — hospedado)               │
└────────┬──────────────────────────┬─────────────┘
         │ HTTP                     │ HTTP
         ▼                          ▼
┌─────────────────┐      ┌───────────────────────┐
│  Catalog Server │      │    Cluster Master      │
│  (port 8800)    │      │    (port 8700)         │
│  permafrost     │      │    permafrost          │
│  catalog serve  │      │    cluster start       │
└────────┬────────┘      └──────────┬────────────┘
         │                          │
         ▼                          ▼
┌─────────────────┐      ┌───────────────────────┐
│  .permafrost    │      │  Workers (N)           │
│  files em       │      │  na infra do cliente   │
│  S3/GCS/Azure   │      └───────────────────────┘
│  ou local       │
└─────────────────┘
         ↑
    dados NUNCA
   saem da infra
    do cliente
```

**Configuração inicial pelo usuário (uma vez):**
1. Roda `permafrost catalog serve` na infra dele
2. Cola a URL + token no painel de configurações
3. (Opcional) Cola a URL do cluster master

---

## 2. Tech stack

| Camada | Tecnologia | Motivo |
|--------|-----------|--------|
| Framework frontend | **Next.js 14** (App Router) | SSR, API routes, TypeScript nativo |
| Linguagem | **TypeScript** | Type safety nas chamadas ao catalog server |
| UI | **Tailwind CSS + shadcn/ui** | Componentes prontos, customizáveis, sem opinião de design |
| Gráficos | **Recharts** | Leve, declarativo, React-first |
| Fetching / cache | **TanStack Query v5** | Cache automático, refetch, loading/error states |
| Estado global | **Zustand** | Simples, sem boilerplate para config do agente |
| Auth | **JWT** via cookie httpOnly | Aproveita o RBAC já existente no cluster |
| Ícones | **Lucide React** | Consistente com shadcn/ui |

**Não usar:**
- Redux (overkill para o MVP)
- GraphQL (o catalog server já é REST)
- ORMs no frontend (sem banco na plataforma)

---

## 3. Estrutura de pastas (Next.js)

```
platform/
├── app/
│   ├── layout.tsx              # root layout (sidebar + header)
│   ├── page.tsx                # redirect → /catalog
│   ├── catalog/
│   │   ├── page.tsx            # Tela 1: Catalog Browser
│   │   └── [name]/
│   │       └── page.tsx        # detalhe de um dataset
│   ├── cost/
│   │   └── page.tsx            # Tela 2: Cost Dashboard
│   ├── jobs/
│   │   └── page.tsx            # Tela 3: Job Trigger
│   └── settings/
│       └── page.tsx            # URLs do agente + token
├── components/
│   ├── catalog/
│   │   ├── DatasetTable.tsx
│   │   ├── DatasetDetailDrawer.tsx
│   │   ├── ChunkList.tsx
│   │   └── IntegrityBadge.tsx
│   ├── cost/
│   │   ├── SummaryCards.tsx
│   │   ├── TierComparisonChart.tsx
│   │   └── CostTable.tsx
│   ├── jobs/
│   │   ├── FreezeForm.tsx
│   │   ├── JobTable.tsx
│   │   └── JobProgressBar.tsx
│   └── shared/
│       ├── AgentStatusBadge.tsx
│       ├── EmptyState.tsx
│       └── ErrorBoundary.tsx
├── lib/
│   ├── api.ts                  # wrapper tipado sobre o catalog server
│   ├── cluster-api.ts          # wrapper sobre o cluster master
│   └── store.ts                # Zustand: config do agente
├── hooks/
│   ├── useCatalog.ts
│   ├── useCostReport.ts
│   └── useJobs.ts
└── types/
    └── catalog.ts              # tipos espelhando as respostas do catalog server
```

---

## 4. Tela 1 — Catalog Browser

**Objetivo:** visão completa de todos os datasets registrados; navegar, filtrar, inspecionar e gerenciar.

### Layout

```
┌──────────────────────────────────────────────────────────────────────┐
│  ❄ Permafrost                       [Catalog] [Cost] [Jobs]  ⚙ ●ok │
├──────────────────────────────────────────────────────────────────────┤
│  Datasets                                                    [+ Register] │
│                                                                       │
│  🔍 Search name...   Codec ▾   Tags ▾   [✓] Lossless only           │
│                                                                       │
│  ┌──────────┬────────┬──────────┬──────┬────────────┬──────────────┐ │
│  │ Name     │ Codec  │ Rows     │  MB  │ Frozen     │ Actions      │ │
│  ├──────────┼────────┼──────────┼──────┼────────────┼──────────────┤ │
│  │ sales    │ lzma2  │ 210,000k │ 3.0  │ 2026-01-15 │ 👁 🔍 🗑    │ │
│  │ returns  │ zstd   │  12,500  │ 0.2  │ 2026-01-10 │ 👁 🔍 🗑    │ │
│  │ users    │ lzma2  │  85,000  │ 1.1  │ 2025-12-01 │ 👁 🔍 🗑    │ │
│  └──────────┴────────┴──────────┴──────┴────────────┴──────────────┘ │
│                                                 3 datasets · 305 MB  │
└──────────────────────────────────────────────────────────────────────┘
```

**Ao clicar em 👁 (detalhe) — drawer lateral:**

```
┌──────────────────────────────────────────┐
│  sales                          [×]       │
│  ─────────────────────────────────────   │
│  Codec     lzma2                          │
│  Rows      210,000,000                    │
│  File      3.03 GB                        │
│  Frozen    2026-01-15 14:22:00            │
│  Partition year                           │
│  Schema    id(int64), date(ts), ...       │
│                                           │
│  Versions                                 │
│  ● v2024  (2026-01-15)  210M rows         │
│  ○ v2023  (2025-01-10)  198M rows         │
│                                           │
│  Integrity    [▶ Run Check]               │
│  ✓ 4200 chunks OK  ·  last: 2026-01-16   │
│                                           │
│  Chunks (4200)            [▼ expand]      │
│  #0  rows 0–49999   offset 1024   48KB   │
│  #1  rows 50k–99k   offset 50688  51KB   │
│  ...                                      │
└──────────────────────────────────────────┘
```

### Componentes

| Componente | Responsabilidade |
|-----------|-----------------|
| `DatasetTable` | Tabela principal com sort, paginação client-side |
| `FilterBar` | Inputs de busca + dropdowns de codec/tags + toggle lossless |
| `DatasetDetailDrawer` | Drawer com todas as infos, versões, integrity e chunks |
| `IntegrityBadge` | Badge colorido (verde OK / vermelho FAIL / cinza not-checked) |
| `RegisterModal` | Form para registrar novo arquivo via `POST /datasets/register` |
| `ChunkList` | Tabela colapsável do sparse index |

### Chamadas de API

```typescript
// Listar/filtrar datasets
GET /datasets?name=sales&lossless_only=false&codec=lzma2

// Detalhe + versões
GET /datasets/{name}
GET /datasets/{name}/versions
GET /datasets/{name}/chunks

// Integrity check (dispara e aguarda)
GET /datasets/{name}/integrity

// Deletar
DELETE /datasets/{name}

// Registrar
POST /datasets/register  { path, name, version, tags }
```

### Estados a tratar

- **Loading:** skeleton table enquanto carrega
- **Empty:** "No datasets registered yet" + botão Register
- **Erro de conexão:** banner vermelho "Cannot reach catalog server" + link para Settings
- **Integrity running:** spinner no badge enquanto o check executa (pode demorar para arquivos grandes)

---

## 5. Tela 2 — Cost Dashboard

**Objetivo:** mostrar o custo de storage atual e projeções, com comparação entre tiers.

### Layout

```
┌──────────────────────────────────────────────────────────────────────┐
│  ❄ Permafrost                       [Catalog] [Cost] [Jobs]  ⚙ ●ok │
├──────────────────────────────────────────────────────────────────────┤
│  Cost Dashboard                                                       │
│                                                                       │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────┐  │
│  │  Total Storage  │  │  Monthly Cost   │  │   Annual Savings    │  │
│  │    305.3 MB     │  │    $0.0003      │  │  vs S3 Standard     │  │
│  │   3 datasets    │  │  glacier deep   │  │     -94.2%          │  │
│  └─────────────────┘  └─────────────────┘  └─────────────────────┘  │
│                                                                       │
│  Tier Comparison                   Tier  [glacier_deep ▾]            │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │  $0.007 ├─────────────────────────────────── s3_standard     │    │
│  │  $0.004 ├──────────────────────── s3_ia                      │    │
│  │  $0.001 ├────────────── glacier                              │    │
│  │  $0.000 ├──── glacier_deep  ← current                        │    │
│  │         └──────────────────────────────────────────────────  │    │
│  │              sales     returns     users                      │    │
│  └──────────────────────────────────────────────────────────────┘    │
│                                                                       │
│  Per-dataset breakdown (tier: glacier_deep)                           │
│  ┌──────────┬──────┬──────────────┬──────────────┬────────────────┐  │
│  │ Dataset  │  MB  │ Monthly USD  │ Annual USD   │ 3-Year USD     │  │
│  ├──────────┼──────┼──────────────┼──────────────┼────────────────┤  │
│  │ sales    │ 3010 │    $0.00030  │   $0.00359   │    $0.01078    │  │
│  │ users    │  110 │    $0.000011 │   $0.000131  │    $0.000393   │  │
│  │ returns  │   20 │    $0.000002 │   $0.000024  │    $0.000072   │  │
│  └──────────┴──────┴──────────────┴──────────────┴────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
```

### Componentes

| Componente | Responsabilidade |
|-----------|-----------------|
| `SummaryCards` | 3 cards: total storage, monthly cost no tier atual, savings vs s3_standard |
| `TierComparisonChart` | Grouped bar chart (Recharts) com os 4 tiers × datasets |
| `TierSelector` | Dropdown para trocar o tier de referência dos cards e tabela |
| `CostTable` | Tabela ordenável por monthly_cost, annual, 3yr |
| `SavingsCallout` | Destaque textual "Você economiza X% vs armazenar CSV puro" |

### Cálculo de savings

```typescript
// savings vs CSV sem compressão (estimativa 1:1 com s3_standard)
const csvEquivalentMB = totalMB * avgCompressionRatio  // ratio vem do audit
const csvMonthlyCost  = (csvEquivalentMB / 1024) * PRICES.s3_standard
const actualCost      = (totalMB / 1024) * PRICES[selectedTier]
const savingsPct      = ((csvMonthlyCost - actualCost) / csvMonthlyCost) * 100
```

### Chamadas de API

```typescript
GET /stats                         // total_mb, total_datasets, total_rows
GET /cost_report?tier=glacier_deep // lista por dataset com custos
GET /cost_report?tier=s3_standard  // para calcular baseline de savings
```

### Estados a tratar

- **Catálogo vazio:** empty state "Register datasets to see cost estimates"
- **Tier inválido:** impossível — o dropdown só mostra os 4 tiers válidos
- **Valores muito pequenos:** formatar com notação científica ou "< $0.01"

---

## 6. Tela 3 — Job Trigger

**Objetivo:** submeter jobs de freeze ao cluster e acompanhar o progresso em tempo real.

> Esta tela requer a URL do **cluster master** (não do catalog server).

### Layout

```
┌──────────────────────────────────────────────────────────────────────┐
│  ❄ Permafrost                       [Catalog] [Cost] [Jobs]  ⚙ ●ok │
├──────────────────────────────────────────────────────────────────────┤
│  Freeze Jobs                                          [+ New Job]    │
│                                                                       │
│  ┌─── New Freeze Job ────────────────────────────────────────────┐   │
│  │  Source file     [/data/raw/sales_2024.csv             ]      │   │
│  │  Output path     [s3://my-bucket/cold/sales_2024.permafrost ] │   │
│  │  Codec           [lzma2 ▾]    Partition by  [year ▾]         │   │
│  │  Chunk rows      [50000     ]                                 │   │
│  │                                              [Cancel] [▶ Run] │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                       │
│  Cluster: 2 workers idle · 0 running                                 │
│                                                                       │
│  ┌──────────┬─────────┬────────────────────────┬───────┬─────────┐  │
│  │ Job ID   │ Status  │ Progress               │ Ratio │ Started │  │
│  ├──────────┼─────────┼────────────────────────┼───────┼─────────┤  │
│  │ a3f1b2c4 │ ● done  │ ████████████████ 100%  │ 8.4×  │ 14:22  │  │
│  │ e9d2a1f7 │ ◌ run   │ ████████░░░░░░░░  52%  │  —    │ 14:31  │  │
│  │ 2b8c3e01 │ ○ queue │ ░░░░░░░░░░░░░░░░   0%  │  —    │ 14:31  │  │
│  └──────────┴─────────┴────────────────────────┴───────┴─────────┘  │
│                                                                       │
│  e9d2a1f7 detail                                                      │
│  ├─ Task 0  ● done    rows 0–49,999    worker ft-w01   3.2s          │
│  ├─ Task 1  ◌ running rows 50k–99k     worker ft-w02   …             │
│  └─ Task 2  ○ queued  rows 100k–149k   —                              │
└──────────────────────────────────────────────────────────────────────┘
```

### Componentes

| Componente | Responsabilidade |
|-----------|-----------------|
| `FreezeForm` | Formulário: source, output, codec, partition_by, chunk_rows |
| `ClusterStatusBar` | Workers idle/running em tempo real via polling |
| `JobTable` | Lista de jobs com status, progress bar, ratio |
| `JobDetailPanel` | Expandível: tasks por chunk com worker, tempo, status |
| `JobProgressBar` | Barra de progresso calculada de `tasks_done / tasks_total` |
| `StatusBadge` | `pending` (cinza) / `running` (azul) / `done` (verde) / `failed` (vermelho) |

### Polling strategy

```typescript
// Job ativo: polling a cada 2s
// Job concluído ou falho: para o polling
// Hook com TanStack Query refetchInterval dinâmico:

useQuery({
  queryKey: ['job', jobId],
  queryFn: () => clusterApi.getJob(jobId),
  refetchInterval: (data) =>
    data?.status === 'done' || data?.status === 'failed' ? false : 2000,
})
```

### Cálculo do progress

```typescript
// O cluster retorna tasks[] com status por chunk
const done  = tasks.filter(t => t.status === 'done').length
const total = tasks.length
const pct   = total > 0 ? Math.round((done / total) * 100) : 0
```

### Chamadas de API (cluster master)

```typescript
// Cluster health + workers
GET  /health                    → { workers, idle_workers, ... }

// Submeter job
POST /jobs                      → { job_id, status: "pending" }
  body: { source_path, output_path, codec, partition_by, chunk_rows }

// Status do job (polling)
GET  /jobs/{job_id}             → { status, tasks: [...], ratio }

// Cancelar
DELETE /jobs/{job_id}           (se implementado no master)
```

### Estados a tratar

- **Cluster unreachable:** banner "Cluster not configured — go to Settings" (não bloqueia Catalog/Cost)
- **Job failed:** linha vermelha + mensagem de erro na task que falhou
- **0 workers:** aviso "No workers registered — job will queue until a worker connects"
- **Source file não existe:** validação no form antes de submeter (opcional, o master já valida)

---

## 7. Tela de Settings (suporte)

Não é uma das 3 telas principais, mas é pré-requisito para as outras funcionarem.

```
┌──────────────────────────────────────────┐
│  Settings                                │
│                                          │
│  Catalog Agent                           │
│  URL    [http://localhost:8800     ]      │
│  Token  [••••••••••••••••••••••••  ]      │
│         [Test connection] ✓ Connected     │
│                                          │
│  Cluster Master (optional)               │
│  URL    [http://localhost:8700     ]      │
│  Token  [••••••••••••••••••••••••  ]      │
│         [Test connection] ✓ Connected     │
│                                          │
│                           [Save]         │
└──────────────────────────────────────────┘
```

Configurações salvas em `localStorage` (MVP) — nenhum dado sensível vai para o servidor da plataforma.

---

## 8. Tipos TypeScript

```typescript
// types/catalog.ts

export interface Dataset {
  id: number
  name: string
  codec: string
  quant: number
  rows: number
  mb: number
  partition_col: string | null
  freeze_date: string
  comment: string | null
}

export interface DatasetVersion {
  id: number
  name: string
  version: string | null
  path: string
  freeze_date: string
  orig_rows: number
  file_size_mb: number
  registered_at: string
}

export interface Chunk {
  chunk_id: number
  row_start: number
  row_end: number
  part_key: string | null
  byte_offset: number
  byte_len: number
  sha256: string
  kb: number
}

export interface IntegrityResult {
  name: string
  status: 'OK' | 'FILE_MISSING' | 'CORRUPTED' | 'RESOLVE_ERROR'
  chunks_ok: number
  chunks_fail: number
  path: string
}

export interface CostRow {
  name: string
  codec: string
  quant: string
  rows: number
  size_mb: number
  n_chunks: number
  freeze_date: string
  cost_monthly_usd: number
  cost_annual_usd: number
  cost_3yr_usd: number
  tier: string
}

export interface CatalogStats {
  total_datasets: number
  total_rows: number
  total_mb: number
  total_chunks: number
  avg_mb_per_1k_rows: number | null
  distinct_codecs: number
  lossless_count: number
  vault_count: number
}

export interface AgentConfig {
  catalogUrl: string
  catalogToken: string
  clusterUrl: string
  clusterToken: string
}
```

---

## 9. API client tipado

```typescript
// lib/api.ts

import { Dataset, DatasetVersion, Chunk, IntegrityResult, CostRow, CatalogStats } from '@/types/catalog'

export class CatalogClient {
  constructor(private baseUrl: string, private token?: string) {}

  private headers() {
    return this.token
      ? { Authorization: `Bearer ${this.token}` }
      : {}
  }

  async health() {
    const r = await fetch(`${this.baseUrl}/health`, { headers: this.headers() })
    return r.json()
  }

  async listDatasets(params?: {
    name?: string
    codec?: string
    lossless_only?: boolean
    tags_contain?: string
    min_rows?: number
    max_mb?: number
  }): Promise<Dataset[]> {
    const qs = new URLSearchParams(params as Record<string, string>)
    const r = await fetch(`${this.baseUrl}/datasets?${qs}`, { headers: this.headers() })
    return r.json()
  }

  async getVersions(name: string): Promise<DatasetVersion[]> {
    const r = await fetch(`${this.baseUrl}/datasets/${name}/versions`, { headers: this.headers() })
    return r.json()
  }

  async getChunks(name: string, part_key?: string): Promise<Chunk[]> {
    const qs = part_key ? `?part_key=${part_key}` : ''
    const r = await fetch(`${this.baseUrl}/datasets/${name}/chunks${qs}`, { headers: this.headers() })
    return r.json()
  }

  async integrity(name: string): Promise<IntegrityResult[]> {
    const r = await fetch(`${this.baseUrl}/datasets/${name}/integrity`, { headers: this.headers() })
    return r.json()
  }

  async deleteDataset(name: string) {
    const r = await fetch(`${this.baseUrl}/datasets/${name}`, {
      method: 'DELETE', headers: this.headers(),
    })
    return r.json()
  }

  async register(payload: { path: string; name?: string; version?: string; tags?: string[] }) {
    const r = await fetch(`${this.baseUrl}/datasets/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...this.headers() },
      body: JSON.stringify(payload),
    })
    return r.json()
  }

  async stats(): Promise<CatalogStats> {
    const r = await fetch(`${this.baseUrl}/stats`, { headers: this.headers() })
    return r.json()
  }

  async costReport(tier = 'glacier_deep'): Promise<CostRow[]> {
    const r = await fetch(`${this.baseUrl}/cost_report?tier=${tier}`, { headers: this.headers() })
    return r.json()
  }
}
```

---

## 10. Ordem de desenvolvimento

### Sprint 1 — Fundação (1–2 dias)
1. `npx create-next-app@latest platform --typescript --tailwind --app`
2. Instalar dependências: `shadcn/ui`, `tanstack-query`, `zustand`, `recharts`, `lucide-react`
3. Layout shell: sidebar com 3 links + header com status badge
4. Tela Settings: campos URL/token + botão "Test connection" → `GET /health`
5. `lib/api.ts` com `CatalogClient` tipado
6. `lib/store.ts` com Zustand para `AgentConfig`

### Sprint 2 — Tela 1: Catalog Browser (2–3 dias)
1. `DatasetTable` com dados reais via `GET /datasets`
2. `FilterBar` conectado à query
3. `DatasetDetailDrawer` com versions + chunks
4. `IntegrityBadge` + botão "Run Check"
5. `RegisterModal` com form + `POST /datasets/register`
6. Delete com confirmação

### Sprint 3 — Tela 2: Cost Dashboard (1–2 dias)
1. `SummaryCards` com dados do `/stats` + `/cost_report`
2. `TierComparisonChart` (Recharts grouped bar)
3. `CostTable` ordenável
4. `TierSelector` dropdown

### Sprint 4 — Tela 3: Job Trigger (2–3 dias)
1. `lib/cluster-api.ts` com ClusterClient
2. `ClusterStatusBar` via `GET /health` no cluster
3. `FreezeForm` + submit para `POST /jobs`
4. `JobTable` com polling via TanStack Query
5. `JobDetailPanel` com tasks por chunk

### Sprint 5 — Polimento (1 dia)
- Empty states em todas as telas
- Error boundaries e banners de conexão
- Loading skeletons
- Responsividade mobile (tablet no mínimo)

**Total estimado MVP:** 7–11 dias de desenvolvimento.

---

## 11. Decisões que ficam para depois do MVP

| Feature | Motivo de deixar para depois |
|---------|------------------------------|
| Auth próprio (login/senha) | MVP assume que a URL é interna; adicionar auth é sprint separado |
| Multi-agente (vários catalog servers) | Adicionar após validar com betas |
| Alertas (integrity failing, custo acima de threshold) | Precisa de persistência backend |
| Dark mode | Shadcn/ui suporta, mas não é bloqueante |
| Export CSV do cost report | Quick win mas não prioritário |
| Histórico de jobs (persistido) | O cluster não persiste entre restarts — precisa de store próprio |
