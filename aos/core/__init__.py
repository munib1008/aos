"""AOS core (TCCM): compute capacity, routing, budget, cache, benchmark. Standard library only.
Reuses scripts/aos_lib.py; importing this package makes it importable."""
import sys
from pathlib import Path

_SCRIPTS = str(Path(__file__).resolve().parent.parent / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
