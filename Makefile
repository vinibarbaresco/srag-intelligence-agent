# Atalhos de desenvolvimento e demonstracao.
#
#   make demo          prepara a base (download) e gera o relatorio sem LLM
#   make demo CSV=...  idem, a partir de um CSV ja em disco (base do enunciado)
#   make run           relatorio com LLM (exige OPENAI_API_KEY no .env)
#   make test | lint | docs | api | docker-build | docker-demo
#   make venv          ambiente isolado nas versoes fixadas (reproduz o CI)
#
# No Windows sem `make`, use `.\run_demo.ps1` (mesmos passos).

PYTHON ?= python

# Ambiente de verificacao local.
#
# O CI instala exatamente o que `requirements.txt` fixa. Um interpretador de
# sistema com versao fora dessa faixa -- pandas 3.x, por exemplo, quando o
# pino e `>=2.2,<3` -- roda a suite contra uma configuracao que o projeto nao
# suporta, e o defeito so aparece no CI. `make venv` cria o ambiente certo, e
# os demais alvos aceitam `PYTHON=` para usa-lo:
#
#   make venv
#   make check PYTHON=$(VENV_PY)
VENV ?= .venv-ci
ifeq ($(OS),Windows_NT)
VENV_PY := $(VENV)/Scripts/python.exe
else
VENV_PY := $(VENV)/bin/python
endif

YEARS  ?= 2022 2023 2024 2025 2026
CSV    ?=
UF     ?=

UF_FLAG := $(if $(UF),--uf $(UF),)
SETUP   := $(if $(CSV),--setup --csv $(CSV),--setup --years $(YEARS))

.PHONY: install venv demo run setup test lint format docs check api docker-build docker-demo clean

install:
	$(PYTHON) -m pip install -r requirements-dev.txt

# Nao entra em `install`: criar meio giga de ambiente e uma decisao de quem
# desenvolve, nao um efeito colateral de instalar dependencias. O diretorio
# e ignorado pelo git (`.venv-*/`) e pode ser apagado a qualquer momento.
venv:
	$(PYTHON) -m venv $(VENV)
	$(VENV_PY) -m pip install --upgrade pip
	$(VENV_PY) -m pip install -r requirements-dev.txt
	@$(VENV_PY) -c "import pandas; print('pandas instalado:', pandas.__version__)"
	@echo "Ambiente pronto. Rode o mesmo gate do CI com:"
	@echo "    make check PYTHON=$(VENV_PY)"

setup:
	$(PYTHON) main.py $(SETUP) --no-llm $(UF_FLAG)

demo: setup
	@echo "Relatorio gerado em outputs/reports/ (via deterministica)."

run:
	$(PYTHON) main.py $(UF_FLAG)

test:
	$(PYTHON) -m pytest -q

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .

format:
	$(PYTHON) -m ruff format .
	$(PYTHON) -m ruff check --fix .

docs:
	$(PYTHON) docs/gerar_documentacao.py
	$(PYTHON) docs/gerar_diagrama_pdf.py

# O mesmo gate do CI, em uma linha.
check: lint test docs
	git diff --exit-code --stat docs/

api:
	$(PYTHON) -m src.api

docker-build:
	docker build -t srag-agent .

docker-demo: docker-build
	docker run --rm -v "$(CURDIR)/data:/app/data" -v "$(CURDIR)/outputs:/app/outputs" \
		srag-agent --setup --years $(YEARS) --no-llm

clean:
	rm -rf outputs/reports/* outputs/charts/* outputs/audit/* outputs/history/*
