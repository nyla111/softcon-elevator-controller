#!/usr/bin/env bash
# Creates labels and one issue per row of requirements.csv.
# Needs the GitHub CLI logged in (gh auth login). Run from the repo root:
#   bash scripts/create_issues.sh
set -euo pipefail

declare -A PART_NAME=([1]="part-1-simulator" [2]="part-2-dispatch" [3]="part-3-access"
                      [4]="part-4-safety" [5]="part-5-priority" [6]="part-6-monitor")

gh label create requirement --color 0E8A16 --force >/dev/null
gh label create contract --color B60205 --force >/dev/null
for n in 1 2 3 4 5 6; do
  gh label create "${PART_NAME[$n]}" --color 1D76DB --force >/dev/null
done

tail -n +2 "$(dirname "$0")/requirements.csv" | while IFS=, read -r part id title; do
  gh issue create --title "$id $title" --label requirement --label "${PART_NAME[$part]}" \
    --body "Part $part. See SRS v0.13 for $id. Close via PR titled \"$id ...\"." >/dev/null
  echo "created: $id"
done
