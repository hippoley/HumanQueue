import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault(
    "HUMAN_QUEUE_HOME",
    tempfile.mkdtemp(prefix="humanqueue-tests-"),
)
os.environ.setdefault("HUMAN_QUEUE_DISABLE_BACKGROUND_WORKERS", "1")
