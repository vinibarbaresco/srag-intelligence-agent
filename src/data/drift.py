"""Deteccao de mudanca de esquema entre safras do DATASUS.

O SIVEP-Gripe **republica o mesmo ano** varias vezes: `INFLUD25-14-09-2026.csv`
tem 336.391 linhas e `INFLUD25_DATASUS-Versao26-06-2025.csv` tem 165.397 -- sao
duas safras do ano de 2025, nao dois anos. Um pipeline que nao compara carga
contra carga aceita em silencio uma coluna que sumiu, um tipo que mudou ou um
codigo categorico que a fonte passou a emitir; o sintoma so aparece semanas
depois, como indicador vazio num relatorio.

Este modulo compara a carga corrente contra uma **linha de base persistida** e
classifica cada achado em ERROR (interrompe a carga) ou WARNING (registra e
segue). Nao existe caminho silencioso: todo achado entra no relatorio, e aceitar
uma mudanca conhecida exige o gesto explicito `--accept-drift`, que fica gravado
na propria linha de base com data e lista do que foi aceito.

Duas decisoes de desenho merecem registro.

**A linha de base e por ano.** Comparar a carga inteira contra a carga inteira
faria de toda republicacao um falso alarme de contagem -- e faria de toda
diferenca legitima entre anos um alarme de completude: `DT_DIGITA` esta 34,37%
vazia no INFLUD19 e 0,00% no INFLUD25, e isso e um fato dos dois anos, nao uma
degradacao. O que se compara e sempre a safra nova de um ano contra a safra
anterior **do mesmo ano**.

**A profundidade da inspecao respeita a fronteira de minimizacao.** Colunas
novas e colunas ausentes sao detectadas no **cabecalho** do arquivo, que e lido
inteiro e nao custa nada -- e por isso a chegada de `SURTO_SG`, `VG_OMS` ou
`REINF` e percebida mesmo sem que uma unica celula dessas colunas entre em
memoria. Tipo, completude e dominio categorico, ao contrario, exigem ler valores
e por isso sao observados **apenas nas colunas de `ALLOWED_COLUMNS`**. Inspecao
nao e desculpa para ler dado pessoal.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import pandas as pd

from src.data.cleaning.dates import parse_dates
from src.data.schema import ALLOWED_COLUMNS, CATEGORICAL_COLUMNS, CODE_LABELS
from src.observability.logging_config import get_logger

logger = get_logger(__name__)

# =============================================================================
# Vocabulario
# =============================================================================

SEVERITY_ERROR: Final[str] = "ERROR"
SEVERITY_WARNING: Final[str] = "WARNING"

#: As seis classes de mudanca detectadas. A ordem e a do relatorio.
KIND_NEW_COLUMNS: Final[str] = "new_columns"
KIND_MISSING_COLUMNS: Final[str] = "missing_columns"
KIND_DTYPE_CHANGES: Final[str] = "dtype_changes"
KIND_NEW_CATEGORIES: Final[str] = "new_categories"
KIND_MISSING_RATE_CHANGES: Final[str] = "missing_rate_changes"
KIND_RECORD_COUNT_CHANGES: Final[str] = "record_count_changes"

FINDING_KINDS: Final[tuple[str, ...]] = (
    KIND_NEW_COLUMNS,
    KIND_MISSING_COLUMNS,
    KIND_DTYPE_CHANGES,
    KIND_NEW_CATEGORIES,
    KIND_MISSING_RATE_CHANGES,
    KIND_RECORD_COUNT_CHANGES,
)

#: Tipos inferidos. Sao quatro de proposito: `vazio` e um estado, nao um tipo.
#: Uma coluna que estava toda vazia e passou a trazer inteiros nao mudou de
#: tipo -- ganhou dado --, e tratar isso como mudanca de tipo produziria alarme
#: em toda coluna que a fonte comeca a preencher.
KIND_EMPTY: Final[str] = "vazio"
KIND_INTEGER: Final[str] = "inteiro"
KIND_DATE: Final[str] = "data"
KIND_TEXT: Final[str] = "texto"

_INTEGER_PATTERN: Final[str] = r"[+-]?\d+"

#: Fracao dos valores nao vazios que precisa concordar para fixar o tipo.
#:
#: Nao e 1.0 porque a fonte ja publica lixo pontual -- datas ilegiveis e codigos
#: nao numericos sao contados pelo proprio pipeline de limpeza. Com exigencia de
#: unanimidade, **um** valor corrompido em 336 mil reclassificaria a coluna de
#: `data` para `texto` e interromperia a carga: um ERROR falso causado pelo
#: registro que a limpeza ja sabe tratar.
_KIND_AGREEMENT: Final[float] = 0.99

#: Teto de valores inspecionados por bloco na inferencia de tipo. Com a regra de
#: maioria acima, amostrar nao muda a conclusao e limita o custo do parse.
_KIND_SAMPLE: Final[int] = 20_000

#: Teto de categorias distintas guardadas por coluna. As colunas categoricas do
#: SIVEP tem dominios de 3 a 5 codigos, entao o teto nunca e alcancado com dado
#: saudavel; ele existe para que uma coluna que deixou de ser categorica na
#: origem nao carregue milhares de valores para dentro da linha de base.
_MAX_CATEGORIES: Final[int] = 64


class SchemaDriftError(RuntimeError):
    """Mudanca de esquema classificada como ERROR, sem aceite explicito."""


# =============================================================================
# Observacao da carga corrente
# =============================================================================


@dataclass(frozen=True, slots=True)
class SchemaObservation:
    """Retrato do arquivo bruto de um ano, como ele chegou nesta carga."""

    year: int
    filename: str
    #: Cabecalho completo do CSV (as 194 colunas), nao apenas a allowlist.
    raw_columns: tuple[str, ...]
    #: Tipo inferido por coluna lida. So cobre `ALLOWED_COLUMNS`.
    dtypes: dict[str, str]
    #: Percentual de celulas vazias por coluna lida.
    missing_rate: dict[str, float]
    #: Valores categoricos distintos observados, por coluna.
    categories: dict[str, list[str]]
    record_count: int

    def to_dict(self) -> dict[str, Any]:
        """Serializa a observacao no formato gravado na linha de base."""
        return {
            "filename": self.filename,
            "raw_columns": list(self.raw_columns),
            "dtypes": dict(sorted(self.dtypes.items())),
            "missing_rate": {k: round(v, 4) for k, v in sorted(self.missing_rate.items())},
            "categories": {k: sorted(v) for k, v in sorted(self.categories.items())},
            "record_count": self.record_count,
        }


def _infer_kind(values: pd.Series) -> str:
    """Classifica os valores nao vazios de uma coluna bruta em um tipo.

    Args:
        values: coluna como lida do CSV, ainda em texto.

    Returns:
        Um de :data:`KIND_EMPTY`, :data:`KIND_INTEGER`, :data:`KIND_DATE` ou
        :data:`KIND_TEXT`.
    """
    text = values.astype("string").str.strip().replace({"": pd.NA}).dropna()
    if text.empty:
        return KIND_EMPTY

    sample = text.head(_KIND_SAMPLE)
    total = len(sample)
    # Inteiro antes de data: `20241229` satisfaz os dois, e o formato compacto
    # nao e publicado pela fonte em nenhuma das tres variantes conhecidas.
    if int(sample.str.fullmatch(_INTEGER_PATTERN).sum()) / total >= _KIND_AGREEMENT:
        return KIND_INTEGER
    if int(parse_dates(sample).notna().sum()) / total >= _KIND_AGREEMENT:
        return KIND_DATE
    return KIND_TEXT


def _merge_kinds(left: str, right: str) -> str:
    """Combina o tipo de dois blocos da mesma coluna.

    `vazio` e neutro: um bloco sem valores nao contradiz nada. Blocos que
    discordam viram `texto`, que e o unico tipo capaz de conter os dois.
    """
    if left == KIND_EMPTY:
        return right
    if right == KIND_EMPTY:
        return left
    return left if left == right else KIND_TEXT


class SchemaObserver:
    """Acumula o retrato de um ano enquanto o arquivo e lido em blocos.

    Existe como acumulador, e nao como funcao sobre o dataframe final, porque a
    carga le o CSV em blocos e nunca tem o arquivo bruto inteiro em memoria --
    e porque a observacao precisa acontecer **antes** da limpeza: depois dela os
    tipos sao os que o pipeline imps, e nao os que a fonte publicou.
    """

    def __init__(self, year: int, filename: str) -> None:
        self.year = year
        self.filename = filename
        self._raw_columns: tuple[str, ...] = ()
        self._kinds: dict[str, str] = {}
        self._present: dict[str, int] = {}
        self._categories: dict[str, set[str]] = {}
        self._rows = 0

    def observe_header(self, columns: list[str]) -> None:
        """Registra o cabecalho completo do arquivo bruto."""
        self._raw_columns = tuple(columns)

    def observe_chunk(self, chunk: pd.DataFrame) -> None:
        """Acumula tipo, completude e categorias de um bloco ainda bruto."""
        self._rows += len(chunk)
        for column in chunk.columns:
            text = chunk[column].astype("string").str.strip().replace({"": pd.NA})
            self._present[column] = self._present.get(column, 0) + int(text.notna().sum())
            self._kinds[column] = _merge_kinds(
                self._kinds.get(column, KIND_EMPTY), _infer_kind(chunk[column])
            )
            if column in CATEGORICAL_COLUMNS:
                seen = self._categories.setdefault(column, set())
                if len(seen) < _MAX_CATEGORIES:
                    seen.update(str(value) for value in text.dropna().unique())

    def result(self) -> SchemaObservation:
        """Consolida o que foi observado em uma :class:`SchemaObservation`."""
        rows = self._rows
        return SchemaObservation(
            year=self.year,
            filename=self.filename,
            raw_columns=self._raw_columns,
            dtypes=dict(self._kinds),
            missing_rate={
                column: (1.0 - present / rows) * 100 if rows else 0.0
                for column, present in self._present.items()
            },
            categories={column: sorted(values) for column, values in self._categories.items()},
            record_count=rows,
        )


# =============================================================================
# Achados
# =============================================================================


@dataclass(frozen=True, slots=True)
class DriftFinding:
    """Uma mudanca detectada entre a safra anterior e a corrente."""

    kind: str
    severity: str
    year: int
    message: str
    column: str | None = None
    previous: Any = None
    current: Any = None

    def to_dict(self) -> dict[str, Any]:
        """Serializa o achado para o relatorio de qualidade."""
        return {
            "tipo": self.kind,
            "severidade": self.severity,
            "ano": self.year,
            "coluna": self.column,
            "anterior": self.previous,
            "atual": self.current,
            "mensagem": self.message,
        }


@dataclass
class DriftThresholds:
    """Limiares de variacao tolerada entre safras do mesmo ano.

    Os tres valores foram escolhidos contra as safras medidas, nao arbitrados:

    * `missing_rate_delta_pct` -- uma republicacao completa o que faltava, entao
      a completude do mesmo ano se move pouco. 5 pontos percentuais ficam acima
      do ruido de um preenchimento parcial e muito abaixo das diferencas
      legitimas **entre anos** (`DT_DIGITA`: 34,37% em 2019 contra 0,00% em
      2025), que a comparacao por ano ja isola.
    * `record_drop_pct` -- o DATASUS republica o ano de forma cumulativa: a
      safra nova nunca deveria ter menos fichas que a anterior. Uma queda so
      pode vir de arquivo truncado, download incompleto ou ano trocado. O teto
      de 20% e folga para uma revisao de encerramento, nao tolerancia a perda.
    * `record_growth_pct` -- o crescimento e esperado e legitimo: 165.397 para
      336.391 no mesmo INFLUD25, +103%. Nao pode interromper a carga, mas
      tambem nao pode passar despercebido, porque dobrar o denominador desloca
      toda serie historica.
    """

    missing_rate_delta_pct: float = 5.0
    record_drop_pct: float = 20.0
    record_growth_pct: float = 50.0

    def to_dict(self) -> dict[str, float]:
        """Publica os limiares junto dos achados que eles produziram."""
        return {
            "variacao_de_ausencia_pp": self.missing_rate_delta_pct,
            "queda_de_registros_pct": self.record_drop_pct,
            "crescimento_de_registros_pct": self.record_growth_pct,
        }


@dataclass
class DriftReport:
    """Resultado da comparacao de uma carga inteira contra a linha de base."""

    findings: list[DriftFinding] = field(default_factory=list)
    #: Anos que ainda nao tinham linha de base e acabaram de estabelece-la.
    baselines_established: list[int] = field(default_factory=list)
    thresholds: DriftThresholds = field(default_factory=DriftThresholds)
    accepted: bool = False

    @property
    def errors(self) -> list[DriftFinding]:
        """Achados que interrompem a carga."""
        return [item for item in self.findings if item.severity == SEVERITY_ERROR]

    @property
    def warnings(self) -> list[DriftFinding]:
        """Achados que sao registrados e nao interrompem a carga."""
        return [item for item in self.findings if item.severity == SEVERITY_WARNING]

    def by_kind(self, kind: str) -> list[DriftFinding]:
        """Achados de uma das seis classes de mudanca."""
        return [item for item in self.findings if item.kind == kind]

    def to_dict(self) -> dict[str, Any]:
        """Serializa a secao `schema` do relatorio de qualidade.

        Cada achado aparece **duas vezes**: sob a sua classe de mudanca e na
        lista da sua severidade. A redundancia e proposital -- e ela que torna
        verificavel a promessa de que nenhum achado e omitido de um dos dois
        cortes do relatorio.
        """
        payload: dict[str, Any] = {
            kind: [item.to_dict() for item in self.by_kind(kind)] for kind in FINDING_KINDS
        }
        payload["errors"] = [item.to_dict() for item in self.errors]
        payload["warnings"] = [item.to_dict() for item in self.warnings]
        payload["baseline_estabelecida_para"] = sorted(self.baselines_established)
        payload["limiares"] = self.thresholds.to_dict()
        payload["aceite_explicito"] = self.accepted
        payload["resumo"] = (
            f"{len(self.errors)} erro(s) e {len(self.warnings)} aviso(s) de mudanca de esquema"
            if self.findings
            else (
                "linha de base estabelecida, sem comparacao possivel"
                if self.baselines_established
                else "nenhuma mudanca de esquema detectada"
            )
        )
        return payload

    def log(self) -> None:
        """Emite cada achado no nivel correspondente a sua severidade."""
        for year in sorted(self.baselines_established):
            logger.info(
                "linha de base de esquema estabelecida",
                extra={"ano": year},
            )
        for finding in self.findings:
            emit = logger.error if finding.severity == SEVERITY_ERROR else logger.warning
            emit("mudanca de esquema detectada", extra=finding.to_dict())


# =============================================================================
# Comparacao
# =============================================================================


def _official_domain(column: str) -> set[str]:
    """Codigos que o dicionario oficial declara para `column`, como texto."""
    return {str(code) for code in CODE_LABELS.get(column, {})}


def compare(
    baseline: dict[str, Any] | None,
    observation: SchemaObservation,
    thresholds: DriftThresholds | None = None,
) -> list[DriftFinding]:
    """Compara a observacao de um ano contra a linha de base do mesmo ano.

    Args:
        baseline: retrato da safra anterior deste ano, ou `None` na primeira
            carga -- caso em que nao ha o que comparar e nenhum achado e
            produzido.
        observation: retrato da safra corrente.
        thresholds: limiares de variacao tolerada.

    Returns:
        Lista de achados, cada um ja classificado em ERROR ou WARNING.
    """
    if baseline is None:
        return []

    thresholds = thresholds or DriftThresholds()
    year = observation.year
    findings: list[DriftFinding] = []

    # --- colunas do arquivo bruto (cabecalho, sem ler uma celula sequer) -----
    previous_columns = set(baseline.get("raw_columns", ()))
    current_columns = set(observation.raw_columns)

    for column in sorted(current_columns - previous_columns):
        # WARNING e nao ERROR porque a allowlist protege o pipeline: a coluna
        # nova nao e lida e nao entra em metrica nenhuma. Mas a fonte mudou, e
        # uma coluna nova pode ser exatamente o campo que o proximo indicador
        # precisa -- ou o sinal de que um campo existente foi renomeado.
        findings.append(
            DriftFinding(
                kind=KIND_NEW_COLUMNS,
                severity=SEVERITY_WARNING,
                year=year,
                column=column,
                previous=None,
                current=column,
                message=(
                    f"coluna {column} passou a existir no arquivo bruto de {year}; "
                    "nao e lida pela allowlist, mas a fonte mudou"
                ),
            )
        )

    for column in sorted(previous_columns - current_columns):
        # A allowlist e o criterio de severidade: sem uma coluna que o pipeline
        # le, uma regra de limpeza ou um indicador inteiro para de funcionar.
        allowed = column in ALLOWED_COLUMNS
        findings.append(
            DriftFinding(
                kind=KIND_MISSING_COLUMNS,
                severity=SEVERITY_ERROR if allowed else SEVERITY_WARNING,
                year=year,
                column=column,
                previous=column,
                current=None,
                message=(
                    f"coluna {column} desapareceu do arquivo bruto de {year}"
                    + (
                        "; ela pertence a ALLOWED_COLUMNS e alimenta o calculo"
                        if allowed
                        else "; nao era lida pelo pipeline"
                    )
                ),
            )
        )

    # --- tipo inferido -------------------------------------------------------
    previous_dtypes = baseline.get("dtypes", {})
    for column, current_kind in sorted(observation.dtypes.items()):
        previous_kind = previous_dtypes.get(column)
        # `vazio` de qualquer um dos lados nao e mudanca de tipo: e dado que
        # comecou ou parou de ser preenchido, e isso e assunto da completude.
        if previous_kind in (None, KIND_EMPTY) or current_kind == KIND_EMPTY:
            continue
        if previous_kind == current_kind:
            continue
        findings.append(
            DriftFinding(
                kind=KIND_DTYPE_CHANGES,
                severity=SEVERITY_ERROR,
                year=year,
                column=column,
                previous=previous_kind,
                current=current_kind,
                message=(
                    f"coluna {column} mudou de {previous_kind} para {current_kind} em {year}; "
                    "a regra de limpeza correspondente converteria os valores em nulo"
                ),
            )
        )

    # --- categorias novas ----------------------------------------------------
    previous_categories = baseline.get("categories", {})
    for column, values in sorted(observation.categories.items()):
        known = _official_domain(column) | set(previous_categories.get(column, ()))
        for value in sorted(set(values) - known):
            findings.append(
                DriftFinding(
                    kind=KIND_NEW_CATEGORIES,
                    severity=SEVERITY_WARNING,
                    year=year,
                    column=column,
                    previous=sorted(known),
                    current=value,
                    message=(
                        f"codigo {value!r} apareceu em {column} ({year}) sem estar no "
                        "dicionario oficial nem na safra anterior"
                    ),
                )
            )

    # --- completude ----------------------------------------------------------
    previous_missing = baseline.get("missing_rate", {})
    for column, current_rate in sorted(observation.missing_rate.items()):
        if column not in previous_missing:
            continue
        delta = current_rate - previous_missing[column]
        if abs(delta) < thresholds.missing_rate_delta_pct:
            continue
        findings.append(
            DriftFinding(
                kind=KIND_MISSING_RATE_CHANGES,
                severity=SEVERITY_WARNING,
                year=year,
                column=column,
                previous=round(previous_missing[column], 3),
                current=round(current_rate, 3),
                message=(
                    f"ausencia de {column} em {year} variou {delta:+.2f} pontos percentuais "
                    f"entre safras (limiar {thresholds.missing_rate_delta_pct} pp)"
                ),
            )
        )

    # --- contagem de registros ----------------------------------------------
    previous_count = baseline.get("record_count")
    if previous_count:
        variation = (observation.record_count - previous_count) / previous_count * 100
        if variation <= -thresholds.record_drop_pct:
            findings.append(
                DriftFinding(
                    kind=KIND_RECORD_COUNT_CHANGES,
                    severity=SEVERITY_ERROR,
                    year=year,
                    column=None,
                    previous=previous_count,
                    current=observation.record_count,
                    message=(
                        f"a safra de {year} perdeu {abs(variation):.1f}% dos registros "
                        f"({previous_count} para {observation.record_count}); o DATASUS "
                        "republica o ano de forma cumulativa, entao perder ficha indica "
                        "arquivo truncado ou ano trocado"
                    ),
                )
            )
        elif variation >= thresholds.record_growth_pct:
            findings.append(
                DriftFinding(
                    kind=KIND_RECORD_COUNT_CHANGES,
                    severity=SEVERITY_WARNING,
                    year=year,
                    column=None,
                    previous=previous_count,
                    current=observation.record_count,
                    message=(
                        f"a safra de {year} cresceu {variation:+.1f}% "
                        f"({previous_count} para {observation.record_count}); esperado numa "
                        "republicacao, mas desloca toda serie historica do ano"
                    ),
                )
            )

    return findings


# =============================================================================
# Linha de base persistida
# =============================================================================


def load_baseline(path: Path) -> dict[str, Any]:
    """Le a linha de base, ou devolve vazio se ela ainda nao existe.

    Um arquivo ilegivel devolve vazio e **avisa**: a carga seguinte volta ao
    estado "primeira carga" e reestabelece a linha de base, o que e visivel no
    relatorio. Nunca e um `except` que engole o problema.
    """
    if not path.exists():
        return {}
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning(
            "linha de base de esquema ilegivel; sera reestabelecida",
            extra={"path": str(path)},
        )
        return {}
    return content.get("years", {})


def save_baseline(
    path: Path,
    observations: list[SchemaObservation],
    *,
    accepted: DriftReport | None = None,
) -> None:
    """Grava a linha de base do que acabou de ser carregado.

    Anos ja presentes e nao recarregados sao preservados: a linha de base e um
    acumulado por ano, nao um retrato da ultima execucao.

    Args:
        path: arquivo da linha de base.
        observations: retratos dos anos processados nesta carga.
        accepted: relatorio cujos achados foram aceitos com `--accept-drift`.
            Quando presente, o aceite fica gravado com data e lista do que foi
            aceito -- e o que torna a excecao auditavel em vez de invisivel.
    """
    years = load_baseline(path)
    now = datetime.now(tz=UTC).isoformat()
    for observation in observations:
        entry = observation.to_dict()
        entry["captured_at"] = now
        if accepted is not None and accepted.findings:
            entry["accepted_at"] = now
            entry["accepted_findings"] = [
                item.to_dict() for item in accepted.findings if item.year == observation.year
            ]
        years[str(observation.year)] = entry

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"years": years}, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def detect_drift(
    observations: list[SchemaObservation],
    baseline_path: Path,
    *,
    accept: bool = False,
    thresholds: DriftThresholds | None = None,
) -> DriftReport:
    """Compara todos os anos da carga corrente contra a linha de base.

    Args:
        observations: retratos dos anos processados.
        baseline_path: arquivo da linha de base por ano.
        accept: marca os achados como aceitos, de modo que um ERROR registre e
            nao interrompa. Corresponde a `--accept-drift` na linha de comando.
        thresholds: limiares de variacao tolerada.

    Returns:
        Relatorio com os achados, ja classificados.
    """
    baseline = load_baseline(baseline_path)
    report = DriftReport(thresholds=thresholds or DriftThresholds(), accepted=accept)

    for observation in observations:
        entry = baseline.get(str(observation.year))
        if entry is None:
            report.baselines_established.append(observation.year)
            continue
        report.findings.extend(compare(entry, observation, report.thresholds))

    return report
