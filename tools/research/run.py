"""Small CLI for a bounded research plan; no synthesis or LLM call occurs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tools.artifacts import RunArtifactRecorder
from tools.research import ResearchPlan, ResearchPlanError, ResearchWorkflow
from tools.search import create_default_search_service
from tools.web import PublicPageFetcher


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a bounded source research plan")
    parser.add_argument("plan", type=Path, help="JSON research-plan file")
    parser.add_argument(
        "--target",
        required=True,
        type=Path,
        help="Explicit external target project used to group Factory artifacts",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    factory_root = Path(__file__).resolve().parents[2]
    try:
        raw_plan = json.loads(arguments.plan.read_text(encoding="utf-8-sig"))
        plan = ResearchPlan.from_dict(raw_plan)
        with RunArtifactRecorder(factory_root, arguments.target) as recorder:
            workflow = ResearchWorkflow(
                create_default_search_service(),
                PublicPageFetcher(),
                recorder=recorder,
            )
            bundle = workflow.run(plan)
            print(json.dumps(bundle.to_dict(), indent=2, sort_keys=True))
        return 0
    except (OSError, UnicodeError, json.JSONDecodeError, ResearchPlanError, ValueError) as exc:
        print(f"Research failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
