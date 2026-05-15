"""
Formato Binário Spec — verificação byte a byte do formato .permafrost v1.2.
Garante que o formato é estável e interoperável entre versões.
Executar: pytest tests/test_formato_binario_spec.py -v
"""
import os, shutil, tempfile, struct, json, hashlib
import pytest
import numpy as np
import pandas as pd
import permafrost as pf
from permafrost.codec import (
    MAGIC, EOF_MAGIC, VERSION,
    CODEC_LZMA2, CODEC_ZSTD, CODEC_ZPAQ,
    QUANT_NONE, QUANT_HIGH, QUANT_MEDIUM, QUANT_LOW,
    FLAG_DELTA, FLAG_QUANTIZE, FLAG_CHUNKED, FLAG_PREDICTOR, FLAG_INDEX,
    _read_header, _read_sparse_index, _sha256,
)


@pytest.fixture
def tmp():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d)


@pytest.fixture(scope="module")
def spec_file(tmp_path_factory):
    """Arquivo de referência para os testes de spec."""
    tmp = tmp_path_factory.mktemp("spec")
    np.random.seed(42); N = 5_000
    df = pd.DataFrame({
        "id":     np.arange(1, N+1, dtype=np.int32),
        "ano":    pd.date_range("2020-01-01", periods=N, freq="1h").year.astype(np.int16),
        "total":  np.round(np.random.uniform(1, 5000, N), 2),
        "status": np.random.choice(["Ativo","Inativo","Pendente"], N),
        "ts":     pd.date_range("2020-01-01", periods=N, freq="1h"),
    }).sort_values("ano").reset_index(drop=True)
    path = str(tmp / "spec_ref.permafrost")
    pf.freeze(df, path, codec=CODEC_LZMA2, quant=QUANT_NONE,
              partition_by="ano", chunk_rows=1000,
              comment="spec test v1.2")
    raw = open(path, "rb").read()
    return path, df, N, raw


# ══════════════════════════════════════════════════════════════════════════════
# §1 MAGIC BYTES E VERSÃO
# ══════════════════════════════════════════════════════════════════════════════

class TestMagicEVersao:

    def test_magic_prms_posicao_0_a_3(self, spec_file):
        _, _, _, raw = spec_file
        assert raw[0:4] == b"PRMS", f"Magic errado: {raw[0:4]}"

    def test_magic_prms_e_literal_ascii(self, spec_file):
        _, _, _, raw = spec_file
        assert raw[0] == ord('P')
        assert raw[1] == ord('R')
        assert raw[2] == ord('M')
        assert raw[3] == ord('S')

    def test_eof_magic_smrp_ultimos_4_bytes(self, spec_file):
        _, _, _, raw = spec_file
        assert raw[-4:] == b"SMRP", f"EOF magic errado: {raw[-4:]}"

    def test_eof_magic_e_prms_invertido(self, spec_file):
        _, _, _, raw = spec_file
        assert raw[-4:] == MAGIC[::-1]

    def test_version_field_bytes_4_e_5(self, spec_file):
        _, _, _, raw = spec_file
        major = raw[4]
        minor = raw[5]
        assert major == 1, f"Major version: {major}"
        assert minor == 2, f"Minor version: {minor}"

    def test_version_field_igual_constante(self, spec_file):
        _, _, _, raw = spec_file
        assert raw[4:6] == VERSION

    def test_arquivo_comeca_com_magic_e_version(self, spec_file):
        _, _, _, raw = spec_file
        assert raw[0:4] == MAGIC
        assert raw[4:6] == VERSION


# ══════════════════════════════════════════════════════════════════════════════
# §2 FLAGS
# ══════════════════════════════════════════════════════════════════════════════

