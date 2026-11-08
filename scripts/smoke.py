"""Open the packaged app, wait for its window to load, and check it closed cleanly (used by the GitHub build)."""

import glob
import os
import subprocess
import sys
import time

import platformdirs

cmd = sys.argv[1:]
logs = os.path.join(platformdirs.user_data_dir("Rennix", appauthor=False, roaming=False), "logs")
start = time.time()
env = dict(os.environ, RENNIX_SMOKE="1")
try:
    code = subprocess.run(cmd, env=env, timeout=150).returncode
except subprocess.TimeoutExpired:
    code = "timeout"
text = ""
for f in sorted(glob.glob(os.path.join(logs, "*.log*"))):
    if os.path.getmtime(f) >= start - 5:
        text += open(f, encoding="utf-8", errors="replace").read()
print(text[-4000:] or f"(no log found in {logs})")
if code != 0 or "Window ready" not in text:
    sys.exit(f"Smoke test failed: exit code {code}, window ready: {'Window ready' in text}")
print("Smoke test passed: the window opened and closed cleanly.")
