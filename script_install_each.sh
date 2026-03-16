#!/bin/bash
set -euo pipefail
REQ="requirements.txt"
OUT="results.csv"
echo "package,installed,output" > $OUT
while IFS= read -r line || [ -n "$line" ]; do
  # ignore comments/empty lines
  if [[ -z "$line" || "$line" =~ ^# ]]; then
    continue
  fi
  pkg="$line"
  echo "=== Installing: $pkg ==="
  # try install; capture stdout+stderr
  output=$(python -m pip install "$pkg" 2>&1) || rc=$?
  rc=${rc:-0}
  if [ "$rc" -eq 0 ]; then
    echo "\"$pkg\",installed,\"$(echo "$output" | tr '\n' ' ' )\"" >> $OUT
  else
    echo "\"$pkg\",failed,\"$(echo "$output" | tr '\n' ' ' )\"" >> $OUT
    # reset rc for next loop
    rc=0
  fi
done < "$REQ"
echo "Finished. Results in $OUT"
