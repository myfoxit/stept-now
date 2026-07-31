"""Export the OpenAPI schema: `python -m app.export_openapi [outfile]`."""

from __future__ import annotations

import json
import sys

from app.main import app


def main() -> None:
    out = sys.argv[1] if len(sys.argv) > 1 else "openapi.json"
    with open(out, "w") as fh:
        json.dump(app.openapi(), fh, indent=2, sort_keys=True)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
