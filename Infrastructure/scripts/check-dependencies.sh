#!/bin/bash
# Dependency vulnerability scanning script

set -e

echo "=== Checking Python Dependencies ==="
cd "$(dirname "$0")/../automation-service" || exit 1

if command -v safety &> /dev/null; then
    echo "Running safety check on requirements.txt..."
    safety check --file requirements.txt || echo "⚠️  Safety check found vulnerabilities"
else
    echo "⚠️  safety not installed. Install with: pip install safety"
fi

echo ""
echo "=== Checking TypeScript Dependencies ==="
cd "../frontend" || exit 1

if command -v npm &> /dev/null; then
    echo "Running npm audit..."
    npm audit --audit-level=high || echo "⚠️  npm audit found vulnerabilities"
else
    echo "⚠️  npm not found"
fi

echo ""
echo "=== Dependency check complete ==="
