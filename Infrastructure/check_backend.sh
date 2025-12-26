#!/bin/bash
# Quick script to check if backend is running

echo "Checking if backend is running on port 8000..."

if curl -s http://127.0.0.1:8000/health > /dev/null 2>&1; then
    echo "✓ Backend is running and responding"
    curl -s http://127.0.0.1:8000/health | python3 -m json.tool 2>/dev/null || echo "Response received"
else
    echo "✗ Backend is NOT running or not accessible"
    echo ""
    echo "To start the backend, run:"
    echo "  sudo systemctl start cea-backend"
    echo ""
    echo "Or manually:"
    echo "  cd '/home/antoine/Project CEA/Infrastructure/backend'"
    echo "  python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000"
fi

