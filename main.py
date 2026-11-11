"""Excelas Scale prospect research and HubSpot dry-run."""
import argparse
import sys
from pathlib import Path



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    research = commands.add_parser("research", help="Build the evidence store and human review queue")
    research.add_argument("--out", type=Path, default=Path("runs/excelas"))
    research.add_argument("--target", type=int, default=5, help="Required number of approved prospects")
    research.add_argument("--seed", type=Path, help="Use a local, already enriched evidence file")
    research.add_argument("--model", help="Override OPENAI_MODEL for an online run")
    research.add_argument("--icp-config", type=Path, help="Reuse a saved ICP search plan")
    handoff = commands.add_parser("handoff", help="Validate approved contacts and preview a create-only HubSpot import")
    handoff.add_argument("--out", type=Path, default=Path("runs/excelas"))
    handoff.add_argument("--review", type=Path, help="Edited CSV; default is OUT/research/review_queue.csv")
    handoff.add_argument("--crm-snapshot", type=Path, help="Local simulated CRM contact snapshot")
    args = parser.parse_args()

    try:
        if args.command == "research":
            from excelas.research import research_run

            if not 1 <= args.target <= 50:
                parser.error("--target must be between 1 and 50")
            research_run(args.out, args.target, args.seed, args.model, args.icp_config)
        else:
            from excelas.handoff import handoff_run

            handoff_run(args.out, args.review, args.crm_snapshot)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
