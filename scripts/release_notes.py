"""Write each version's section of CHANGELOG.md to dist/notes/<version>.md (used by the GitHub build)."""

import re
import sys
from pathlib import Path

FOOTER = ("\n\n---\n**Download:** `Rennix-Setup-…exe` installs for your user (no admin needed) and updates over "
          "the top, keeping your settings. The portable `.zip` runs from anywhere.\n\n"
          "Full history: [CHANGELOG.md](https://github.com/FiTH-g1gGLe/Rennix/blob/main/CHANGELOG.md)\n")

out = Path(sys.argv[1] if len(sys.argv) > 1 else "dist/notes")
out.mkdir(parents=True, exist_ok=True)
text = Path("CHANGELOG.md").read_text(encoding="utf-8")
for part in re.split(r"^## ", text, flags=re.M)[1:]:
    head, body = part.split("\n", 1)
    version = head.split(" ")[0]
    if re.match(r"\d+\.\d+\.\d+", version):
        (out / f"{version}.md").write_text(body.strip() + FOOTER, encoding="utf-8")
        print("notes for", version)
