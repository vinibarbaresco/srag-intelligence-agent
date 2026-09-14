"""Regras de alerta avaliadas a cada execucao.

Um relatorio que so descreve o cenario exige que alguem o leia para perceber
uma mudanca. As regras abaixo transformam os indicadores calculados em um
veredito operacional -- `normal`, `atencao` ou `alerta` -- que aparece no
relatorio, no terminal e, com `--fail-on-alert`, no codigo de saida do
processo. E esse codigo que a execucao agendada usa para sinalizar.

As regras sao deterministicas e comparam apenas valores ja calculados pelas
tools com limiares declarados na configuracao. Nenhum limiar e inferido do
dado, e o modelo de linguagem nao participa da decisao.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from src.config import get_settings

LEVEL_NORMAL: Final[str] = "normal"
LEVEL_ATTENTION: Final[str] = "atencao"
LEVEL_ALERT: Final[str] = "alerta"

_LEVEL_RANK: Final[dict[str, int]] = {LEVEL_NORMAL: 0, LEVEL_ATTENTION: 1, LEVEL_ALERT: 2}


@dataclass(frozen=True, slots=True)
class AlertRule:
    """Regra declarativa: um indicador, um limiar e o nivel que ele dispara."""

    key: str
    metric: str
    description: str
    threshold: float
    level: str
    unit: str = "%"

    def to_dict(self) -> dict[str, Any]:
        return {
            "regra": self.key,
            "indicador": self.metric,
            "descricao": self.description,
            "limiar": self.threshold,
            "unidade": self.unit,
            "nivel_se_disparada": self.level,
        }


def threshold_rules() -> tuple[AlertRule, ...]:
    """Regras de limiar, montadas a partir da configuracao vigente."""
    settings = get_settings()
    return (
        AlertRule(
            key="crescimento_de_casos",
            metric="case_growth_rate",
            description="taxa de aumento de casos entre janelas consecutivas acima do limiar",
            threshold=settings.alert_growth_threshold_pct,
            level=LEVEL_ALERT,
        ),
        AlertRule(
            key="letalidade",
            metric="mortality_rate",
            description="letalidade entre casos encerrados acima do limiar",
            threshold=settings.alert_mortality_threshold_pct,
            level=LEVEL_ALERT,
        ),
        AlertRule(
            key="excesso_sazonal",
            metric="seasonal_excess",
            description="casos acima da mediana da mesma epoca nos anos de baseline",
            threshold=settings.alert_baseline_excess_threshold_pct,
            level=LEVEL_ALERT,
        ),
    )


def evaluate_alerts(
    metrics: dict[str, dict[str, Any]], diagnostics: dict[str, dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Avalia todas as regras sobre os resultados das tools.

    Args:
        metrics: indicadores do estado do grafo (`metric -> envelope`).
        diagnostics: diagnosticos do estado (completude da notificacao).

    Returns:
        Bloco com cada regra avaliada (valor observado, limiar, disparada), a
        lista dos disparos, o nivel consolidado e a contagem por nivel. Um
        indicador indisponivel nunca dispara alerta de limiar -- ele gera um
        item de `atencao`, porque a ausencia do dado tambem e informacao.
    """
    evaluated: list[dict[str, Any]] = []
    triggered: list[dict[str, Any]] = []

    for rule in threshold_rules():
        metric = metrics.get(rule.metric)
        value = metric.get("value") if metric else None
        item = {
            **rule.to_dict(),
            "valor_observado": value,
            "disparada": value is not None and value > rule.threshold,
        }
        if metric is None:
            item["mensagem"] = f"Indicador {rule.metric} nao executado nesta execucao."
        elif value is None:
            item["mensagem"] = (
                f"Indicador {rule.metric} indisponivel: {metric.get('unavailable_reason')}"
            )
        elif item["disparada"]:
            item["mensagem"] = (
                f"{rule.description}: {value}{rule.unit} observado contra limiar de "
                f"{rule.threshold}{rule.unit}."
            )
            item["nivel"] = rule.level
            triggered.append(item)
        else:
            item["mensagem"] = (
                f"{value}{rule.unit} observado, dentro do limiar de {rule.threshold}{rule.unit}."
            )
        evaluated.append(item)

    attention: list[dict[str, Any]] = []
    for key, metric in metrics.items():
        if metric.get("value") is None:
            attention.append(
                {
                    "regra": "indicador_indisponivel",
                    "indicador": key,
                    "nivel": LEVEL_ATTENTION,
                    "mensagem": (
                        f"{key} nao calculavel nesta execucao: {metric.get('unavailable_reason')}"
                    ),
                }
            )

    completeness = (diagnostics or {}).get("get_notification_completeness") or {}
    if completeness and completeness.get("corte_suficiente") is False:
        attention.append(
            {
                "regra": "corte_analitico_insuficiente",
                "indicador": "get_notification_completeness",
                "nivel": LEVEL_ATTENTION,
                "mensagem": completeness.get("alerta"),
            }
        )

    level = LEVEL_NORMAL
    for item in [*triggered, *attention]:
        if _LEVEL_RANK[item["nivel"]] > _LEVEL_RANK[level]:
            level = item["nivel"]

    return {
        "nivel": level,
        "regras_avaliadas": evaluated,
        "disparados": triggered,
        "atencao": attention,
        "total_disparados": len(triggered),
        "resumo": _summary(level, triggered, attention),
    }


def _summary(level: str, triggered: list[dict[str, Any]], attention: list[dict[str, Any]]) -> str:
    if level == LEVEL_NORMAL:
        return "Nenhuma regra de alerta disparada; indicadores dentro dos limiares configurados."
    parts = []
    if triggered:
        parts.append(
            f"{len(triggered)} regra(s) de limiar disparada(s): "
            + ", ".join(item["regra"] for item in triggered)
        )
    if attention:
        parts.append(
            f"{len(attention)} ponto(s) de atencao: "
            + ", ".join(item["regra"] for item in attention)
        )
    return "; ".join(parts) + "."
