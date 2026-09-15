"""Testes da deteccao de encoding dos arquivos brutos (D-01).

`src/data/encoding.py` existia sem nenhum teste. A regra que ele implementa e
justamente a que nao falha sozinha quando esta errada: `latin-1` decodifica
qualquer byte, entao um arquivo UTF-8 lido como `latin-1` produz mojibake
silencioso, e um arquivo `latin-1` lido como UTF-8 quebra a carga inteira. Os
casos abaixo fixam as duas pontas e as bordas que a leitura incremental
introduz -- bloco, fim de arquivo e arquivo vazio.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.data.encoding import (
    _READ_BLOCK,
    FALLBACK_ENCODING,
    PREFERRED_ENCODING,
    detect_encoding,
)


def _write(path: Path, payload: bytes) -> Path:
    """Grava bytes crus, sem passar por nenhuma camada de texto."""
    path.write_bytes(payload)
    return path


class TestDeteccaoDeEncoding:
    def test_arquivo_utf8_puro_e_detectado_como_utf8(self, tmp_path):
        """Cabecalho e valores validos em UTF-8, como nas publicacoes recentes."""
        path = _write(tmp_path / "utf8.csv", b"SG_UF;OBS\nSP;notificacao\n")
        assert detect_encoding(path) == PREFERRED_ENCODING

    def test_arquivo_com_acento_utf8_multibyte_e_utf8(self, tmp_path):
        path = _write(tmp_path / "acentos.csv", "municipio;São Paulo;Ceará\n".encode())
        assert detect_encoding(path) == PREFERRED_ENCODING

    def test_arquivo_so_ascii_e_utf8(self, tmp_path):
        """ASCII e um subconjunto de UTF-8: nao ha motivo para cair no fallback.

        E o caso de toda a allowlist atual (datas, codigos, siglas de UF), onde
        a escolha errada hoje nao teria consequencia visivel -- exatamente por
        isso ela precisa estar travada por teste.
        """
        path = _write(tmp_path / "ascii.csv", b"DT_SIN_PRI;UTI\n2026-05-08;1\n")
        assert detect_encoding(path) == PREFERRED_ENCODING

    def test_byte_invalido_em_utf8_cai_para_latin1(self, tmp_path):
        """`0xFF` nao inicia nenhuma sequencia UTF-8 valida; em latin-1 e 'y' com trema."""
        path = _write(tmp_path / "latin1.csv", b"municipio;S\xe3o Paulo\n\xff\n")
        assert detect_encoding(path) == FALLBACK_ENCODING

    def test_arquivo_latin1_de_safra_antiga_cai_para_latin1(self, tmp_path):
        """Safras exportadas de DBF: `\\xe7` e 'c-cedilha' em latin-1 e invalido em UTF-8."""
        path = _write(tmp_path / "dbf.csv", b"OBS\nnotifica\xe7\xe3o hospitalar\n")
        assert detect_encoding(path) == FALLBACK_ENCODING

    def test_sequencia_utf8_truncada_no_fim_do_arquivo_cai_para_latin1(self, tmp_path):
        """Arquivo cortado no meio de um caractere: o byte inicial sozinho e invalido.

        Este e o caso que so aparece no `decode(b"", final=True)`: ate o ultimo
        bloco o decodificador incremental nao tem como saber se a continuacao
        viria depois. Sem essa chamada final, um download truncado seria
        declarado UTF-8 e quebraria na leitura do CSV, longe da causa.
        """
        path = _write(tmp_path / "truncado.csv", b"cabecalho\nSao Paul\xc3")
        assert detect_encoding(path) == FALLBACK_ENCODING

    def test_arquivo_vazio_e_utf8(self, tmp_path):
        """Nenhum byte invalido: nao ha evidencia para recorrer ao fallback."""
        path = _write(tmp_path / "vazio.csv", b"")
        assert detect_encoding(path) == PREFERRED_ENCODING

    def test_caractere_multibyte_dividido_entre_blocos_continua_utf8(self, tmp_path):
        """A leitura e incremental de proposito: o bloco nao pode cortar a decisao.

        Um arquivo de 155 MB e lido em blocos de 4 MiB. Se cada bloco fosse
        decodificado isoladamente, todo caractere multibyte que caisse na
        emenda entre dois blocos apareceria como byte invalido -- e o pipeline
        declararia `latin-1` para um arquivo UTF-8 perfeito, com probabilidade
        proporcional ao tamanho do arquivo.
        """
        acento = "ç".encode()  # dois bytes
        recheio = b"a" * (_READ_BLOCK - 1)  # deixa o primeiro byte do acento no fim do bloco
        path = _write(tmp_path / "emenda.csv", recheio + acento + b"\n")

        assert len(recheio) + 1 == _READ_BLOCK  # a emenda esta mesmo no meio do caractere
        assert detect_encoding(path) == PREFERRED_ENCODING

    def test_byte_invalido_no_segundo_bloco_e_detectado(self, tmp_path):
        """A deteccao le o arquivo inteiro, sem amostrar o inicio (D-01)."""
        path = _write(tmp_path / "tarde.csv", b"a" * (_READ_BLOCK + 10) + b"\xff\n")
        assert detect_encoding(path) == FALLBACK_ENCODING


class TestContratoDaDeteccao:
    def test_fallback_nunca_e_o_padrao(self):
        """A constante de recurso decodifica qualquer byte -- por isso nunca e a preferida."""
        assert PREFERRED_ENCODING == "utf-8"
        assert FALLBACK_ENCODING == "latin-1"
        assert PREFERRED_ENCODING != FALLBACK_ENCODING

    def test_latin1_realmente_aceitaria_o_arquivo_utf8(self, tmp_path):
        """A razao de existir da regra: o erro oposto seria silencioso.

        Se a deteccao respondesse `latin-1` para um arquivo UTF-8, nada
        falharia -- o texto apenas viraria mojibake. Este teste demonstra a
        ausencia de sinal de erro, que e o que torna o teste acima necessario.
        """
        conteudo = "notificação".encode()
        path = _write(tmp_path / "mojibake.csv", conteudo)

        assert conteudo.decode(FALLBACK_ENCODING) != "notificação"  # sem excecao, so lixo
        assert detect_encoding(path) == PREFERRED_ENCODING

    def test_encoding_detectado_e_utilizavel_para_ler_o_arquivo(self, tmp_path):
        """O valor devolvido e um nome de codec aceito pelo `open`/`read_csv`."""
        path = _write(tmp_path / "leitura.csv", b"col\nvalor\n")
        with path.open(encoding=detect_encoding(path)) as handle:
            assert handle.read().splitlines() == ["col", "valor"]


class TestEncodingNaCarga:
    """O encoding detectado e publicado na proveniencia, nao apenas usado."""

    @pytest.mark.parametrize(
        ("payload", "esperado"),
        [(b"col\nvalor\n", "utf-8"), (b"col\nvalor\xe7\n", "latin-1")],
    )
    def test_deteccao_e_deterministica_entre_chamadas(self, tmp_path, payload, esperado):
        """Duas cargas do mesmo arquivo nao podem divergir de encoding."""
        path = _write(tmp_path / "estavel.csv", payload)
        assert detect_encoding(path) == detect_encoding(path) == esperado
