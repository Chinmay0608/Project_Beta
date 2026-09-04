"""GCC Job Radar - Tracker for entry-level tech roles in India at foreign GCCs."""

import os
from pathlib import Path

__version__ = "0.1.0"


def _load_env_file() -> None:
    """Lightweight zero-dependency .env loader."""
    env_paths = [Path(".env"), Path(__file__).resolve().parent.parent / ".env"]
    for path in env_paths:
        if path.is_file():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        key, val = line.split("=", 1)
                        key = key.strip()
                        val = val.strip().strip("'\"")
                        if key and key not in os.environ and val:
                            os.environ[key] = val
            except Exception:
                pass
            break


_load_env_file()
