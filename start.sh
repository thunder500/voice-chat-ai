#!/bin/bash
set -e

echo "========================================="
echo "  Voice Chat AI - Starting Services"
echo "========================================="
echo ""

# Check Docker is running
if ! docker info > /dev/null 2>&1; then
    echo "Error: Docker is not running. Please start Docker first."
    exit 1
fi

echo "[1/3] Building and starting containers..."
docker compose up -d --build

echo ""
echo "[2/3] Waiting for Ollama to be ready..."
until curl -s http://localhost:11434/api/tags > /dev/null 2>&1; do
    sleep 2
    echo "  Waiting..."
done
echo "  Ollama is ready!"

echo ""
echo "[3/3] Pulling LLM model (llama3.2)... This may take a few minutes on first run."
docker compose exec -T ollama ollama pull llama3.2

echo ""
echo "========================================="
echo "  Voice Chat AI is ready!"
echo "  Open: http://localhost:8000"
echo "========================================="
