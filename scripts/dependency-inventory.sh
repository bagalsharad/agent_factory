#!/usr/bin/env bash
# Writes a dependency inventory (pinned Python + npm packages) to dist/dependency-inventory/.
# For a CycloneDX SBOM, CI additionally runs anchore/sbom-action on the built image.
set -euo pipefail
cd "$(dirname "$0")/.."
out=dist/dependency-inventory
mkdir -p "$out"
uv export --frozen --all-extras --no-hashes --format requirements-txt > "$out/python-requirements.txt"
(cd web && npm ls --all --json --omit=dev > "../$out/npm-production.json" 2>/dev/null || true)
echo "inventory written to $out"