class TestFlags:

    def test_flags_campo_bytes_6_e_7(self, spec_file):
        _, _, _, raw = spec_file
        flags = struct.unpack_from(">H", raw, 6)[0]
        # Arquivo criado com partition_by → deve ter FLAG_INDEX e FLAG_PREDICTOR
        assert flags & FLAG_PREDICTOR, "FLAG_PREDICTOR ausente"
        assert flags & FLAG_DELTA,     "FLAG_DELTA ausente"
        assert flags & FLAG_CHUNKED,   "FLAG_CHUNKED ausente"
        assert flags & FLAG_INDEX,     "FLAG_INDEX ausente"

    def test_flag_quantize_ausente_em_lossless(self, spec_file):
        _, _, _, raw = spec_file
        flags = struct.unpack_from(">H", raw, 6)[0]
        assert not (flags & FLAG_QUANTIZE), "FLAG_QUANTIZE presente em arquivo lossless"

    def test_flag_quantize_presente_em_vault(self, tmp):
        df = pd.DataFrame({"id": range(100), "v": np.random.rand(100)})
        path = os.path.join(tmp, "vault.permafrost")
        pf.freeze(df, path, quant=QUANT_MEDIUM)
        raw   = open(path, "rb").read()
        flags = struct.unpack_from(">H", raw, 6)[0]
        assert flags & FLAG_QUANTIZE, "FLAG_QUANTIZE ausente em arquivo vault"


# ══════════════════════════════════════════════════════════════════════════════
# §3 CODEC_ID E QUANT
# ══════════════════════════════════════════════════════════════════════════════

class TestCodecIdEQuant:

    def test_codec_id_byte_8_lzma2(self, spec_file):
        _, _, _, raw = spec_file
        codec_id = raw[8]
        assert codec_id == CODEC_LZMA2 == 0x02, f"CODEC_ID={codec_id:#04x}"

    def test_codec_id_byte_8_zstd(self, tmp):
        df = pd.DataFrame({"id": range(100), "v": range(100)})
        path = os.path.join(tmp, "zstd.permafrost")
        pf.freeze(df, path, codec=CODEC_ZSTD)
        raw = open(path, "rb").read()
        assert raw[8] == CODEC_ZSTD == 0x01

    def test_quant_byte_9_zero_lossless(self, spec_file):
        _, _, _, raw = spec_file
        assert raw[9] == QUANT_NONE == 0x00

    @pytest.mark.parametrize("quant,expected", [
        (QUANT_NONE,   0x00),
        (QUANT_HIGH,   0x01),
        (QUANT_MEDIUM, 0x02),
        (QUANT_LOW,    0x03),
    ])
    def test_quant_levels_no_byte_9(self, tmp, quant, expected):
        df = pd.DataFrame({"id": range(100), "v": np.random.rand(100)})
        path = os.path.join(tmp, f"q{expected}.permafrost")
        pf.freeze(df, path, quant=quant)
        raw = open(path, "rb").read()
        assert raw[9] == expected, f"QUANT={raw[9]:#04x} esperado {expected:#04x}"

    def test_codec_constants_valores_corretos(self):
        assert CODEC_ZSTD  == 0x01
        assert CODEC_LZMA2 == 0x02
        assert CODEC_ZPAQ  == 0x03
        assert QUANT_NONE   == 0x00
        assert QUANT_HIGH   == 0x01
        assert QUANT_MEDIUM == 0x02
        assert QUANT_LOW    == 0x03


# ══════════════════════════════════════════════════════════════════════════════
# §4 SHA-256 DO HEADER
# ══════════════════════════════════════════════════════════════════════════════

