"""Download reproduzivel dos arquivos brutos de SRAG do Open DATASUS.

O portal de dados abertos do SUS nao expoe API CKAN publica (`/api/3/action/*`
responde 404). A pagina do dataset, porem, e renderizada no servidor e contem os
links diretos do bucket S3 de todos os anos. Como o nome do arquivo embute a
data de republicacao (`INFLUD26-24-08-2026.csv`), a URL **nunca** e fixada no
codigo: ela e resolvida a cada execucao.

Cada download e registrado em `data/raw/manifest.json` com tamanho, sha256 e
data, de modo que a base usada por um relatorio possa ser reproduzida depois.

Tambem e possivel registrar um CSV que ja se tenha em disco -- o caso de quem
recebeu o arquivo junto com o enunciado. O arquivo nao e copiado: entra no
manifesto pelo caminho original, com o mesmo calculo de sha256, para que a
proveniencia continue registrada seja qual for a origem.

Uso::

    python -m src.data.download                 # anos definidos em SRAG_YEARS
    python -m src.data.download --years 2026
    python -m src.data.download --force         # reprocessa mesmo com cache
    python -m src.data.download --local a.csv   # registra um arquivo local
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests

from src.config import DATASUS_DATASET_URL, get_settings
from src.observability.logging_config import configure_logging, get_logger

logger = get_logger(__name__)

#: Extrai os links de CSV anuais do HTML da pagina do dataset.
_RESOURCE_PATTERN = re.compile(
    r"https://[^\"'\\\s]*?/SRAG/(?P<year>\d{4})/(?P<filename>INFLUD\d{2}-[\d-]+\.csv)"
)

_HTTP_TIMEOUT = (15, 120)  # (connect, read) em segundos
_CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True, slots=True)
class RemoteResource:
    """Arquivo anual de SRAG publicado pelo DATASUS."""

    year: int
    filename: str
    url: str


class DownloadError(RuntimeError):
    """Falha na resolucao ou no download de um recurso do DATASUS."""


def discover_resources(dataset_url: str = DATASUS_DATASET_URL) -> dict[int, RemoteResource]:
    """Resolve as URLs vigentes dos arquivos anuais de SRAG.

    Args:
        dataset_url: pagina do dataset no portal de dados abertos.

    Returns:
        Mapa `ano -> recurso`. Quando ha mais de um arquivo para o mesmo ano,
        prevalece o de nome lexicograficamente maior (republicacao mais recente).

    Raises:
        DownloadError: se a pagina nao puder ser lida ou nao contiver links.
    """
    try:
        response = requests.get(dataset_url, timeout=_HTTP_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise DownloadError(f"Nao foi possivel ler {dataset_url}: {exc}") from exc

    resources: dict[int, RemoteResource] = {}
    for match in _RESOURCE_PATTERN.finditer(response.text):
        year = int(match.group("year"))
        candidate = RemoteResource(year, match.group("filename"), match.group(0))
        current = resources.get(year)
        if current is None or candidate.filename > current.filename:
            resources[year] = candidate

    if not resources:
        raise DownloadError(
            f"Nenhum arquivo CSV de SRAG encontrado em {dataset_url}. "
            "O layout da pagina pode ter mudado."
        )

    logger.info(
        "recursos resolvidos", extra={"anos": sorted(resources), "fonte": dataset_url}
    )
    return resources


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


#: Extrai o ano do padrao de nome do DATASUS (`INFLUD25...` -> 2025).
_YEAR_FROM_FILENAME = re.compile(r"INFLUD(?P<year>\d{2})", re.IGNORECASE)


def infer_year(filename: str) -> int | None:
    """Deduz o ano a partir do nome de arquivo do DATASUS."""
    match = _YEAR_FROM_FILENAME.search(filename)
    return 2000 + int(match.group("year")) if match else None


def _load_manifest(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("manifesto ilegivel; sera recriado", extra={"path": str(path)})
        return {}


def _save_manifest(path: Path, manifest: dict[str, dict]) -> None:
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def download_resource(
    resource: RemoteResource, destination_dir: Path, *, force: bool = False
) -> Path:
    """Baixa um arquivo anual, reaproveitando o cache local quando possivel.

    O cache e considerado valido se o arquivo existir com o mesmo nome e o mesmo
    tamanho informado pelo servidor -- o nome ja carrega a data de republicacao,
    portanto uma atualizacao da fonte gera outro nome e invalida o cache
    naturalmente.

    Args:
        resource: recurso resolvido por :func:`discover_resources`.
        destination_dir: diretorio `data/raw`.
        force: ignora o cache e rebaixa o arquivo.

    Returns:
        Caminho do arquivo local.
    """
    destination_dir.mkdir(parents=True, exist_ok=True)
    target = destination_dir / resource.filename

    expected_size = _remote_size(resource.url)
    if not force and target.exists() and target.stat().st_size == expected_size:
        logger.info(
            "cache valido; download ignorado",
            extra={"arquivo": resource.filename, "bytes": target.stat().st_size},
        )
        return target

    partial = target.with_suffix(target.suffix + ".part")
    logger.info(
        "baixando arquivo",
        extra={"arquivo": resource.filename, "bytes_esperados": expected_size},
    )
    try:
        with requests.get(resource.url, stream=True, timeout=_HTTP_TIMEOUT) as response:
            response.raise_for_status()
            with partial.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=_CHUNK_SIZE):
                    handle.write(chunk)
    except requests.RequestException as exc:
        partial.unlink(missing_ok=True)
        raise DownloadError(f"Falha ao baixar {resource.url}: {exc}") from exc

    partial.replace(target)
    return target


def _remote_size(url: str) -> int | None:
    """Le o `Content-Length` do recurso, ou `None` se indisponivel."""
    try:
        response = requests.head(url, timeout=_HTTP_TIMEOUT, allow_redirects=True)
        response.raise_for_status()
        length = response.headers.get("Content-Length")
        return int(length) if length else None
    except (requests.RequestException, ValueError):
        return None


def download_years(years: list[int], *, force: bool = False) -> dict[int, Path]:
    """Baixa os anos solicitados e atualiza o manifesto de proveniencia.

    Args:
        years: anos do dataset SRAG a obter.
        force: rebaixa mesmo havendo cache valido.

    Returns:
        Mapa `ano -> caminho local do CSV`.

    Raises:
        DownloadError: se algum ano solicitado nao existir na fonte.
    """
    settings = get_settings()
    settings.ensure_directories()

    resources = discover_resources()
    missing = sorted(set(years) - set(resources))
    if missing:
        raise DownloadError(
            f"Anos indisponiveis na fonte: {missing}. Disponiveis: {sorted(resources)}"
        )

    manifest = _load_manifest(settings.raw_manifest_path)
    downloaded: dict[int, Path] = {}

    for year in sorted(years):
        resource = resources[year]
        path = download_resource(resource, settings.raw_dir, force=force)
        downloaded[year] = path

        entry = manifest.get(str(year))
        needs_hash = (
            force
            or entry is None
            or entry.get("filename") != resource.filename
            or entry.get("size_bytes") != path.stat().st_size
        )
        manifest[str(year)] = {
            "year": year,
            "filename": resource.filename,
            "path": str(path),
            "url": resource.url,
            "origin": "download do Open DATASUS",
            "size_bytes": path.stat().st_size,
            "sha256": _sha256(path) if needs_hash else entry["sha256"],
            "downloaded_at": datetime.now(tz=timezone.utc).isoformat()
            if needs_hash
            else entry["downloaded_at"],
        }

    _save_manifest(settings.raw_manifest_path, manifest)
    logger.info(
        "download concluido",
        extra={"anos": sorted(downloaded), "manifesto": str(settings.raw_manifest_path)},
    )
    return downloaded


def register_local_file(path: Path, year: int | None = None) -> tuple[int, Path]:
    """Registra no manifesto um CSV de SRAG ja presente em disco.

    Serve para quem recebeu o arquivo pronto em vez de baixa-lo. O conteudo nao
    e copiado -- o manifesto guarda o caminho original -- mas o sha256 e
    calculado do mesmo modo, de forma que a proveniencia de um relatorio seja
    verificavel independentemente da origem do dado.

    Args:
        path: caminho do CSV.
        year: ano coberto pelo arquivo; deduzido do nome quando omitido.

    Returns:
        Par `(ano, caminho registrado)`.

    Raises:
        DownloadError: se o arquivo nao existir ou o ano nao puder ser deduzido.
    """
    settings = get_settings()
    settings.ensure_directories()

    path = path.expanduser().resolve()
    if not path.is_file():
        raise DownloadError(f"Arquivo nao encontrado: {path}")

    year = year or infer_year(path.name)
    if year is None:
        raise DownloadError(
            f"Nao foi possivel deduzir o ano de {path.name!r}. Informe "
            "explicitamente com --year."
        )

    manifest = _load_manifest(settings.raw_manifest_path)
    manifest[str(year)] = {
        "year": year,
        "filename": path.name,
        "path": str(path),
        "url": None,
        "origin": "arquivo local fornecido pelo usuario",
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
        "downloaded_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    _save_manifest(settings.raw_manifest_path, manifest)

    logger.info(
        "arquivo local registrado",
        extra={"ano": year, "arquivo": path.name, "bytes": path.stat().st_size},
    )
    return year, path


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    configure_logging(settings.log_level)

    parser = argparse.ArgumentParser(description="Baixa os arquivos de SRAG do DATASUS.")
    parser.add_argument(
        "--years",
        type=int,
        nargs="+",
        default=settings.srag_years,
        help="anos a baixar (padrao: valor de SRAG_YEARS no .env)",
    )
    parser.add_argument(
        "--force", action="store_true", help="rebaixa mesmo havendo cache valido"
    )
    parser.add_argument(
        "--local",
        type=Path,
        default=None,
        help="registra um CSV ja presente em disco em vez de baixar",
    )
    parser.add_argument(
        "--year",
        type=int,
        default=None,
        help="ano do arquivo informado em --local (deduzido do nome se omitido)",
    )
    args = parser.parse_args(argv)

    try:
        if args.local is not None:
            year, path = register_local_file(args.local, args.year)
            paths = {year: path}
        else:
            paths = download_years(args.years, force=args.force)
    except DownloadError as exc:
        logger.error("download falhou", extra={"motivo": str(exc)})
        return 1

    for year, path in sorted(paths.items()):
        print(f"{year}: {path} ({path.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
