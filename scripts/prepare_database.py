"""Prepare the small runtime additions used by the FastAPI service."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi_app.db import ensure_runtime_schema


if __name__ == "__main__":
    ensure_runtime_schema()
    print("Runtime schema is ready.")
