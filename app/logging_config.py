import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_logging(level: str = "INFO", log_dir: Path | None = None) -> None:
    log_dir = log_dir or Path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    file_handler = RotatingFileHandler(
        log_dir / "job-agent.log",
        maxBytes=2_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        handlers=[console, file_handler],
        force=True,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
