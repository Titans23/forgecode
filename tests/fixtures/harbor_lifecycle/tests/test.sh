#!/bin/bash
set -euo pipefail
test -d /logs/verifier
mkdir -p /logs/artifacts
printf 'lifecycle-smoke\n' > /logs/artifacts/smoke.txt
printf '1\n' > /logs/verifier/reward.txt
