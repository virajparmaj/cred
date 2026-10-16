"""Outcome-only logging with a final redaction boundary."""

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path


class RedactingFormatter(logging.Formatter):
    def format(self, record):
        text = super().format(record)
        text = re.sub(r"(?i)bearer\s+[^\s,}\"]+", "Bearer [REDACTED]", text)
        text = re.sub(
            r"(?i)(authorization|access_token|refresh_token|cookie|id_token)[\"\x27]?\s*[:=]\s*[\"\x27]?[^\"\x27\s,}]+",
            r"\1=[REDACTED]",
            text,
        )
        text = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[REDACTED]", text)
        return text


def setup_logging(root: Path):
    directory = root / "logs"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / "credusage.log"
    handler = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=2)
    path.chmod(0o600)
    handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(message)s"))
    logger = logging.getLogger("credusage")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    return logger
