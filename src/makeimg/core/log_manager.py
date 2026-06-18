from __future__ import annotations

import logging
import logging.handlers
from datetime import datetime
from pathlib import Path


def setup_logging(logs_dir: Path, level: int = logging.DEBUG) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(level)

    if root.handlers:
        return

    # ローリングログ (app.log, 最大10MB×5世代)
    app_handler = logging.handlers.RotatingFileHandler(
        logs_dir / "app.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    app_handler.setFormatter(fmt)
    app_handler.setLevel(logging.DEBUG)
    root.addHandler(app_handler)

    # 日付付きログ (makeimg_YYYYMMDD.log, 起動日ごとに1ファイル)
    date_str = datetime.now().strftime("%Y%m%d")
    date_handler = logging.FileHandler(
        logs_dir / f"makeimg_{date_str}.log",
        encoding="utf-8",
    )
    date_handler.setFormatter(fmt)
    date_handler.setLevel(logging.DEBUG)
    root.addHandler(date_handler)

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    console.setLevel(logging.INFO)
    root.addHandler(console)


def get_setup_logger(logs_dir: Path) -> logging.Logger:
    logger = logging.getLogger("makeimg.setup")
    if not any(isinstance(h, logging.FileHandler) and "setup" in getattr(h, "baseFilename", "") for h in logger.handlers):
        handler = logging.handlers.RotatingFileHandler(
            logs_dir / "setup.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s]: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        logger.addHandler(handler)
    return logger


def get_job_logger(logs_dir: Path, job_id: str) -> logging.Logger:
    logger = logging.getLogger(f"makeimg.job.{job_id}")
    if not logger.handlers:
        job_logs_dir = logs_dir / "jobs"
        job_logs_dir.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(
            job_logs_dir / f"{job_id}.log",
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s]: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        logger.addHandler(handler)
    return logger
