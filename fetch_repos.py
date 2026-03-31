"""
Fetch repository-related metrics from Azure DevOps:
  - List of repositories
  - Commits per author per repo (within LOOKBACK_DAYS)
  - Pull requests: count, cycle time, review time, reviewer participation
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
    # ADO uses ISO-8601 with Z or +00:00
    s = s.replace("Z", "+00:00")
    return datetime.fromisoformat(s)


# ---------------------------------------------------------------------------
# Repositories
# ---------------------------------------------------------------------------

def fetch_repositories() -> list[dict]:
    """Return all git repositories in the project."""
    logger.info("Fetching repositories…")
    data = ado_client.get("git/repositories")
    repos = data.get("value", [])
    logger.info("  Found %d repositories", len(repos))
    return repos


# ---------------------------------------------------------------------------
# Commits
# ---------------------------------------------------------------------------

def fetch_commits(repo_id: str, repo_name: str) -> list[dict]:
    """Return raw commit records for a repo within LOOKBACK_DAYS."""
    since = _lookback_date().strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {
        "searchCriteria.fromDate": since,
        "searchCriteria.includeUserImageUrl": "false",
    }
    commits = list(ado_client.get_paged(
        f"git/repositories/{repo_id}/commits", params=params
    ))
    logger.info("  [%s] %d commits in last %d days", repo_name, len(commits), config.LOOKBACK_DAYS)
    return commits


def aggregate_commits(commits: list[dict], repo_name: str) -> list[dict]:
    """Summarise commit counts per author."""
    by_author: dict[str, dict] = defaultdict(lambda: {"commits": 0, "dates": []})
    for c in commits:
        author_name = c.get("author", {}).get("name") or c.get("committer", {}).get("name", "Unknown")
        author_email = c.get("author", {}).get("email") or c.get("committer", {}).get("email", "")
        key = author_email or author_name
        by_author[key]["name"] = author_name
        by_author[key]["email"] = author_email
        by_author[key]["commits"] += 1
        dt_str = c.get("author", {}).get("date") or c.get("committer", {}).get("date")
        by_author[key]["dates"].append(dt_str)

    result = []
    for _email, info in by_author.items():
        dates = sorted(filter(None, info["dates"]))
        result.append({
            "repo": repo_name,
            "author_name": info["name"],
            "author_email": info["email"],
            "commit_count": info["commits"],
            "first_commit": dates[0] if dates else None,
            "last_commit": dates[-1] if dates else None,
        })
    return sorted(result, key=lambda x: x["commit_count"], reverse=True)


# ---------------------------------------------------------------------------
# Pull Requests
# ---------------------------------------------------------------------------

_PR_STATUSES = ["active", "completed", "abandoned"]


def fetch_pull_requests(repo_id: str, repo_name: str) -> list[dict]:
    """Fetch all PRs (active + recently completed/abandoned) for a repo."""
    since = _lookback_date()
    all_prs: list[dict] = []

    for status in _PR_STATUSES:
        params = {"searchCriteria.status": status}
        prs = list(ado_client.get_paged(
            f"git/repositories/{repo_id}/pullrequests", params=params
        ))
        # Filter by creation date for completed/abandoned
        if status != "active":
            prs = [
                pr for pr in prs
                if _parse_dt(pr.get("creationDate")) and _parse_dt(pr.get("creationDate")) >= since
            ]
        all_prs.extend(prs)

    logger.info("  [%s] %d PRs (active/completed/abandoned)", repo_name, len(all_prs))
    return all_prs


def aggregate_pull_requests(prs: list[dict], repo_name: str) -> dict[str, Any]:
    """
    Returns:
      - summary counts
      - per-author stats (PRs created, avg cycle time)
      - reviewer participation (reviews given per person)
      - cycle time distribution (open→merge in hours)
    """
    counts = {"active": 0, "completed": 0, "abandoned": 0}
    by_author: dict[str, dict] = defaultdict(lambda: {
        "prs_created": 0,
        "prs_merged": 0,
        "cycle_times_h": [],
    })
    reviewer_counts: dict[str, int] = defaultdict(int)
    cycle_times_h: list[float] = []

    for pr in prs:
        status = pr.get("status", "unknown")
        counts[status] = counts.get(status, 0) + 1

        creator = pr.get("createdBy", {})
        author_key = creator.get("uniqueName") or creator.get("displayName", "Unknown")
        by_author[author_key]["display_name"] = creator.get("displayName", author_key)
        by_author[author_key]["prs_created"] += 1

        # Cycle time for merged PRs
        if status == "completed":
            created = _parse_dt(pr.get("creationDate"))
            closed = _parse_dt(pr.get("closedDate"))
            if created and closed:
                hours = (closed - created).total_seconds() / 3600
                cycle_times_h.append(hours)
                by_author[author_key]["prs_merged"] += 1
                by_author[author_key]["cycle_times_h"].append(hours)

        # Reviewer participation
        for reviewer in pr.get("reviewers", []):
            rv_key = reviewer.get("uniqueName") or reviewer.get("displayName", "Unknown")
            vote = reviewer.get("vote", 0)
            # vote != 0 means they actually voted (approved, rejected, etc.)
            if vote != 0:
                reviewer_counts[rv_key] += 1

    # Build per-author summary
    author_stats = []
    for key, info in by_author.items():
        ct = info["cycle_times_h"]
        author_stats.append({
            "repo": repo_name,
            "author": info.get("display_name", key),
            "unique_name": key,
            "prs_created": info["prs_created"],
            "prs_merged": info["prs_merged"],
            "avg_cycle_time_h": round(sum(ct) / len(ct), 1) if ct else None,
            "min_cycle_time_h": round(min(ct), 1) if ct else None,
            "max_cycle_time_h": round(max(ct), 1) if ct else None,
        })

    # Reviewer summary
    reviewer_stats = [
        {"repo": repo_name, "reviewer": k, "reviews_given": v}
        for k, v in reviewer_counts.items()
    ]

    avg_cycle = round(sum(cycle_times_h) / len(cycle_times_h), 1) if cycle_times_h else None

    return {
        "repo": repo_name,
        "pr_counts": counts,
        "avg_cycle_time_h": avg_cycle,
        "author_stats": sorted(author_stats, key=lambda x: x["prs_created"], reverse=True),
        "reviewer_stats": sorted(reviewer_stats, key=lambda x: x["reviews_given"], reverse=True),
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run() -> dict[str, Any]:
    """Fetch all repo metrics and return structured results."""
    repos = fetch_repositories()

    all_commit_stats: list[dict] = []
    all_pr_stats: list[dict] = []

    for repo in repos:
        rid = repo["id"]
        rname = repo["name"]

        # --- commits ---
        try:
            commits = fetch_commits(rid, rname)
            all_commit_stats.extend(aggregate_commits(commits, rname))
        except Exception as exc:
            logger.error("  Error fetching commits for %s: %s", rname, exc)

        # --- pull requests ---
        try:
            prs = fetch_pull_requests(rid, rname)
            all_pr_stats.append(aggregate_pull_requests(prs, rname))
        except Exception as exc:
            logger.error("  Error fetching PRs for %s: %s", rname, exc)

    return {
        "lookback_days": config.LOOKBACK_DAYS,
        "repositories": [{"id": r["id"], "name": r["name"], "default_branch": r.get("defaultBranch")} for r in repos],
        "commit_stats": all_commit_stats,
        "pr_stats": all_pr_stats,
    }


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    result = run()
    print(json.dumps(result, indent=2, default=str))
