"""Contabilidade do que a ingestao fez com os dados.

Dois registros distintos, deliberadamente separados:

* :class:`QualityReport` -- agregado da carga: quantas linhas, quantas regras
  dispararam, quantos codigos de ausencia em cada campo;
* :class:`AdjustmentLog` -- por registro: quais valores o pipeline alterou.

A separacao importa. O agregado responde "quanto do dado esta ruim"; o log por
registro responde "quais registros eu toquei, e por que". Sem o segundo, um
campo anulado pela limpeza fica indistinguivel de um que ja veio vazio da fonte,
e a alteracao e, na pratica, silenciosa.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from src.config import DATASUS_SOURCE_LABEL
from src.data.schema import ADJUSTMENT_CODES, COHERENCE_FLAGS


class AdjustmentLog:
    """Acumula, por registro, os ajustes que a ingestao aplicou.

    Existe para que a afirmacao "nada e alterado silenciosamente" valha no nivel
    do registro, e nao apenas no agregado: depois da carga e possivel localizar
    exatamente quais linhas o pipeline tocou, e por que.
    """

    def __init__(self, index: pd.Index) -> None:
        self._index = index
        self._entries: list[tuple[str, pd.Series]] = []

    def add(self, code: str, mask: pd.Series) -> None:
        """Registra que `code` foi aplicado aos registros marcados em `mask`."""
        mask = mask.fillna(False).astype(bool)
        if mask.any():
            self._entries.append((code, mask))

    def to_series(self) -> pd.Series:
        """Codigos aplicados a cada registro, separados por virgula."""
        result = pd.Series("", index=self._index, dtype="string")
        for code, mask in self._entries:
            result[mask] = result[mask].str.cat([code] * int(mask.sum()), sep=",")
        return result.str.lstrip(",")

    def affected_rows(self) -> int:
        """Numero de registros que sofreram ao menos um ajuste."""
        if not self._entries:
            return 0
        combined = pd.Series(False, index=self._index)
        for _, mask in self._entries:
            combined |= mask
        return int(combined.sum())

    def counts(self) -> dict[str, int]:
        """Quantidade de registros afetados por codigo de ajuste."""
        return {code: int(mask.sum()) for code, mask in self._entries}


@dataclass
class QualityReport:
    """Agregado das regras de transformacao aplicadas na carga.

    Existe para cumprir a regra "nunca remova ou altere registros
    silenciosamente": cada decisao da limpeza vira um numero auditavel.
    """

    rows_read: int = 0
    rows_written: int = 0
    invalid_dates: Counter = field(default_factory=Counter)
    null_counts: Counter = field(default_factory=Counter)
    ignored_code_counts: Counter = field(default_factory=Counter)
    unknown_uf: int = 0
    coherence_flags: Counter = field(default_factory=Counter)
    age_out_of_range: int = 0
    adjusted_rows: int = 0
    adjustments: Counter = field(default_factory=Counter)
    out_of_domain_counts: Counter = field(default_factory=Counter)
    #: Registros lidos por coluna categorica. Denominador da identidade de
    #: completude -- sem ele, "1.846 ignorados" nao diz de quanto.
    code_totals: Counter = field(default_factory=Counter)
    #: Valores categoricos presentes no arquivo mas nao numericos, por coluna.
    #: Viram nulo e recebem o ajuste `codigo_ilegivel:<coluna>`.
    unreadable_codes: Counter = field(default_factory=Counter)
    #: Registros em que a semana derivada de DT_SIN_PRI diverge de SEM_PRI.
    #: Contagem pura: a derivada manda, e nenhuma das duas corrige a outra.
    epiweek_mismatch: int = 0
    #: Registros em que SEM_PRI esta presente e comparavel com a derivada.
    #: Denominador da divergencia acima.
    epiweek_compared: int = 0
    #: Idades fora do dominio da unidade declarada em TP_IDADE.
    age_unit_out_of_domain: int = 0
    #: Linhas identicas em todas as colunas lidas. Informativo: sem o
    #: identificador da notificacao (negado por minimizacao) nao ha como
    #: distinguir duplicata real de pacientes distintos com atributos iguais.
    identical_rows: int = 0
    #: Colunas sobre as quais a duplicidade foi medida. Publicada junto com a
    #: contagem porque o numero so significa alguma coisa com a lista ao lado.
    identical_rows_subset: list[str] = field(default_factory=list)

    def code_completeness(self) -> dict[str, dict[str, int | bool]]:
        """Completude por coluna categorica, com a identidade verificada.

        Um unico numero de "nulos" por coluna confunde tres situacoes
        diferentes: campo vazio na origem, codigo de ausencia declarado pelo
        dicionario (9-Ignorado) e codigo que o dicionario nao preve. As tres
        exigem tratamentos distintos nas metricas, e so a primeira e "dado que
        nao veio". A identidade
        `total = validos + ignorados + ausentes + fora_do_dominio` e publicada
        junto, para que a decomposicao seja verificavel em vez de acreditada.

        Returns:
            Mapa `coluna -> contagens`, com `identidade_confere` por coluna.
        """
        completeness: dict[str, dict[str, int | bool]] = {}
        for column, total in sorted(self.code_totals.items()):
            absent = self.null_counts[column]
            ignored = self.ignored_code_counts[column]
            outside = self.out_of_domain_counts[column]
            valid = total - absent - ignored - outside
            completeness[column] = {
                "total": total,
                "validos": valid,
                "ignorados": ignored,
                "ausentes": absent,
                "fora_do_dominio": outside,
                "identidade_confere": valid + ignored + absent + outside == total,
            }
        return completeness

    def absorb(self, log: AdjustmentLog) -> None:
        """Incorpora ao agregado os ajustes registrados em um bloco."""
        self.adjusted_rows += log.affected_rows()
        for code, count in log.counts().items():
            self.adjustments[code] += count

    def to_dict(
        self,
        *,
        source_files: list[str],
        run_id: str = "",
        pipeline: list[dict[str, str]] | None = None,
        schema_drift: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Serializa o relatorio de qualidade da carga.

        Args:
            source_files: arquivos brutos processados.
            run_id: identificador da execucao da ingestao.
            pipeline: regras aplicadas, na ordem em que rodaram.
            schema_drift: secao `schema` produzida por
                :meth:`src.data.drift.DriftReport.to_dict` -- o que mudou no
                arquivo bruto em relacao a safra anterior do mesmo ano. Entra
                aqui, e nao num relatorio proprio, porque "a fonte mudou" e um
                fato de qualidade da carga: sem ele, um indicador que zerou por
                causa de uma coluna renomeada seria investigado como defeito do
                calculo.
        """
        return {
            "run_id": run_id,
            "generated_at": datetime.now(tz=UTC).isoformat(),
            "source": DATASUS_SOURCE_LABEL,
            "source_files": source_files,
            "pipeline": pipeline or [],
            "schema": schema_drift or {},
            "rows_read": self.rows_read,
            "rows_written": self.rows_written,
            "rows_dropped": self.rows_read - self.rows_written,
            "rows_adjusted": self.adjusted_rows,
            "adjustments": {
                "por_codigo": dict(self.adjustments),
                "significado": ADJUSTMENT_CODES,
                "como_localizar": (
                    "SELECT * FROM srag_cases WHERE ajustes_aplicados <> '' -- "
                    "cada registro alterado carrega os codigos aplicados a ele"
                ),
            },
            "coherence_flags": {
                name: {
                    "registros": self.coherence_flags[name],
                    "percentual": (
                        round(self.coherence_flags[name] / self.rows_read * 100, 3)
                        if self.rows_read
                        else 0.0
                    ),
                    "significado": description,
                    "exclui_da_view_analitica": name == "flag_data_invalida",
                }
                for name, description in COHERENCE_FLAGS.items()
            },
            "rules": {
                "datas_nao_parseaveis_por_coluna": dict(self.invalid_dates),
                "valores_nulos_por_coluna": dict(self.null_counts),
                "codigo_9_ignorado_por_coluna": dict(self.ignored_code_counts),
                "codigo_fora_do_dominio_por_coluna": dict(self.out_of_domain_counts),
                "codigo_ilegivel_por_coluna": dict(self.unreadable_codes),
                "completude_por_coluna_categorica": self.code_completeness(),
                "linhas_identicas_nas_colunas_persistidas": self.identical_rows,
                "colunas_da_medicao_de_duplicidade": self.identical_rows_subset,
                "uf_fora_do_dominio": self.unknown_uf,
                "idade_fora_do_intervalo_plausivel": self.age_out_of_range,
                "idade_fora_do_dominio_da_unidade": self.age_unit_out_of_domain,
                "semana_epidemiologica": {
                    "registros_comparados_com_SEM_PRI": self.epiweek_compared,
                    "divergencias": self.epiweek_mismatch,
                    "percentual": (
                        round(self.epiweek_mismatch / self.epiweek_compared * 100, 3)
                        if self.epiweek_compared
                        else 0.0
                    ),
                },
            },
            "notes": [
                "Nenhum registro e excluido: inconsistencias sao marcadas em "
                "flags de coerencia e permanecem na base.",
                "As flags sao por dimensao. Apenas flag_data_invalida exclui o "
                "registro da view analitica, porque sem eixo temporal nenhuma "
                "metrica pode situar o caso. As demais sao respeitadas apenas "
                "pelas metricas que dependem daquela dimensao.",
                "O codigo 9 (Ignorado) e preservado e excluido dos denominadores "
                "na camada de metricas, nunca convertido em 'Nao' ou zero.",
                "Colunas com dados pessoais nao sao lidas do arquivo bruto "
                "(ver DENIED_COLUMNS em src/data/schema.py).",
                "Toda alteracao de valor e registrada por registro na coluna "
                "ajustes_aplicados, alem de contabilizada aqui.",
                "Codigos fora do dominio do dicionario sao contados, nao anulados: "
                "a camada de metricas so reconhece codigos validos, entao eles nao "
                "entram em numerador nem denominador.",
                "A semana epidemiologica e derivada de DT_SIN_PRI pela regra do "
                "Ministerio da Saude (semana iniciada no domingo). SEM_PRI e "
                "lida apenas para reconciliacao: a divergencia e contada e "
                "nenhuma das duas corrige ou preenche a outra.",
                "Codigos fora do dominio e codigos de ausencia sao contados "
                "separadamente por coluna, e a identidade total = validos + "
                "ignorados + ausentes + fora_do_dominio e publicada verificada.",
                "A secao 'schema' compara o arquivo bruto contra a safra "
                "anterior do MESMO ano (coluna nova, coluna sumida, tipo, "
                "categoria nova, completude e contagem de registros). Todo "
                "achado aparece classificado em ERROR ou WARNING; nenhum e "
                "ignorado em silencio, e aceitar uma mudanca conhecida exige "
                "--accept-drift, que fica gravado na linha de base.",
                "Linhas identicas nas colunas persistidas sao contadas mas NAO "
                "deduplicadas: sem o identificador da notificacao (excluido por "
                "minimizacao) nao e possivel distinguir duplicata de pacientes "
                "distintos com os mesmos atributos agregados. Deduplicar "
                "removeria casos reais.",
            ],
        }
