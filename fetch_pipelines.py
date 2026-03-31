"""
Fetch pipeline / build metrics from Azure DevOps:
  - Pipeline definitions (list)
  - Build runs: success rate, duration trends, failure breakdown
  - Per-developer build trigger stats
  - Deployment frequency (pipeline runs per day)
"""
import logging
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from typing import Any

import ado_client
import config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _lookback_date() -> datetime:
    return _utcnow() - timedelta(days=config.LOOKBACK_DAYS)


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    return datetime.fromisoformat(s)


def _duration_seconds(b: dict) -> float | None:
    start = _parse_dt(b.get("startTime"))
    finish = _parse_dt(b.get("finishTime"))
    if start and finish:
        return (finish - start).total_seconds()
    return None


# ---------------------------------------------------------------------------
# Pipeline definitions
# ---------------------------------------------------------------------------

def fetch_pipeline_definitions() -> list[dict]:
    """Return all build pipeline definitions."""
    logger.info("Fetching pipeline definitions…")
    data = ado_client.get("build/definitions", params={"includeAllProperties": "false"})
    defs = data.get("value", [])
    logger.info("  Found %d pipeline definitions", len(defs))
    return defs


# ---------------------------------------------------------------------------
# Build runs
# ---------------------------------------------------------------------------

def fetch_builds(definition_id: int | None = None) -> list[dict]:
    """
    Fetch build runs within LOOKBACK_DAYS.
    If definition_id is given, fetch only that pipeline's builds.
    """
    since = _lookback_date().strftime("%Y-%m-%dT%H:%M:%SZ")
    params: dict = {"minTime": since, "statusFilter": "completed"}
    if definition_id:
        params["definitions"] = str(definition_id)

    builds = list(ado_client.get_paged("build/builds", params=params))
    logger.info(
        "  Fetched %d completed builds%s",
        len(builds),
        f" for definition {definition_id}" if definition_id else "",
    )
    return builds


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def aggregate_builds_by_pipeline(builds: list[dict]) -> list[dict]:
    """
    Per-pipeline summary:
      - total runs, successes, failures, partial failures
      - success rate
      - avg / min / max duration (seconds)
      - runs per day (deployment frequency)
    """
    by_pipeline: dict[int, dict] = defaultdict(lambda: {
        "runs": 0,
        "succeeded": 0,
        "failed": 0,
        "partiallySucceeded": 0,
        "canceled": 0,
        "durations_s": [],
        "dates": [],
    })

    for b in builds:
        defn = b.get("definition", {})
        pid = defn.get("id", 0)
        pname = defn.get("name", f"pipeline-{pid}")
        by_pipeline[pid]["name"] = pname
        by_pipeline[pid]["id"] = pid
        by_pipeline[pid]["runs"] += 1

        result = b.get("result", "")
        if result in ("succeeded", "failed", "partiallySucceeded", "canceled"):
            by_pipeline[pid][result] = by_pipeline[pid].get(result, 0) + 1

        dur = _duration_seconds(b)
        if dur is not None:
            by_pipeline[pid]["durations_s"].append(dur)

        finish = _parse_dt(b.get("finishTime"))
        if finish:
            by_pipeline[pid]["dates"].append(finish.date().isoformat())

    results = []
    for _pid, info in by_pipeline.items():
        runs = info["runs"]
        succeeded = info.get("succeeded", 0)
        durations = info["durations_s"]
        unique_days = len(set(info["dates"]))
        results.append({
            "pipeline_id": info["id"],
            "pipeline_name": info["name"],
            "total_runs": runs,
            "succeeded": succeeded,
            "failed": info.get("failed", 0),
            "partially_succeeded": info.get("partiallySucceeded", 0),
            "canceled": info.get("canceled", 0),
            "success_rate_pct": round(succeeded / runs * 100, 1) if runs else 0,
            "avg_duration_s": round(sum(durations) / len(durations)) if durations else None,
            "min_duration_s": round(min(durations)) if durations else None,
            "max_duration_s": round(max(durations)) if durations else None,
            "active_days": unique_days,
            "runs_per_day": round(runs / config.LOOKBACK_DAYS, 2),
        })

    return sorted(results, key=lambda x: x["total_runs"], reverse=True)


