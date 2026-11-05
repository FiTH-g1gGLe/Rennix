import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Tests use Rennix's own parser so results are the same on every machine.
# test_parse.py has a separate smoke test for the guessit path when it's installed.
os.environ.setdefault("RENNIX_PARSER", "builtin")
