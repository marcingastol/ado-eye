"""
Main entry point — fetches all ADO metrics and writes results to output/.

Usage:
    python fetch_all.py                    # uses config.py defaults / env vars
    python fetch_all.py --days 14          # override lookback window
    python fetch_all.py --out results/     # override output directory
    python fetch_all.py --format csv       # also write CSV files (default: json)

Output files (in OUTPUT_DIR):
    repos.json          — repository list + commit stats + PR stats
    pipelines.json      — pipeline/build stats
    summary.json        — cross-cutting developer summary

Environment variables (override config.py):
    ADO_ORG, ADO_PROJECT, ADO_PAT, ADO_LOOKBACK_DAYS, ADO_OUTPUT_DIR
"""
import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---- make sure we can import config even when run from a different cwd ----
sys.path.insert(0, str(Path(__file__).parent))

import config  # noqa: E402

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch Azure DevOps team metrics")
    parser.add_argument("--days", type=int, help="Lookback window in days (overrides config)")
    parser.add_argument("--out", type=str, help="Output directory (overrides config)")
    parser.add_argument(
        "--format", choices=["json", "csv", "both"], default="json",
        help="Output format (default: json)"
    )
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Developer summary — merges commit + PR + pipeline data by developer name
# ---------------------------------------------------------------------------

def _build_developer_summary(
    repo_data: dict,
    pipeline_data: dict,
) -> list[dict]:
    """
    Produce one row per developer with aggregated metrics across all repos/pipelines.
    """
    devs: dict[str, dict] = defaultdict(lambda: {
        "commits": 0,
        "prs_created": 0,
        "prs_merged": 0,
        "pr_cycle_times_h": [],
        "reviews_given": 0,
        "builds_triggered": 0,
        "builds_succeeded": 0,
        "builds_failed": 0,
    })

    # Commits
    for cs in repo_data.get("commit_stats", []):
        key = cs["author_email"] or cs["author_name"]
        devs[key]["display_name"] = cs["author_name"]
        devs[key]["email"] = cs["author_email"]
        devs[key]["commits"] += cs["commit_count"]

    # PRs (author side)
    for pr_repo in repo_data.get("pr_stats", []):
        for a in pr_repo.get("author_stats", []):
            key = a["unique_name"]
            devs[key].setdefault("display_name", a["author"])
            devs[key]["prs_created"] += a["prs_created"]
            devs[key]["prs_merged"] += a["prs_merged"]
            ct = a.get("avg_cycle_time_h")
            if ct is not None:
                devs[key]["pr_cycle_times_h"].append(ct)

        # Reviews given
        for r in pr_repo.get("reviewer_stats", []):
            key = r["reviewer"]
            devs[key].setdefault("display_name", key)
            devs[key]["reviews_given"] += r["reviews_given"]

    # Builds
    for bd in pipeline_data.get("by_developer", []):
        key = bd["unique_name"]
        devs[key].setdefault("display_name", bd["developer"])
        devs[key]["builds_triggered"] += bd["total_builds_triggered"]
        devs[key]["builds_succeeded"] += bd["succeeded"]
        devs[key]["builds_failed"] += bd["failed"]

    result = []
    for key, info in devs.items():
        cts = info["pr_cycle_times_h"]
        total_builds = info["builds_triggered"]
        result.append({
            "developer": info.get("display_name", key),
            "identifier": key,
            "commits": info["commits"],
            "prs_created": info["prs_created"],
            "prs_merged": info["prs_merged"],
            "avg_pr_cycle_time_h": round(sum(cts) / len(cts), 1) if cts else None,
            "reviews_given": info["reviews_given"],
            "builds_triggered": total_builds,
            "build_success_rate_pct": (
                round(info["builds_succeeded"] / total_builds * 100, 1) if total_builds else None
            ),
        })

    return sorted(result, key=lambda x: x["commits"], reverse=True)


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

def _write_json(data: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    logger.info("Wrote %s", path)


def _write_csv(rows: list[dict], path: Path) -> None:
    if not rows:
        return
    import csv
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    logger.info("Wrote %s", path)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    args = _parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%H:%M:%S",
    )

    # Apply CLI overrides
    if args.days:
        config.LOOKBACK_DAYS = args.days
    if args.out:
        config.OUTPUT_DIR = args.out

    out_dir = Path(config.OUTPUT_DIR)

    # Validate config
    if config.ADO_PAT in ("", "your-pat-token"):
        logger.error(
            "ADO_PAT is not set. "
            "Set the ADO_PAT environment variable or edit config.py."
        )
        sys.exit(1)
    if config.ADO_ORG == "your-organization":
        logger.error("ADO_ORG is not set.")
        sys.exit(1)
    if config.ADO_PROJECT == "your-project":
        logger.error("ADO_PROJECT is not set.")
        sys.exit(1)

    logger.info(
        "Fetching ADO metrics | org=%s project=%s lookback=%d days",
        config.ADO_ORG,
        config.ADO_PROJECT,
        config.LOOKBACK_DAYS,
    )

    # --- fetch ---
    import fetch_repos
    import fetch_pipelines

    logger.info("=== REPOSITORIES ===")
    repo_data = fetch_repos.run()

    logger.info("=== PIPELINES ===")
    pipeline_data = fetch_pipelines.run()

    # --- cross-cutting summary ---
    logger.info("=== BUILDING DEVELOPER SUMMARY ===")
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "lookback_days": config.LOOKBACK_DAYS,
        "org": config.ADO_ORG,
        "project": config.ADO_PROJECT,
        "developer_summary": _build_developer_summary(repo_data, pipeline_data),
    }

    # --- write output ---
    fmt = args.format

    if fmt in ("json", "both"):
        _write_json(repo_data, out_dir / "repos.json")
        _write_json(pipeline_data, out_dir / "pipelines.json")
        _write_json(summary, out_dir / "summary.json")

    if fmt in ("csv", "both"):
        _write_csv(repo_data["commit_stats"], out_dir / "commit_stats.csv")
        _write_csv(summary["developer_summary"], out_dir / "developer_summary.csv")
        _write_csv(pipeline_data["by_pipeline"], out_dir / "pipelines_by_pipeline.csv")
        _write_csv(pipeline_data["by_developer"], out_dir / "pipelines_by_developer.csv")
        _write_csv(pipeline_data["daily_trend"], out_dir / "build_trend.csv")

    logger.info("Done. Results in %s/", out_dir.resolve())


if __name__ == "__main__":
    main()
