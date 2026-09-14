"""Aplicacao FastAPI: rotas e tratamento de erros.

Regras da fronteira HTTP:

* nomes de tool e parametros passam pela mesma validacao das tools -- um nome
  fora do registro e 404, um parametro fora do dominio e 422;
* `run_id` e validado como UUID antes de virar caminho de arquivo, para que a
  leitura de relatorios e trilhas nunca saia de `outputs/`;
* falhas internas devolvem mensagem generica; o detalhe fica no log.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from src.api.schemas import (
    ErrorResponse,
    HealthResponse,
    IndicatorSummary,
    ReportRequest,
    ReportResponse,
)
from src.config import get_settings
from src.observability.logging_config import get_logger
from src.tools.registry import REGISTRY, UnknownToolError, call_tool

logger = get_logger(__name__)

_RUN_ID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

#: Categorias de tool expostas por rota de leitura direta.
_INDICATOR_CATEGORIES = {"indicador", "diagnostico"}
_SERIES_CATEGORIES = {"serie"}


def create_app() -> FastAPI:
    """Monta a aplicacao. Fabrica explicita para que os testes a instanciem."""
    app = FastAPI(
        title="SRAG Intelligence Agent",
        version="1",
        description=(
            "Indicadores deterministicos de SRAG (Open DATASUS) e geracao de relatorio "
            "com guardrails e trilha de auditoria. Analise agregada; nenhum dado individual."
        ),
        responses={422: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
    )

    @app.get("/health", response_model=HealthResponse, tags=["operacao"])
    def health() -> HealthResponse:
        settings = get_settings()
        ready = settings.database_path.exists()
        return HealthResponse(
            status="ok" if ready else "sem_banco",
            banco_analitico_pronto=ready,
            llm_habilitado=settings.llm_enabled,
            revisao_semantica_habilitada=settings.llm_enabled and settings.semantic_judge_enabled,
        )

    @app.get("/indicadores", tags=["dados"])
    def list_indicators() -> dict[str, Any]:
        return {
            "indicadores": [
                {"nome": tool.name, "categoria": tool.category, "descricao": tool.description}
                for tool in REGISTRY.values()
                if tool.category in _INDICATOR_CATEGORIES
            ]
        }

    @app.get("/indicadores/{nome}", tags=["dados"])
    def get_indicator(
        nome: str,
        uf: str | None = Query(default=None, max_length=2),
        classificacao: int | None = Query(default=None, ge=1, le=5),
        janela_dias: int | None = Query(default=None, ge=7, le=365),
    ) -> dict[str, Any]:
        return _call(
            nome,
            _INDICATOR_CATEGORIES,
            {"uf": uf, "classification": classificacao, "window_days": janela_dias},
        )

    @app.get("/series/{nome}", tags=["dados"])
    def get_series(
        nome: str,
        uf: str | None = Query(default=None, max_length=2),
        classificacao: int | None = Query(default=None, ge=1, le=5),
    ) -> dict[str, Any]:
        return _call(nome, _SERIES_CATEGORIES, {"uf": uf, "classification": classificacao})

    @app.post("/relatorios", response_model=ReportResponse, tags=["relatorio"])
    def create_report(request: ReportRequest) -> ReportResponse:
        if not get_settings().database_path.exists():
            raise HTTPException(
                status_code=503, detail="Banco analitico nao preparado. Execute main.py --setup."
            )
        from src.agent.orchestrator import DEFAULT_REQUEST, run_report

        state = run_report(
            request.solicitacao or DEFAULT_REQUEST,
            uf=request.uf,
            classification=request.classificacao,
            use_llm=request.usar_llm,
        )
        return _report_response(dict(state))

    @app.get("/relatorios/{run_id}", tags=["relatorio"])
    def read_report(run_id: str, formato: str = Query(default="markdown")) -> Any:
        run_id = _validate_run_id(run_id)
        settings = get_settings()
        if formato == "html":
            path = settings.reports_dir / f"relatorio_srag_{run_id}.html"
            return HTMLResponse(_read_text(path))
        if formato != "markdown":
            raise HTTPException(status_code=422, detail="formato deve ser 'markdown' ou 'html'.")
        path = settings.reports_dir / f"relatorio_srag_{run_id}.md"
        return PlainTextResponse(_read_text(path), media_type="text/markdown; charset=utf-8")

    @app.get("/auditoria/{run_id}", tags=["governanca"])
    def read_audit(run_id: str) -> dict[str, Any]:
        run_id = _validate_run_id(run_id)
        path = get_settings().audit_dir / f"{run_id}.jsonl"
        events = [json.loads(line) for line in _read_text(path).splitlines() if line.strip()]
        return {"run_id": run_id, "eventos": events, "total": len(events)}

    @app.exception_handler(Exception)
    async def _unexpected(_, exc: Exception) -> JSONResponse:
        logger.error("erro nao tratado na API", extra={"motivo": f"{type(exc).__name__}: {exc}"})
        return JSONResponse(status_code=500, content={"detail": "Erro interno."})

    return app


def _call(name: str, categories: set[str], raw: dict[str, Any]) -> dict[str, Any]:
    spec = REGISTRY.get(name)
    if spec is None or spec.category not in categories:
        raise HTTPException(status_code=404, detail=f"Recurso nao encontrado: {name!r}.")
    parameters = {key: value for key, value in raw.items() if value is not None}
    try:
        result = call_tool(name, parameters)
    except UnknownToolError as exc:  # defensivo: o registro ja foi consultado acima
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if "error" in result:
        status = 422 if "Parametros invalidos" in result["error"] else 503
        raise HTTPException(status_code=status, detail=result["error"])
    return result


def _validate_run_id(run_id: str) -> str:
    if not _RUN_ID.match(run_id):
        raise HTTPException(status_code=422, detail="run_id deve ser um UUID.")
    return run_id.lower()


def _read_text(path: Path) -> str:
    if not path.exists():
        raise HTTPException(status_code=404, detail="Execucao nao encontrada.")
    return path.read_text(encoding="utf-8")


def _report_response(state: dict[str, Any]) -> ReportResponse:
    validation = state.get("validation") or {}
    accepted = bool(validation.get("allowed", True))
    metrics = state.get("metrics") or {}
    usage = state.get("llm_usage") or {}
    return ReportResponse(
        run_id=str(state.get("run_id")),
        aceito=accepted,
        motivo_da_recusa=None if accepted else validation.get("reason"),
        guardrail_de_entrada=None if accepted else validation.get("blocked_by"),
        via_de_interpretacao=state.get("interpretation_source") or None,
        indicadores=[
            IndicatorSummary(
                metric=metric["metric"],
                value=metric.get("value"),
                unit=metric.get("unit"),
                numerator=metric.get("numerator"),
                denominator=metric.get("denominator"),
                unavailable_reason=metric.get("unavailable_reason"),
            )
            for metric in metrics.values()
        ],
        alertas={
            key: (state.get("alerts") or {}).get(key)
            for key in ("nivel", "resumo", "total_disparados", "disparados")
            if (state.get("alerts") or {}).get(key) is not None
        },
        caminhos=state.get("report_paths") or {},
        avisos=list(state.get("warnings") or []),
        erros=list(state.get("errors") or []),
        custo_estimado_usd=usage.get("custo_total_estimado_usd"),
    )
