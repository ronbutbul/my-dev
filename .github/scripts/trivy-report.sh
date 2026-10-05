#!/usr/bin/env bash
# Usage: trivy-report.sh <trivy-json-report> <title>
#
# Converts a Trivy JSON report into .txt (table), .html and .sarif files next to it,
# and appends a severity summary to the GitHub Actions job summary.
# Requires `trivy` on PATH (installed by aquasecurity/trivy-action) and `jq`.
set -euo pipefail

json="$1"
title="$2"
base="${json%.json}"
html_template="$(dirname "$(command -v trivy)")/contrib/html.tpl"

trivy convert --format table --table-mode detailed --output "${base}.txt" "$json"
trivy convert --format sarif --output "${base}.sarif" "$json"
trivy convert --format template --template "@${html_template}" --output "${base}.html" "$json"

count() {
  jq --arg sev "$2" "[.Results[]?.$1[]? | select(.Severity == \$sev)] | length" "$json"
}

{
  echo "## ${title}"
  echo
  echo "| Severity | Vulnerabilities | Secrets |"
  echo "|---|---|---|"
  for sev in CRITICAL HIGH MEDIUM LOW UNKNOWN; do
    echo "| ${sev} | $(count Vulnerabilities "$sev") | $(count Secrets "$sev") |"
  done
  echo
  echo "<details><summary>CRITICAL and HIGH findings</summary>"
  echo
  echo '```'
  trivy convert --format table --table-mode detailed --severity CRITICAL,HIGH "$json"
  echo '```'
  echo "</details>"
  echo
} >> "${GITHUB_STEP_SUMMARY:-/dev/stdout}"
