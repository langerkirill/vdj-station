#!/bin/bash
# Build the release assets of the commit this checkout is on, as the Mac's updater expects them:
#   vdj_autocue-<version>-py3-none-any.whl   the package (pip wheel, nothing uncommitted)
#   vdj-autocue-<version>-tests.tar.gz       its tests and test data (test_bundle.txt)
#   SHA256SUMS
#
#   docs/autocue-lab/autoupdate/build_release_assets.sh <out dir>     # prints the version last
#
# Used by .github/workflows/release.yml on each tag v<version>, by release.py, and by the tests,
# so the assets are always built the same way.
set -euo pipefail
OUT="${1:?usage: build_release_assets.sh <out dir>}"
PY="${PYTHON:-python3}"
ROOT="$(git rev-parse --show-toplevel)"
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd -P)"
cd "$ROOT"
VER="$("$PY" -c 'import re; print(re.search(r"__version__ = \"([^\"]+)\"", open("vdj_cuer/_version.py").read()).group(1))')"
SRC="$(mktemp -d)"
trap 'rm -rf "$SRC"' EXIT
# the wheel from the committed files only
git archive HEAD -- . ':(exclude)bench' | tar -x -C "$SRC"
"$PY" -m pip wheel -q --no-deps -w "$OUT" "$SRC"
[ -f "$OUT/vdj_autocue-$VER-py3-none-any.whl" ] || { echo "no wheel vdj_autocue-$VER-py3-none-any.whl in $OUT" >&2; exit 1; }
# the tests and their data, same commit
paths=()
while IFS= read -r line; do
  case "$line" in ''|'#'*) continue ;; esac
  paths+=("$line")
done < docs/autocue-lab/autoupdate/test_bundle.txt
git archive --format=tar.gz -o "$OUT/vdj-autocue-$VER-tests.tar.gz" HEAD -- "${paths[@]}"
"$PY" - "$OUT" "$VER" <<'PYCODE'
import hashlib, sys
from pathlib import Path
out, ver = Path(sys.argv[1]), sys.argv[2]
names = [f"vdj_autocue-{ver}-py3-none-any.whl", f"vdj-autocue-{ver}-tests.tar.gz"]
lines = [f"{hashlib.sha256((out / n).read_bytes()).hexdigest()}  {n}" for n in names]
(out / "SHA256SUMS").write_text("\n".join(lines) + "\n")
PYCODE
echo "$VER"
