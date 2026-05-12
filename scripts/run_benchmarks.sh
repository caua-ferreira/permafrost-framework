#!/usr/bin/env bash
# Executa todos os benchmarks do Permafrost Framework em sequência
# Uso: bash scripts/run_benchmarks.sh

set -e
cd "$(dirname "$0")/.."

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  PERMAFROST FRAMEWORK — BENCHMARK SUITE"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

echo "[1/3] Gerando dataset de amostra..."
python scripts/generate_dataset.py --rows 80000 --output data/samples/test.csv

echo ""
echo "[2/3] Benchmark 01 — Algoritmos de compressão..."
python benchmarks/01_compression_algorithms.py --rows 80000

echo ""
echo "[3/3] Benchmark 03 — Projeção 10 GB..."
python benchmarks/03_10gb_projection.py

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Resultados em: benchmarks/results/"
ls benchmarks/results/
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
