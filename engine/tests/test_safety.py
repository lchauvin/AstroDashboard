"""The data root is only ever changed by one module (PLAN.md §2.1).

`ingest/fileops.py` is the single module allowed to move/copy files (and to
remove empty .Download folders on request); it re-checks every path itself.
Everything else stays read-only. The only other file-mutating calls swap
freshly written cache files into place inside var/.
"""

import re
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "astrodash"

FORBIDDEN = re.compile(
    r"\bos\.(remove|unlink|rmdir|removedirs|rename|renames|replace)\b"
    r"|\bshutil\.(rmtree|move|copy\w*)\b"
    r"|\.(unlink|rmdir|rename|write_text|write_bytes|touch)\("
    r"|open\([^)]*['\"](w|a|x|r\+|wb|ab|xb)['\"]"
)
# Whole modules allowed to mutate files (each guards its own paths).
WRITER_MODULES = {"fileops.py"}
WRITE_ALLOWED = {
    # var/thumbs only: `tmp.replace(out)` swaps a freshly written thumbnail into place
    ("api.py", "tmp.replace(out)"),
    # var/previews only
    ("preview.py", "tmp.replace(out)"),
}


def test_no_file_mutation_outside_allowlist():
    offenders = []
    for path in PACKAGE.rglob("*.py"):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if path.name in WRITER_MODULES and path.parent.name == "ingest":
                continue
            if FORBIDDEN.search(code) and not any(path.name == f and snippet in code for f, snippet in WRITE_ALLOWED):
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not offenders, "file-mutating calls found:\n" + "\n".join(offenders)
