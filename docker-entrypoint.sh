#!/bin/bash
set -e

CMD="${1:-bank}"

case "$CMD" in
    bank)
        echo "=== Running RunAllRates.py (Daily + Web) ==="
        uv run python RunAllRates.py
        ;;
    portal)
        echo "=== Running ImportPortalRate.py ==="
        uv run python ImportPortalRate.py
        ;;
    shell)
        exec /bin/bash
        ;;
    *)
        echo "Usage: docker run <image> {bank|portal|shell}"
        echo ""
        echo "  bank    - RunAllRates.py (每日銀行匯率: CNY, EUR, GBP, CAD, AUD, JPY, PHP, USD, INR, MXN, TRY)"
        echo "  portal  - ImportPortalRate.py (三旬匯率: EUR, JPY, USD, CNY)"
        echo "  shell   - Interactive bash shell"
        exit 1
        ;;
esac
