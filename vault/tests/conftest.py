import os
import sys
from pathlib import Path

# Ensure root is importable as package base (so `import vault...` works)
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Make Qt operate without a display during tests
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
