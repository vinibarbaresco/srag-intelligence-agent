# Imagem de execucao do SRAG Intelligence Agent.
#
# Dados e artefatos ficam FORA da imagem: monte `data/` e `outputs/` como
# volumes para que o download de 600 MB e os relatorios sobrevivam ao container.
#
#   docker build -t srag-agent .
#   docker run --rm -v "$PWD/data:/app/data" -v "$PWD/outputs:/app/outputs" srag-agent --setup --no-llm
#   docker run --rm -v "$PWD/data:/app/data" -v "$PWD/outputs:/app/outputs" --env-file .env srag-agent
#   docker run --rm -p 8000:8000 -v "$PWD/data:/app/data" -v "$PWD/outputs:/app/outputs" \
#       --entrypoint python srag-agent -m src.api
#
# A chave da OpenAI entra por `--env-file .env` ou `-e OPENAI_API_KEY=...`;
# nunca e copiada para a imagem (.env esta no .dockerignore).

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    API_HOST=0.0.0.0

WORKDIR /app

# Dependencias primeiro, para aproveitar o cache de camadas quando so o codigo muda.
# `requirements.lock.txt` fixa e verifica por hash toda dependencia, direta e
# transitiva (gerado por `make lock`, ver requirements.txt para as diretas).
COPY requirements.lock.txt .
RUN pip install --upgrade pip && pip install --require-hashes -r requirements.lock.txt

COPY main.py pyproject.toml ./
COPY src/ src/
COPY docs/ docs/
# Referencias externas pequenas (populacao IBGE) acompanham o codigo.
COPY data/reference/ data/reference/

RUN mkdir -p data/raw data/processed data/analytics outputs \
    && useradd --create-home --uid 1000 srag \
    && chown -R srag:srag /app
USER srag

VOLUME ["/app/data", "/app/outputs"]
EXPOSE 8000

ENTRYPOINT ["python", "main.py"]
CMD ["--no-llm"]