def aggregate_builds_by_developer(builds: list[dict]) -> list[dict]:
    """
    Per-developer build trigger stats:
      - how many builds triggered (manual or via their commits)
      - success / failure breakdown
    """
    by_dev: dict[str, dict] = defaultdict(lambda: {
        "runs": 0,
        "succeeded": 0,
        "failed": 0,
        "partially_succeeded": 0,
    })

    for b in builds:
        # requestedFor is the person who triggered or whose commit triggered the build
        req = b.get("requestedFor") or b.get("requestedBy") or {}
        name = req.get("displayName") or req.get("uniqueName", "Unknown")
        by_dev[name]["display_name"] = name
        by_dev[name]["unique_name"] = req.get("uniqueName", name)
        by_dev[name]["runs"] += 1
        result = b.get("result", "")
        if result == "succeeded":
            by_dev[name]["succeeded"] += 1
        elif result == "failed":
            by_dev[name]["failed"] += 1
        elif result == "partiallySucceeded":
            by_dev[name]["partially_succeeded"] += 1

    results = []
    for _name, info in by_dev.items():
        runs = info["runs"]
        succeeded = info["succeeded"]
        results.append({
            "developer": info["display_name"],
            "unique_name": info["unique_name"],
            "total_builds_triggered": runs,
            "succeeded": succeeded,
            "failed": info["failed"],
            "partially_succeeded": info["partially_succeeded"],
            "success_rate_pct": round(succeeded / runs * 100, 1) if runs else 0,
        })

    return sorted(results, key=lambda x: x["total_builds_triggered"], reverse=True)


def aggregate_build_trend(builds: list[dict]) -> list[dict]:
    """
    Daily build outcome trend — useful for charting over time.
    Returns one row per day with counts of succeeded/failed builds.
    """
    by_day: dict[str, dict] = defaultdict(lambda: {"succeeded": 0, "failed": 0, "total": 0})

    for b in builds:
        finish = _parse_dt(b.get("finishTime"))
        if not finish:
            continue
        day = finish.date().isoformat()
        by_day[day]["total"] += 1
        result = b.get("result", "")
        if result == "succeeded":
            by_day[day]["succeeded"] += 1
        elif result in ("failed", "partiallySucceeded"):
            by_day[day]["failed"] += 1

    return [
        {"date": day, **counts}
        for day, counts in sorted(by_day.items())
    ]


def aggregate_failure_reasons(builds: list[dict]) -> list[dict]:
    """
    Count failed builds grouped by pipeline + failure reason / branch.
    """
    failures: dict[str, dict] = defaultdict(lambda: {"count": 0, "branches": defaultdict(int)})

    for b in builds:
        if b.get("result") not in ("failed", "partiallySucceeded"):
            continue
        pname = b.get("definition", {}).get("name", "unknown")
        branch = b.get("sourceBranch", "unknown").replace("refs/heads/", "")
        failures[pname]["pipeline"] = pname
        failures[pname]["count"] += 1
        failures[pname]["branches"][branch] += 1

    results = []
    for pname, info in failures.items():
        results.append({
            "pipeline": info["pipeline"],
            "failure_count": info["count"],
            "by_branch": dict(info["branches"]),
        })
    return sorted(results, key=lambda x: x["failure_count"], reverse=True)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run() -> dict[str, Any]:
    """Fetch all pipeline/build metrics and return structured results."""
    definitions = fetch_pipeline_definitions()
    builds = fetch_builds()  # fetch all pipelines at once (more efficient)

    return {
        "lookback_days": config.LOOKBACK_DAYS,
        "pipeline_definitions": [
            {"id": d["id"], "name": d["name"], "path": d.get("path", "")}
            for d in definitions
        ],
        "by_pipeline": aggregate_builds_by_pipeline(builds),
        "by_developer": aggregate_builds_by_developer(builds),
        "daily_trend": aggregate_build_trend(builds),
        "failure_reasons": aggregate_failure_reasons(builds),
    }


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    result = run()
    print(json.dumps(result, indent=2, default=str))
