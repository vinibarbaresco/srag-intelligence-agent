"""Testes da deteccao de mudanca de esquema entre safras do DATASUS.

A comparacao e sempre entre safras do **mesmo ano**: o SIVEP-Gripe republica
`INFLUD25` varias vezes, e comparar a carga inteira contra a carga inteira faria
de toda republicacao um falso alarme. Os testes abaixo exercitam as seis classes
de mudanca, a severidade de cada uma, o caminho da primeira carga (que estabelece
a linha de base sem alarme) e o aceite explicito.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from src.config import Settings
from src.data.drift import (
    FINDING_KINDS,
    KIND_DATE,
    KIND_DTYPE_CHANGES,
    KIND_EMPTY,
    KIND_INTEGER,
    KIND_MISSING_COLUMNS,
    KIND_MISSING_RATE_CHANGES,
    KIND_NEW_CATEGORIES,
    KIND_NEW_COLUMNS,
    KIND_RECORD_COUNT_CHANGES,
    KIND_TEXT,
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    DriftReport,
    DriftThresholds,
    SchemaDriftError,
    SchemaObservation,
    SchemaObserver,
    compare,
    detect_drift,
    load_baseline,
    save_baseline,
)
from src.data.preprocess import preprocess
from src.data.schema import ALLOWED_COLUMNS

#: Colunas do arquivo bruto usadas pelos cenarios sinteticos: a allowlist mais
#: duas colunas que o pipeline nao le. Elas existem para separar a severidade de
#: "sumiu uma coluna que alimenta o calculo" da de "sumiu uma coluna que nunca
#: foi lida" -- e porque o arquivo real traz dezenas delas (`SURTO_SG`, `REINF`,
#: `VG_OMS`), que nao podem virar alarme.
_EXTRA_COLUMNS: tuple[str, ...] = ("SURTO_SG", "CO_DETEC")

_FILE_COLUMNS: tuple[str, ...] = ALLOWED_COLUMNS + _EXTRA_COLUMNS


def _observation(year: int = 2025, **override) -> SchemaObservation:
    """Retrato minimo de uma safra, com os campos sobrescritos no cenario."""
    base = {
        "filename": f"INFLUD{year % 100}-01-01-2026.csv",
        "raw_columns": _FILE_COLUMNS,
        "dtypes": {"DT_SIN_PRI": KIND_DATE, "UTI": KIND_INTEGER, "CS_SEXO": KIND_TEXT},
        "missing_rate": {"DT_DIGITA": 0.0, "UTI": 10.0},
        "categories": {"UTI": ["1", "2", "9"]},
        "record_count": 100_000,
    }
    base.update(override)
    return SchemaObservation(year=year, **base)


def _baseline_of(observation: SchemaObservation) -> dict:
    """Linha de base equivalente a uma observacao."""
    return observation.to_dict()


class TestObservacaoDaSafra:
    """O retrato e tirado do arquivo **bruto**, antes de qualquer limpeza."""

    def test_tipo_e_completude_sao_medidos_por_bloco(self):
        observer = SchemaObserver(year=2025, filename="INFLUD25.csv")
        observer.observe_header(["DT_SIN_PRI", "UTI", "CS_SEXO", "SURTO_SG"])
        observer.observe_chunk(
            pd.DataFrame(
                {"DT_SIN_PRI": ["2025-01-05", ""], "UTI": ["1", "2"], "CS_SEXO": ["F", "M"]},
                dtype="string",
            )
        )
        observer.observe_chunk(
            pd.DataFrame(
                {
                    "DT_SIN_PRI": ["2025-02-05", "2025-03-05"],
                    "UTI": ["9", "1"],
                    "CS_SEXO": ["", ""],
                },
                dtype="string",
            )
        )
        result = observer.result()

        assert result.record_count == 4
        assert result.dtypes["DT_SIN_PRI"] == KIND_DATE
        assert result.dtypes["UTI"] == KIND_INTEGER
        assert result.dtypes["CS_SEXO"] == KIND_TEXT
        assert result.missing_rate["DT_SIN_PRI"] == 25.0
        assert result.missing_rate["UTI"] == 0.0
        assert sorted(result.categories["UTI"]) == ["1", "2", "9"]
        # O cabecalho inteiro entra no retrato, inclusive o que nao e lido.
        assert "SURTO_SG" in result.raw_columns

    def test_coluna_toda_vazia_e_estado_nao_tipo(self):
        observer = SchemaObserver(year=2025, filename="INFLUD25.csv")
        observer.observe_chunk(pd.DataFrame({"DT_ENTUTI": ["", ""]}, dtype="string"))
        assert observer.result().dtypes["DT_ENTUTI"] == KIND_EMPTY

    def test_lixo_pontual_nao_reclassifica_a_coluna(self):
        # 1 valor ilegivel em 200 fica abaixo do limiar de concordancia: a
        # coluna continua sendo data, e o registro ruim e assunto da limpeza,
        # que ja o marca como `data_ilegivel`.
        valores = ["2025-01-05"] * 199 + ["xx"]
        observer = SchemaObserver(year=2025, filename="INFLUD25.csv")
        observer.observe_chunk(pd.DataFrame({"DT_SIN_PRI": valores}, dtype="string"))
        assert observer.result().dtypes["DT_SIN_PRI"] == KIND_DATE


class TestPrimeiraCarga:
    def test_primeira_carga_estabelece_baseline_sem_alarme(self, tmp_path):
        report = detect_drift([_observation()], tmp_path / "schema_baseline.json")

        assert report.findings == []
        assert report.baselines_established == [2025]
        payload = report.to_dict()
        assert payload["baseline_estabelecida_para"] == [2025]
        assert "linha de base estabelecida" in payload["resumo"]
        assert all(payload[kind] == [] for kind in ("new_columns", "missing_columns"))

    def test_sem_baseline_nenhuma_coluna_desconhecida_alarma(self):
        # As colunas que o dicionario de 19/09/2022 nao descreve ja existem no
        # arquivo. Na primeira carga elas entram na linha de base em silencio.
        assert compare(None, _observation()) == []


class TestColunas:
    def test_coluna_nova_gera_warning(self):
        anterior = _baseline_of(_observation())
        atual = _observation(raw_columns=_FILE_COLUMNS + ("VG_LIN",))

        findings = compare(anterior, atual)

        assert len(findings) == 1
        assert findings[0].kind == KIND_NEW_COLUMNS
        assert findings[0].severity == SEVERITY_WARNING
        assert findings[0].column == "VG_LIN"

    def test_coluna_da_allowlist_ausente_gera_error(self):
        anterior = _baseline_of(_observation())
        restantes = tuple(column for column in _FILE_COLUMNS if column != "UTI")
        atual = _observation(raw_columns=restantes)

        findings = compare(anterior, atual)

        assert [item.kind for item in findings] == [KIND_MISSING_COLUMNS]
        assert findings[0].severity == SEVERITY_ERROR
        assert findings[0].column == "UTI"
        assert "ALLOWED_COLUMNS" in findings[0].message

    def test_coluna_nao_lida_ausente_gera_apenas_warning(self):
        anterior = _baseline_of(_observation())
        restantes = tuple(column for column in _FILE_COLUMNS if column != "SURTO_SG")

        findings = compare(anterior, _observation(raw_columns=restantes))

        assert findings[0].severity == SEVERITY_WARNING


class TestTipos:
    def test_mudanca_de_tipo_detectada(self):
        anterior = _baseline_of(_observation())
        atual = _observation(
            dtypes={"DT_SIN_PRI": KIND_TEXT, "UTI": KIND_INTEGER, "CS_SEXO": KIND_TEXT}
        )

        findings = compare(anterior, atual)

        assert [item.kind for item in findings] == [KIND_DTYPE_CHANGES]
        assert findings[0].severity == SEVERITY_ERROR
        assert (findings[0].previous, findings[0].current) == (KIND_DATE, KIND_TEXT)

    @pytest.mark.parametrize(
        ("antes", "depois"),
        [(KIND_EMPTY, KIND_INTEGER), (KIND_INTEGER, KIND_EMPTY)],
    )
    def test_coluna_que_comeca_ou_para_de_ser_preenchida_nao_e_mudanca_de_tipo(self, antes, depois):
        anterior = _baseline_of(_observation(dtypes={"DT_ENTUTI": antes}))
        findings = compare(anterior, _observation(dtypes={"DT_ENTUTI": depois}))
        assert [item for item in findings if item.kind == KIND_DTYPE_CHANGES] == []


class TestCategorias:
    def test_categoria_nova_gera_warning(self):
        anterior = _baseline_of(_observation())
        atual = _observation(categories={"UTI": ["1", "2", "9", "7"]})

        findings = compare(anterior, atual)

        assert [item.kind for item in findings] == [KIND_NEW_CATEGORIES]
        assert findings[0].severity == SEVERITY_WARNING
        assert findings[0].current == "7"

    def test_codigo_do_dicionario_oficial_nunca_e_categoria_nova(self):
        # `3` esta em CODE_LABELS["SUPORT_VEN"] mas nao na safra anterior: e
        # dominio declarado, nao novidade da fonte.
        anterior = _baseline_of(_observation(categories={"SUPORT_VEN": ["1", "2"]}))
        atual = _observation(categories={"SUPORT_VEN": ["1", "2", "3"]})
        assert compare(anterior, atual) == []

    def test_codigo_ja_visto_na_safra_anterior_nao_realarma(self):
        # `9` nao existe no dominio de CLASSI_FIN, mas se ja apareceu na carga
        # anterior ele ja foi reportado uma vez; repetir o alarme a cada carga
        # treinaria o operador a ignorar a secao inteira.
        anterior = _baseline_of(_observation(categories={"CLASSI_FIN": ["1", "9"]}))
        atual = _observation(categories={"CLASSI_FIN": ["1", "9"]})
        assert compare(anterior, atual) == []


class TestCompletudeEContagem:
    def test_variacao_de_missing_acima_do_limiar_gera_warning(self):
        anterior = _baseline_of(_observation(missing_rate={"DT_DIGITA": 0.0}))
        atual = _observation(missing_rate={"DT_DIGITA": 12.5})

        findings = compare(anterior, atual)

        assert [item.kind for item in findings] == [KIND_MISSING_RATE_CHANGES]
        assert findings[0].severity == SEVERITY_WARNING
        assert (findings[0].previous, findings[0].current) == (0.0, 12.5)

    def test_variacao_de_missing_abaixo_do_limiar_nao_alarma(self):
        anterior = _baseline_of(_observation(missing_rate={"DT_DIGITA": 10.0}))
        assert compare(anterior, _observation(missing_rate={"DT_DIGITA": 13.0})) == []

    def test_queda_de_registros_gera_error(self):
        anterior = _baseline_of(_observation(record_count=336_391))
        atual = _observation(record_count=165_397)

        findings = compare(anterior, atual)

        assert [item.kind for item in findings] == [KIND_RECORD_COUNT_CHANGES]
        assert findings[0].severity == SEVERITY_ERROR
        assert findings[0].current == 165_397

    def test_crescimento_de_registros_gera_warning(self):
        # O caso real medido: 165.397 -> 336.391 na mesma INFLUD25 (+103%).
        anterior = _baseline_of(_observation(record_count=165_397))
        atual = _observation(record_count=336_391)

        findings = compare(anterior, atual)

        assert [item.kind for item in findings] == [KIND_RECORD_COUNT_CHANGES]
        assert findings[0].severity == SEVERITY_WARNING

    def test_limiares_sao_configuraveis(self):
        anterior = _baseline_of(_observation(record_count=100))
        atual = _observation(record_count=130)
        folgado = DriftThresholds(record_growth_pct=50.0)
        apertado = DriftThresholds(record_growth_pct=10.0)

        assert compare(anterior, atual, folgado) == []
        assert len(compare(anterior, atual, apertado)) == 1


class TestRelatorio:
    def test_nada_e_ignorado_silenciosamente(self):
        anterior = _baseline_of(_observation())
        atual = _observation(
            raw_columns=_FILE_COLUMNS + ("VG_LIN",),
            dtypes={"DT_SIN_PRI": KIND_TEXT},
            categories={"UTI": ["1", "2", "9", "7"]},
            missing_rate={"DT_DIGITA": 40.0, "UTI": 10.0},
            record_count=10_000,
        )
        report = DriftReport(findings=compare(anterior, atual))
        payload = report.to_dict()

        # As seis chaves exigidas existem sempre, mesmo vazias.
        for kind in (
            "new_columns",
            "missing_columns",
            "dtype_changes",
            "new_categories",
            "missing_rate_changes",
            "record_count_changes",
        ):
            assert kind in payload

        # Todo achado aparece sob a sua classe E na lista da sua severidade.
        por_classe = [item for kind in FINDING_KINDS for item in payload[kind]]
        assert len(por_classe) == len(report.findings)
        assert len(payload["errors"]) + len(payload["warnings"]) == len(report.findings)
        assert all(item.to_dict() in payload[item.kind] for item in report.findings)
        assert all(
            item.to_dict() in payload["errors"] + payload["warnings"] for item in report.findings
        )
        # Os cinco tipos de mudanca do cenario estao todos representados.
        assert {item.kind for item in report.findings} == {
            KIND_NEW_COLUMNS,
            KIND_DTYPE_CHANGES,
            KIND_NEW_CATEGORIES,
            KIND_MISSING_RATE_CHANGES,
            KIND_RECORD_COUNT_CHANGES,
        }

    def test_limiares_acompanham_os_achados(self):
        payload = DriftReport().to_dict()
        assert payload["limiares"]["queda_de_registros_pct"] == 20.0


class TestLinhaDeBasePersistida:
    def test_baseline_acumula_por_ano(self, tmp_path):
        path = tmp_path / "schema_baseline.json"
        save_baseline(path, [_observation(2019), _observation(2025)])
        save_baseline(path, [_observation(2025, record_count=1)])

        baseline = load_baseline(path)

        assert sorted(baseline) == ["2019", "2025"]
        assert baseline["2019"]["record_count"] == 100_000
        assert baseline["2025"]["record_count"] == 1

    def test_baseline_ilegivel_volta_ao_estado_de_primeira_carga(self, tmp_path):
        path = tmp_path / "schema_baseline.json"
        path.write_text("{ nao e json", encoding="utf-8")
        assert load_baseline(path) == {}

    def test_accept_drift_registra_aceite_na_linha_de_base(self, tmp_path):
        path = tmp_path / "schema_baseline.json"
        save_baseline(path, [_observation(2025, record_count=336_391)])
        atual = _observation(2025, record_count=1_000)
        report = detect_drift([atual], path, accept=True)
        save_baseline(path, [atual], accepted=report)

        entry = load_baseline(path)["2025"]

        assert entry["accepted_at"]
        assert entry["accepted_findings"][0]["tipo"] == KIND_RECORD_COUNT_CHANGES
        assert entry["accepted_findings"][0]["severidade"] == SEVERITY_ERROR


# =============================================================================
# Carga ponta a ponta
# =============================================================================


class _Carga:
    """Ambiente de carga isolado: manifesto, CSV sintetico e `preprocess`."""

    def __init__(self, tmp_path, monkeypatch) -> None:
        self.tmp_path = tmp_path
        self.settings = Settings(data_root=tmp_path, reporting_lag_days=21)
        self.settings.ensure_directories()
        monkeypatch.setattr("src.data.preprocess.get_settings", lambda: self.settings)

    def run(self, year: int, rows: list[dict], *, columns=_FILE_COLUMNS, accept: bool = False):
        """Grava um CSV do ano, registra no manifesto e roda a carga."""
        header = ";".join(f'"{column}"' for column in columns)
        linhas = [";".join(f'"{row.get(column, "")}"' for column in columns) for row in rows]
        csv_path = self.tmp_path / f"INFLUD{year % 100}-{len(rows)}.csv"
        csv_path.write_text("\n".join([header, *linhas]) + "\n", encoding="utf-8")

        manifest = {}
        if self.settings.raw_manifest_path.exists():
            manifest = json.loads(self.settings.raw_manifest_path.read_text(encoding="utf-8"))
        manifest[str(year)] = {
            "year": year,
            "filename": csv_path.name,
            "path": str(csv_path),
            "url": None,
            "origin": "arquivo local de teste",
            "size_bytes": 1,
            "sha256": "0" * 64,
            "downloaded_at": "2026-01-01T00:00:00+00:00",
        }
        self.settings.raw_manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return preprocess([year], accept_drift=accept)

    def drift(self) -> dict:
        """Achados de esquema da carga mais recente."""
        return json.loads(self.settings.schema_drift_path.read_text(encoding="utf-8"))


def _row(**override) -> dict:
    """Registro bruto valido, no formato publicado pela fonte."""
    row = {
        "DT_SIN_PRI": "2026-05-08",
        "DT_DIGITA": "2026-05-20",
        "SG_UF_NOT": "SP",
        "SG_UF": "SP",
        "NU_IDADE_N": "45",
        "TP_IDADE": "3",
        "SEM_PRI": "19",
        "EVOLUCAO": "1",
        "HOSPITAL": "1",
        "UTI": "2",
        "SUPORT_VEN": "3",
        "CLASSI_FIN": "5",
        "CRITERIO": "1",
        "VACINA_COV": "1",
        "VACINA": "2",
        "CS_SEXO": "F",
    }
    row.update(override)
    return row


class TestCargaPontaAPonta:
    def test_primeira_carga_estabelece_baseline_sem_alarme(self, tmp_path, monkeypatch):
        carga = _Carga(tmp_path, monkeypatch)
        carga.run(2026, [_row(), _row()])

        drift = carga.drift()
        assert drift["errors"] == [] and drift["warnings"] == []
        assert drift["baseline_estabelecida_para"] == [2026]
        assert carga.settings.schema_baseline_path.exists()

        # A secao entra no relatorio de qualidade com as seis chaves.
        report = json.loads(carga.settings.quality_report_path.read_text(encoding="utf-8"))
        assert set(report["schema"]) >= {
            "new_columns",
            "missing_columns",
            "dtype_changes",
            "new_categories",
            "missing_rate_changes",
            "record_count_changes",
            "errors",
            "warnings",
        }

    def test_coluna_da_allowlist_ausente_interrompe_a_carga(self, tmp_path, monkeypatch):
        carga = _Carga(tmp_path, monkeypatch)
        carga.run(2026, [_row()])
        sem_uti = tuple(column for column in _FILE_COLUMNS if column != "UTI")

        with pytest.raises(SchemaDriftError, match="UTI"):
            carga.run(2026, [_row()], columns=sem_uti)

        # O achado foi persistido mesmo com a carga interrompida, e a linha de
        # base ficou intacta: a proxima tentativa detecta a mesma mudanca.
        assert carga.drift()["errors"][0]["coluna"] == "UTI"
        assert "UTI" in load_baseline(carga.settings.schema_baseline_path)["2026"]["raw_columns"]

    def test_accept_drift_registra_aceite_e_nao_interrompe(self, tmp_path, monkeypatch):
        carga = _Carga(tmp_path, monkeypatch)
        carga.run(2026, [_row()])
        sem_uti = tuple(column for column in _FILE_COLUMNS if column != "UTI")

        parquet = carga.run(2026, [_row()], columns=sem_uti, accept=True)

        assert parquet.exists()
        drift = carga.drift()
        assert drift["aceite_explicito"] is True
        assert drift["errors"][0]["coluna"] == "UTI"
        entry = load_baseline(carga.settings.schema_baseline_path)["2026"]
        assert "UTI" not in entry["raw_columns"]
        assert entry["accepted_findings"][0]["coluna"] == "UTI"

    def test_coluna_nova_gera_warning_e_segue(self, tmp_path, monkeypatch):
        carga = _Carga(tmp_path, monkeypatch)
        carga.run(2026, [_row()])

        parquet = carga.run(2026, [_row()], columns=_FILE_COLUMNS + ("VG_LIN",))

        assert parquet.exists()
        drift = carga.drift()
        assert drift["errors"] == []
        assert [item["coluna"] for item in drift["new_columns"]] == ["VG_LIN"]

    def test_baseline_e_por_ano_entao_safra_nova_do_mesmo_ano_compara_certo(
        self, tmp_path, monkeypatch
    ):
        carga = _Carga(tmp_path, monkeypatch)
        # 2019: 10 registros, 40% deles sem DT_DIGITA (o padrao medido no
        # INFLUD19 real). 2025: 10 registros, DT_DIGITA sempre preenchida.
        carga.run(
            2019,
            [_row(DT_SIN_PRI="2019-05-08", DT_DIGITA="") for _ in range(4)]
            + [_row(DT_SIN_PRI="2019-05-08") for _ in range(6)],
        )
        carga.run(2025, [_row(DT_SIN_PRI="2025-05-08") for _ in range(10)])

        # Safra nova de 2025: 25 registros (+150%), completude inalterada.
        carga.run(2025, [_row(DT_SIN_PRI="2025-05-08") for _ in range(25)])
        drift = carga.drift()

        contagem = drift["record_count_changes"]
        assert len(contagem) == 1
        assert contagem[0]["ano"] == 2025
        # Comparou contra os 10 registros de 2025, nao contra os 20 da base toda.
        assert contagem[0]["anterior"] == 10
        assert contagem[0]["severidade"] == SEVERITY_WARNING
        # A diferenca de completude de DT_DIGITA entre 2019 e 2025 e um fato dos
        # dois anos; comparar por ano impede que ela vire alarme.
        assert drift["missing_rate_changes"] == []
