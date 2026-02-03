#!/bin/bash
# Quick start script for PolyBot

set -e

echo "🚀 Setting up PolyBot..."

# Create virtual environment
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate
source venv/bin/activate

# Install dependencies
echo "Installing dependencies..."
pip install -e . --quiet

# Create .env if not exists
if [ ! -f ".env" ]; then
    echo "Creating .env from template..."
    cp .env.example .env
    echo "⚠️  Edit .env with your wallet credentials before running!"
fi

# Create directories
mkdir -p logs data

echo ""
echo "✅ Setup complete!"
echo ""
echo "Next steps:"
echo "  1. Edit .env with your Polygon wallet private key"
echo "  2. Fund wallet with USDC on Polygon"
echo "  3. Run: python -m polybot.main --scan  (test mode)"
echo "  4. Run: python -m polybot.main         (live trading)"
echo "  5. Run: python -m polybot.main --dash  (dashboard)"