class TestSHA256Header:

    def test_header_sha256_esta_presente(self, spec_file):
        _, _, _, raw = spec_file
        h = _read_header(raw)
        assert h["hdr_sha_stored"] is not None
        assert len(h["hdr_sha_stored"]) == 32

    def test_header_sha256_valido(self, spec_file):
        _, _, _, raw = spec_file
        h = _read_header(raw)
        computed = _sha256(raw[:h["hdr_end"]])
        assert computed == h["hdr_sha_stored"], "SHA-256 do header inválido"

    def test_header_sha256_detecta_modificacao(self, spec_file, tmp):
        path, _, _, raw = spec_file
        corrupt = os.path.join(tmp, "c.permafrost")
        with open(corrupt, "wb") as f: f.write(raw)
        # Modificar 1 byte no meio do header
        with open(corrupt, "r+b") as f:
            f.seek(200); f.write(b"\xFF")
        with pytest.raises(ValueError, match="SHA"):
            pf.thaw(corrupt, verify=True)

    def test_sha256_de_cada_chunk_presente(self, spec_file):
        _, _, _, raw = spec_file
        h   = _read_header(raw)
        idx = _read_sparse_index(raw)
        for entry in idx:
            blob = raw[entry["byte_offset"]: entry["byte_offset"]+entry["byte_len"]]
            computed = _sha256(blob).hex()
            assert computed == entry["sha256"], \
                f"Chunk {entry['chunk_id']}: SHA-256 não confere"

    def test_sha256_do_index_presente(self, spec_file):
        _, _, _, raw = spec_file
        # EOF: [...][index_json][index_len:4B][index_sha:32B][SMRP:4B]
        # index_len está nos bytes [-4-32-4 : -4-32]
        # index_sha está nos bytes [-4-32   : -4]
        index_len  = struct.unpack(">I", raw[-4-32-4: -4-32])[0]
        index_sha  = raw[-4-32: -4]
        index_json = raw[-4-32-4-index_len: -4-32-4]
        computed   = _sha256(index_json)
        assert computed == index_sha, "SHA-256 do sparse index inválido"


# ══════════════════════════════════════════════════════════════════════════════
# §5 SPARSE INDEX ESTRUTURA
# ══════════════════════════════════════════════════════════════════════════════

class TestSparseIndexEstrutura:

    def test_index_e_json_valido(self, spec_file):
        _, _, _, raw = spec_file
        idx = _read_sparse_index(raw)
        assert isinstance(idx, list)
        assert len(idx) > 0

    def test_cada_entry_tem_campos_obrigatorios(self, spec_file):
        _, _, _, raw = spec_file
        idx = _read_sparse_index(raw)
        required = {"chunk_id","row_start","row_end","byte_offset","byte_len","sha256","part_key","part_col"}
        for entry in idx:
            missing = required - set(entry.keys())
            assert not missing, f"Campos ausentes na entry {entry.get('chunk_id')}: {missing}"

    def test_chunk_ids_sequenciais(self, spec_file):
        _, _, _, raw = spec_file
        idx = _read_sparse_index(raw)
        ids = [e["chunk_id"] for e in idx]
        assert ids == list(range(len(ids))), f"Chunk IDs não sequenciais: {ids}"

    def test_row_ranges_cobrem_todos_os_dados(self, spec_file):
        path, df, N, raw = spec_file
        idx = _read_sparse_index(raw)
        all_rows = set()
        for entry in idx:
            all_rows.update(range(entry["row_start"], entry["row_end"]+1))
        assert len(all_rows) == N, f"Sparse index cobre {len(all_rows)} != {N}"

    def test_byte_offsets_nao_se_sobrepoem(self, spec_file):
        _, _, _, raw = spec_file
        idx = sorted(_read_sparse_index(raw), key=lambda e: e["byte_offset"])
        for i in range(len(idx)-1):
            end_i   = idx[i]["byte_offset"] + idx[i]["byte_len"] + 32  # +sha
            start_i1 = idx[i+1]["byte_offset"]
            assert end_i <= start_i1, \
                f"Chunks {idx[i]['chunk_id']} e {idx[i+1]['chunk_id']} se sobrepõem"

    def test_byte_offsets_dentro_do_arquivo(self, spec_file):
        path, _, _, raw = spec_file
        file_size = os.path.getsize(path)
        idx = _read_sparse_index(raw)
        for entry in idx:
            end = entry["byte_offset"] + entry["byte_len"]
            assert end <= file_size, \
                f"Chunk {entry['chunk_id']} byte_offset+len ({end}) > file_size ({file_size})"


# ══════════════════════════════════════════════════════════════════════════════
# §6 HEADER CAMPOS
# ══════════════════════════════════════════════════════════════════════════════

