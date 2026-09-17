"""Filtros analiticos validados (Guardrail 4 -- sem SQL arbitrario).

O LLM nunca escreve SQL. Ele escolhe tools e preenche parametros tipados, que
sao validados aqui contra dominios fechados antes de virarem predicados. O SQL
propriamente dito e literal no codigo e recebe os valores por binding, de modo
que nenhum texto vindo do modelo chega a ser interpretado pelo banco.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from src.config import get_settings
from src.data.load_database import VIEW_ANALYTICS
from src.data.schema import CODE_LABELS, UF_CODES


class InvalidFilterError(ValueError):
    """Parametro de filtro fora do dominio permitido."""


@dataclass(frozen=True, slots=True)
class AnalyticFilters:
    """Recorte aplicado a uma consulta analitica.

    Attributes:
        uf: sigla da unidade federativa de notificacao, ou `None` para Brasil.
        classification: codigo de `CLASSI_FIN` (1-5), ou `None` para todas as
            classificacoes finais de SRAG.
    """

    uf: str | None = None
    classification: int | None = None

    def __post_init__(self) -> None:
        if self.uf is not None:
            normalized = str(self.uf).strip().upper()
            if normalized not in UF_CODES:
                raise InvalidFilterError(
                    f"UF invalida: {self.uf!r}. Valores aceitos: {sorted(UF_CODES)}"
                )
            object.__setattr__(self, "uf", normalized)

        if self.classification is not None:
            valid = CODE_LABELS["CLASSI_FIN"]
            if self.classification not in valid:
                raise InvalidFilterError(
                    f"Classificacao final invalida: {self.classification!r}. "
                    f"Valores aceitos: {sorted(valid)}"
                )

    def where_clause(self, *, uf_dimension: str = "SG_UF_NOT") -> tuple[str, list[Any]]:
        """Monta o predicado SQL e a lista de parametros para binding.

        Args:
            uf_dimension: coluna de UF a recortar. O padrao e `SG_UF_NOT`, a UF
                de **notificacao**, que e a dimensao correta para carga
                assistencial: o leito e ocupado onde o paciente foi internado.
                Indicadores com denominador populacional externo passam
                `SG_UF` (UF de **residencia**), para casar numerador e
                denominador -- ver `incidence_rate`.

        Returns:
            Par `(predicado, parametros)`.
        """
        predicates: list[str] = []
        parameters: list[Any] = []

        if self.uf is not None:
            predicates.append(f"{uf_dimension} = ?")
            parameters.append(self.uf)

        if self.classification is not None:
            predicates.append("CLASSI_FIN = ?")
            parameters.append(self.classification)

        clause = " AND ".join(predicates) if predicates else "TRUE"
        return clause, parameters

    def to_dict(self) -> dict[str, Any]:
        """Descreve o recorte em termos legiveis para o relatorio."""
        classification_label = (
            CODE_LABELS["CLASSI_FIN"].get(self.classification)
            if self.classification is not None
            else "todas as classificações finais"
        )
        return {
            "uf": self.uf or "BR (nacional)",
            "classificacao_final": classification_label,
        }


def reference_date(connection: Any, today: date | None = None) -> date:
    """Maior data de digitacao **plausivel** presente na base.

    Toda janela temporal do sistema e ancorada nesta data, e nao em `today()`:
    a base publicada pelo DATASUS tem defasagem em relacao ao dia corrente, e
    usar a data atual produziria janelas artificialmente vazias.

    A ancoragem no maximo da coluna, porem, e fragil a um unico valor corrompido:
    uma digitacao registrada no futuro -- e o arquivo bruto tem datas com anos
    como 2202 e 8202 em outras colunas -- deslocaria **todas** as janelas do
    sistema para um periodo vazio, e o relatorio sairia anunciando queda total de
    casos sem nenhuma pista da causa. Por isso a data de referencia ignora
    digitacoes posteriores ao dia de execucao: uma ficha nao pode ter sido
    digitada amanha.

    Args:
        connection: conexao com o banco analitico.
        today: dia de execucao; o padrao e a data corrente. Explicito para que
            os testes possam fixar o teto.

    Returns:
        Maior data de digitacao que nao esta no futuro.

    Raises:
        ValueError: se a base nao possuir nenhuma data de digitacao plausivel.
    """
    ceiling = today or datetime.now(tz=UTC).date()
    row = connection.execute(
        f"SELECT max(data_digitacao) FROM {VIEW_ANALYTICS} WHERE data_digitacao <= ?",
        [ceiling],
    ).fetchone()
    if row is None or row[0] is None:
        raise ValueError(
            "Base analitica sem data de digitacao plausivel (nao futura); nao e "
            "possivel definir a data de referencia."
        )
    return row[0]


def analysis_cutoff(connection: Any) -> date:
    """Ultimo dia considerado confiavel para analise.

    Subtrai `REPORTING_LAG_DAYS` da data de referencia para nao confundir
    atraso de notificacao com queda real de casos.
    """
    settings = get_settings()
    return reference_date(connection) - timedelta(days=settings.reporting_lag_days)
