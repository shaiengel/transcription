#!/bin/bash
# Render .env files from templates
# Usage: ./render_env.sh [dev|prod] [--skip-validation]
#
# This script uses uv to run render_env.py with inline dependencies.
# Dependencies are installed in an isolated cache (NOT global environment).

# Change to script directory
cd "$(dirname "$0")"

# Check if uv is available
if ! command -v uv &> /dev/null; then
    echo "Error: uv is not installed or not in PATH"
    echo "Install it from: https://docs.astral.sh/uv/"
    exit 1
fi

# Run the script with uv (handles inline dependencies automatically)
uv run render_env.py "$@"