class TestHeaderCampos:

    def test_orig_rows_correto(self, spec_file):
        _, df, N, raw = spec_file
        h = _read_header(raw)
        assert h["orig_rows"] == N

    def test_n_chunks_correto(self, spec_file):
        _, df, N, raw = spec_file
        h   = _read_header(raw)
        idx = _read_sparse_index(raw)
        assert h["n_chunks"] == len(idx)

    def test_schema_arrow_embutido(self, spec_file):
        _, df, _, raw = spec_file
        h = _read_header(raw)
        # O schema Arrow deve conter os nomes das colunas
        assert "manifests" in h
        assert set(h["manifests"].keys()) == set(df.columns)

    def test_predictor_manifest_por_coluna(self, spec_file):
        _, df, _, raw = spec_file
        h = _read_header(raw)
        for col in df.columns:
            assert col in h["manifests"], f"Coluna '{col}' ausente no manifesto"
            m = h["manifests"][col]
            assert "predictor" in m, f"Coluna '{col}' sem predictor no manifesto"

    def test_freeze_timestamp_e_int64(self, spec_file):
        _, _, _, raw = spec_file
        h = _read_header(raw)
        assert isinstance(h.get("freeze_ts", 0), int)
        assert h.get("freeze_ts", 0) > 1_000_000_000  # depois de 2001

    def test_payload_start_apos_header(self, spec_file):
        _, _, _, raw = spec_file
        h = _read_header(raw)
        # O payload (chunks) começa depois do header+sha
        payload_start = h["payload_start"]
        assert payload_start > 0
        assert payload_start < len(raw)
        # Os dados reais (não-zeros) começam no payload
        assert raw[payload_start: payload_start+4] != b"\x00\x00\x00\x00", \
            "Payload começa com zeros — possível erro de offset"


# ══════════════════════════════════════════════════════════════════════════════
# §7 COMPATIBILIDADE ENTRE VERSÕES
# ══════════════════════════════════════════════════════════════════════════════

class TestCompatibilidade:

    def test_arquivo_com_codec_zstd_legivel(self, tmp):
        np.random.seed(1); N = 1_000
        df = pd.DataFrame({"id": range(N), "v": np.random.rand(N)})
        path = os.path.join(tmp, "zstd_compat.permafrost")
        pf.freeze(df, path, codec=CODEC_ZSTD)
        # Deve ser legível sem especificar codec
        df_b = pf.thaw(path, verify=True)
        assert len(df_b) == N

    def test_arquivo_com_codec_lzma2_legivel(self, tmp):
        np.random.seed(2); N = 1_000
        df = pd.DataFrame({"id": range(N), "v": np.random.rand(N)})
        path = os.path.join(tmp, "lzma_compat.permafrost")
        pf.freeze(df, path, codec=CODEC_LZMA2)
        df_b = pf.thaw(path, verify=True)
        assert len(df_b) == N

    def test_audit_nao_modifica_conteudo(self, spec_file, tmp):
        path, _, N, raw_before = spec_file
        pf.audit(path)
        raw_after = open(path, "rb").read()
        assert raw_before == raw_after, "audit() modificou o arquivo"

    def test_multiplos_thaw_arquivo_identico(self, spec_file):
        path, _, N, _ = spec_file
        results = []
        for _ in range(5):
            df_b = pf.thaw(path, verify=True)
            results.append(float(df_b["total"].sum()))
        assert len(set(round(r, 2) for r in results)) == 1, \
            f"thaw múltiplo inconsistente: {results}"

    def test_arquivo_sem_partition_by_legivel(self, tmp):
        """Arquivo sem partition_by (sem sparse index de partição)."""
        df = pd.DataFrame({"id": range(500), "v": np.random.rand(500)})
        path = os.path.join(tmp, "nopart.permafrost")
        pf.freeze(df, path)  # sem partition_by
        info = pf.audit(path)
        assert info["partition_col"] in (None, "__rows__", "")
        df_b = pf.thaw(path, verify=True)
        assert len(df_b) == 500

    def test_arquivo_com_1_chunk_legivel(self, tmp):
        """Arquivo com apenas 1 chunk (chunk_rows > N)."""
        N = 200
        df = pd.DataFrame({"id": range(N), "v": range(N)})
        path = os.path.join(tmp, "onechunk.permafrost")
        m = pf.freeze(df, path, chunk_rows=10_000)
        assert m["n_chunks"] == 1
        df_b = pf.thaw(path, verify=True)
        assert len(df_b) == N


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
