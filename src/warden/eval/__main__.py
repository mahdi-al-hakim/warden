"""Run the reliability evaluation from the command line.

    python -m warden.eval            # readable summary, exit 1 if anything failed
    python -m warden.eval --json     # full report as JSON
    python -m warden.eval --trials 50
"""

from __future__ import annotations

import argparse
import sys

from .format import format_report
from .harness import run_evaluation


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="warden.eval", description="Warden reliability evaluation"
    )
    parser.add_argument("--json", action="store_true", help="print the full report as JSON")
    parser.add_argument(
        "--trials", type=int, default=25, help="benchmark trials per enforcement point"
    )
    args = parser.parse_args(argv)

    report = run_evaluation(benchmark_trials=args.trials)
    if args.json:
        print(report.model_dump_json(indent=2))
    else:
        print(format_report(report))
    return 0 if report.all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
