"""Historico de execucoes e comparacao com o relatorio anterior.

Cada relatorio gerado acrescenta uma linha JSON em `outputs/history/runs.jsonl`
com o recorte, a data de corte analitica e o valor de cada indicador. Isso
permite dizer, no relatorio corrente, o que mudou desde a ultima execucao do
mesmo recorte -- a leitura que interessa a quem acompanha a serie semana a
semana e que um relatorio isolado nao consegue dar.

O arquivo guarda apenas agregados ja publicados no relatorio; nenhum dado
individual ou texto do modelo passa por aqui.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.config import get_settings
from src.observability.logging_config import get_logger

logger = get_logger(__name__)

#: Indicadores acompanhados entre execucoes, na ordem do relatorio.
TRACKED_METRICS: tuple[str, ...] = (
    "case_growth_rate",
    "mortality_rate",
    "icu_admission_rate",
    "vaccination_coverage_among_cases",
    "incidence_rate",
    "seasonal_excess",
)


def _history_path(path: Path | None = None) -> Path:
    target = path or get_settings().run_history_path
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def _scope(uf: str | None, classification: int | None) -> dict[str, Any]:
    return {"uf": uf or "BR", "classification": classification}


def build_entry(state: dict[str, Any], alerts: dict[str, Any] | None = None) -> dict[str, Any]:
    """Resumo de uma execucao, no formato gravado no historico."""
    metrics = state.get("metrics") or {}
    cutoff = next(
        (
            metric.get("components", {}).get("data_corte_analitica")
            for metric in metrics.values()
            if metric.get("components", {}).get("data_corte_analitica")
        ),
        None,
    )
    return {
        "run_id": state.get("run_id"),
        "gerado_em": datetime.now(tz=UTC).isoformat(),
        **_scope(state.get("uf"), state.get("classification")),
        "data_corte_analitica": cutoff,
        "indicadores": {
            key: (metrics.get(key) or {}).get("value") for key in TRACKED_METRICS if key in metrics
        },
        "nivel_de_alerta": (alerts or {}).get("nivel"),
        "alertas_disparados": (alerts or {}).get("total_disparados", 0),
    }


def read_history(path: Path | None = None) -> list[dict[str, Any]]:
    """Le todas as execucoes registradas (linhas ilegiveis sao ignoradas)."""
    target = _history_path(path)
    if not target.exists():
        return []
    entries: list[dict[str, Any]] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            logger.warning("linha ilegivel no historico de execucoes ignorada")
    return entries


def previous_run(
    uf: str | None, classification: int | None, path: Path | None = None
) -> dict[str, Any] | None:
    """Ultima execucao registrada para o mesmo recorte, ou `None`."""
    scope = _scope(uf, classification)
    for entry in reversed(read_history(path)):
        same_uf = entry.get("uf") == scope["uf"]
        if same_uf and entry.get("classification") == scope["classification"]:
            return entry
    return None


def record_run(entry: dict[str, Any], path: Path | None = None) -> Path:
    """Acrescenta a execucao ao historico e devolve o caminho do arquivo."""
    target = _history_path(path)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    return target


def compare_runs(current: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    """Variacao de cada indicador entre a execucao anterior e a corrente.

    A comparacao e publicada como esta, sem julgamento: quem decide se uma
    variacao de 2 pontos percentuais e relevante e o leitor, com o contexto
    de completude e limitacoes do proprio relatorio.
    """
    if previous is None:
        return {
            "execucao_anterior": None,
            "mensagem": "Primeira execucao registrada para este recorte; sem base de comparacao.",
            "variacao": {},
        }

    variation: dict[str, dict[str, Any]] = {}
    for key, value in (current.get("indicadores") or {}).items():
        before = (previous.get("indicadores") or {}).get(key)
        delta = round(value - before, 2) if value is not None and before is not None else None
        variation[key] = {"anterior": before, "atual": value, "variacao": delta}

    return {
        "execucao_anterior": previous.get("run_id"),
        "gerada_em": previous.get("gerado_em"),
        "data_corte_anterior": previous.get("data_corte_analitica"),
        "data_corte_atual": current.get("data_corte_analitica"),
        "mesma_data_de_corte": previous.get("data_corte_analitica")
        == current.get("data_corte_analitica"),
        "variacao": variation,
    }
