# Atalhos de desenvolvimento e demonstracao.
#
#   make demo          prepara a base (download) e gera o relatorio sem LLM
#   make demo CSV=...  idem, a partir de um CSV ja em disco (base do enunciado)
#   make setup-completo  carrega os anos de baseline e atualiza IBGE e CNES
#   make referencias     atualiza so as referencias externas automatizaveis
#   make run           relatorio com LLM (exige OPENAI_API_KEY no .env)
#   make test | lint | docs | api | docker-build | docker-demo
#   make venv          ambiente isolado nas versoes fixadas (reproduz o CI)
#   make lock          regenera requirements*.lock.txt a partir dos .txt fonte
#
# No Windows sem `make`, use `.\run_demo.ps1` (mesmos passos).
#
# `install` e `venv` instalam a partir dos `.lock.txt` (fixados e verificados
# por hash, direta e transitivamente -- ver `make lock`), nunca direto dos
# `requirements*.txt` de faixa aberta: o CI e o Docker fazem o mesmo, e assim
# os tres reproduzem exatamente o mesmo conjunto de pacotes.

PYTHON ?= python

# Ambiente de verificacao local.
#
# O CI instala exatamente o que `requirements-dev.lock.txt` fixa -- a mesma
# versao exata de cada pacote, direto e transitivo, verificada por hash. Um
# interpretador de sistema com uma versao de pacote fora do lock -- pandas 3.x
# instalado a mao, por exemplo, quando o lock fixa 2.x -- roda a suite contra
# uma configuracao que o projeto nao trava, e o defeito so aparece no CI.
# `make venv` cria o ambiente certo, e os demais alvos aceitam `PYTHON=` para
# usa-lo:
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

.PHONY: install venv lock demo run setup setup-completo referencias test lint format docs check api docker-build docker-demo clean

install:
	$(PYTHON) -m pip install --require-hashes -r requirements-dev.lock.txt

# Regenera os lockfiles com hash a partir de requirements.txt e
# requirements-dev.txt. Exige `uv` (https://astral.sh/uv), ferramenta externa
# ao ambiente do projeto -- regenerar o lock nunca instala nada na dependencia
# de producao. `--universal` resolve para todos os SO/arquitetura suportados
# (desenvolvimento em Windows, CI e Docker em Linux) e `--generate-hashes`
# fixa por hash tambem as dependencias transitivas.
lock:
	uv pip compile requirements.txt --universal --python-version 3.12 --generate-hashes -o requirements.lock.txt
	uv pip compile requirements-dev.txt --universal --python-version 3.12 --generate-hashes -o requirements-dev.lock.txt

# Nao entra em `install`: criar meio giga de ambiente e uma decisao de quem
# desenvolve, nao um efeito colateral de instalar dependencias. O diretorio
# e ignorado pelo git (`.venv-*/`) e pode ser apagado a qualquer momento.
venv:
	$(PYTHON) -m venv $(VENV)
	$(VENV_PY) -m pip install --upgrade pip
	$(VENV_PY) -m pip install --require-hashes -r requirements-dev.lock.txt
	@$(VENV_PY) -c "import pandas; print('pandas instalado:', pandas.__version__)"
	@echo "Ambiente pronto. Rode o mesmo gate do CI com:"
	@echo "    make check PYTHON=$(VENV_PY)"

setup:
	$(PYTHON) main.py $(SETUP) --no-llm $(UF_FLAG)

# Preparacao completa: carrega tambem os anos de baseline e atualiza as
# referencias externas, deixando TODOS os indicadores calculaveis. Custa o
# download de varios anos do DATASUS.
setup-completo:
	$(PYTHON) main.py --setup --setup-mode completo --no-llm $(UF_FLAG)

# Atualiza so as referencias externas com fonte automatizavel. A do SI-PNI fica
# de fora: o extrato mensal tem alguns GB (ver src/data/reference/vaccination.py).
referencias:
	$(PYTHON) -m src.data.reference.population
	$(PYTHON) -m src.data.reference.icu_capacity

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
