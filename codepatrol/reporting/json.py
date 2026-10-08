"""Machine-readable deterministic report rendering."""

import json

from codepatrol.scanner.models import ScanResult


def render_json_report(result: ScanResult) -> str:
    """Return exactly one JSON document with stable key and item ordering."""
    return json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
