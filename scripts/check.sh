#!/usr/bin/env bash
# Syntax check all Python sources in the project.
set -euo pipefail
cd "$(dirname "$0")/.."

errors=0
while IFS= read -r -d '' pyfile; do
    if ! python3 -c "import ast; ast.parse(open('$pyfile').read())" 2>/dev/null; then
        echo "❌ SYNTAX ERROR: $pyfile"
        errors=$((errors + 1))
    fi
done < <(find src -name '*.py' -print0)

if [ "$errors" -eq 0 ]; then
    echo "✅ All Python files OK"
else
    echo "❌ $errors file(s) with syntax errors"
    exit 1
fi
