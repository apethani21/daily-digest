#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ENV_NAME="daily-digest"

echo "Setting up daily-digest..."

if conda env list | grep -q "^${ENV_NAME} "; then
    echo "Conda environment '${ENV_NAME}' already exists."
else
    conda create -n "$ENV_NAME" python=3.11 -y -q
    echo "Conda environment '${ENV_NAME}' created."
fi

conda run -n "$ENV_NAME" pip install --upgrade pip -q
conda run -n "$ENV_NAME" pip install -r requirements.txt -q
echo "Dependencies installed."

mkdir -p logs
echo "Done."

CONDA_PYTHON="$(conda run -n "$ENV_NAME" python -c 'import sys; print(sys.executable)')"

echo ""
echo "Next steps:"
echo "  1. Edit config.yaml — set your location coordinates"
echo "  2. Copy and fill in .env:  cp .env.example .env && nano .env"
echo "  3. Test dry run:           conda run -n ${ENV_NAME} python main.py --dry-run"
echo "  4. Force a test email:     conda run -n ${ENV_NAME} python main.py --force-email"
echo "  5. Test one check:         conda run -n ${ENV_NAME} python main.py --check holidays --dry-run"
echo ""
echo "Cron job (runs at 07:00 London time — ensure server timezone is Europe/London):"
echo "  0 7 * * * cd $SCRIPT_DIR && $CONDA_PYTHON $SCRIPT_DIR/main.py >> $SCRIPT_DIR/logs/cron.log 2>&1"
