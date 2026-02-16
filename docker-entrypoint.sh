#!/bin/bash
set -e

CMD="${1:-daily}"

case "$CMD" in
    daily)
        echo "=== Running ImportDailyRate.py ==="
        uv run python ImportDailyRate.py
        ;;
    web)
        echo "=== Running ImportWebRate.py ==="
        uv run python ImportWebRate.py
        ;;
    portal)
        echo "=== Running ImportPortalRate.py ==="
        uv run python ImportPortalRate.py
        ;;
    all)
        echo "=== Running all scripts sequentially ==="
        echo "--- [1/3] ImportDailyRate.py ---"
        uv run python ImportDailyRate.py
        echo "--- [2/3] ImportWebRate.py ---"
        uv run python ImportWebRate.py
        echo "--- [3/3] ImportPortalRate.py ---"
        uv run python ImportPortalRate.py
        echo "=== All scripts completed ==="
        ;;
    shell)
        exec /bin/bash
        ;;
    *)
        echo "Usage: docker run <image> {daily|web|portal|all|shell}"
        echo ""
        echo "  daily   - ImportDailyRate.py  (CNY, EUR, GBP, CAD, AUD, JPY, PHP, USD)"
        echo "  web     - ImportWebRate.py    (INR, MXN, TRY)"
        echo "  portal  - ImportPortalRate.py (EUR, JPY, USD from customs portal)"
        echo "  all     - Run all 3 scripts sequentially"
        echo "  shell   - Interactive bash shell"
        exit 1
        ;;
esac
