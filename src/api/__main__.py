"""`python -m src.api` -- sobe a API HTTP com uvicorn."""

from __future__ import annotations

import sys

import uvicorn

from src.config import get_settings
from src.observability.logging_config import configure_logging


def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    uvicorn.run(
        "src.api.app:create_app",
        factory=True,
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level.lower(),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
