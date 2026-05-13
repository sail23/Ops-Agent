"""命令行：与 API 共用 `execute_incident_run`。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from power_aiops.api.schemas import IncidentRunRequest
from power_aiops.run_incident import demo_request, execute_incident_run


def _print_result(data: dict[str, Any], *, pretty: bool) -> None:
    if pretty:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(data, ensure_ascii=False, separators=(",", ":")))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="power-aiops", description="Power AIOps agents CLI")
    parser.add_argument("--version", action="store_true", help="print version and exit")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="run orchestration pipeline (same as POST /incidents/run)")
    run.add_argument("--demo", action="store_true", help="use built-in demo incident (same as POST /incidents/demo)")
    run.add_argument(
        "--json",
        type=Path,
        metavar="PATH",
        dest="json_path",
        help="path to JSON file (IncidentRunRequest shape)",
    )
    run.add_argument(
        "--pretty",
        action="store_true",
        help="pretty-print JSON to stdout",
    )

    args = parser.parse_args(argv)

    if args.version:
        from power_aiops import __version__

        print(__version__)
        return 0

    if args.command == "run":
        if args.demo and args.json_path:
            print("error: use either --demo or --json, not both", file=sys.stderr)
            return 2
        if args.demo:
            req = demo_request()
        elif args.json_path:
            raw = args.json_path.read_text(encoding="utf-8")
            payload = json.loads(raw)
            req = IncidentRunRequest.model_validate(payload)
        else:
            run.print_help()
            print("\nerror: specify --demo or --json PATH", file=sys.stderr)
            return 2

        resp = execute_incident_run(req)
        _print_result(resp.model_dump(mode="json"), pretty=args.pretty)
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
