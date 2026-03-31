# ADO Stats — Azure DevOps Team Metrics Fetcher

Python scripts that pull data from the Azure DevOps REST API and produce JSON/CSV reports on how your team is performing across repositories and pipelines.

---

## What is collected

### Repository metrics (`repos.json`)
| Metric | Description |
|---|---|
| Commit count per developer | Number of commits per author per repository within the lookback window |
| PR counts | Active / completed / abandoned pull requests per repo |
| PR cycle time | Hours from PR creation to merge (avg / min / max) |
| Reviewer participation | Number of votes cast per reviewer across all PRs |

### Pipeline / Build metrics (`pipelines.json`)
| Metric | Description |
|---|---|
| Success rate | % of completed builds that succeeded, per pipeline |
| Build duration | Average / min / max build time in seconds |
| Deployment frequency | How many builds run per day per pipeline |
| Per-developer build triggers | Which developer triggered how many builds and with what outcome |
| Daily trend | Day-by-day succeeded vs. failed build counts (for charting) |
| Failure breakdown | Failed build count grouped by pipeline and branch |

### Developer summary (`summary.json` / `developer_summary.csv`)
One row per developer, aggregating all of the above:
- Commits, PRs created, PRs merged, avg PR cycle time
- Reviews given to others
- Total builds triggered and build success rate

---

## Prerequisites

- Python 3.11+
- An Azure DevOps **Personal Access Token (PAT)**

### Required PAT scopes
Generate your token at `https://dev.azure.com/{your-org}/_usersSettings/tokens` with:
- **Code** → Read
- **Build** → Read

---

## Installation

```powershell
cd "c:\Users\Marcin\IT\ADO stats"
pip install -r requirements.txt
```

---

## Configuration

You can configure the scripts either via **environment variables** (recommended) or by editing `config.py` directly.

### Environment variables

| Variable | Required | Description | Example |
|---|---|---|---|
| `ADO_ORG` | Yes | Organization name from `dev.azure.com/{org}` | `mycompany` |
| `ADO_PROJECT` | Yes | Project name | `my-project` |
| `ADO_PAT` | Yes | Personal Access Token | `abc123...` |
| `ADO_LOOKBACK_DAYS` | No | How many days back to look (default: `30`) | `14` |
| `ADO_OUTPUT_DIR` | No | Where to write output files (default: `output`) | `results` |

Set them in PowerShell:

```powershell
$env:ADO_ORG     = "your-org"
$env:ADO_PROJECT = "your-project"
$env:ADO_PAT     = "your-pat-token"
```

Or set them permanently in your user profile so you don't need to re-enter them each session.

---

## Running the scripts

### Fetch everything (recommended)

```powershell
python fetch_all.py
```

This runs both fetchers and writes all output files to `output/`.

### CLI options

```
python fetch_all.py [OPTIONS]

Options:
  --days   INT          Lookback window in days (overrides ADO_LOOKBACK_DAYS)
  --out    PATH         Output directory (overrides ADO_OUTPUT_DIR)
  --format json|csv|both  Output format (default: json)
  --log-level LEVEL     Logging verbosity: DEBUG, INFO, WARNING, ERROR (default: INFO)
```

**Examples:**

```powershell
# Last 14 days, JSON only
python fetch_all.py --days 14

# Last 30 days, write both JSON and CSV
python fetch_all.py --format both

# Custom output folder
python fetch_all.py --out C:\Reports\ado

# All options combined
python fetch_all.py --days 7 --format both --out .\weekly --log-level DEBUG
```

### Run individual fetchers

You can also run each fetcher standalone — useful for testing or debugging a single data source:

```powershell
# Repos, commits, and PR data only
python fetch_repos.py

# Pipeline and build data only
python fetch_pipelines.py
```

Both print JSON to stdout.

---

## Output files

All files are written to the directory configured by `--out` or `ADO_OUTPUT_DIR` (default: `output/`).

| File | Format | Contents |
|---|---|---|
| `repos.json` | JSON | Repository list, commit stats, PR stats |
| `pipelines.json` | JSON | Pipeline definitions, build stats, daily trend, failure reasons |
| `summary.json` | JSON | Cross-cutting developer summary |
| `developer_summary.csv` | CSV | Developer summary (one row per person) |
| `commit_stats.csv` | CSV | Commit counts per author per repo |
| `pipelines_by_pipeline.csv` | CSV | Build stats per pipeline |
| `pipelines_by_developer.csv` | CSV | Build trigger stats per developer |
| `build_trend.csv` | CSV | Daily build outcome counts |

CSV files are only written when `--format csv` or `--format both` is used.

---

## File structure

```
ADO stats/
├── config.py          # Configuration (org, project, PAT, lookback window)
├── ado_client.py      # HTTP client: PAT auth, pagination, retries
├── fetch_repos.py     # Repos, commits, pull requests
├── fetch_pipelines.py # Builds, success rates, durations, trends
├── fetch_all.py       # Main orchestrator — runs everything, writes output
├── requirements.txt   # Python dependencies
└── output/            # Created automatically on first run
```

---

## Troubleshooting

| Problem | Likely cause | Fix |
|---|---|---|
| `401 Unauthorized` | Invalid or expired PAT | Regenerate the token and update `ADO_PAT` |
| `ADO_PAT is not set` error on startup | Variable not exported | Set `$env:ADO_PAT` in current shell, or edit `config.py` |
| `404 Not Found` on builds/commits | Wrong org or project name | Check `ADO_ORG` and `ADO_PROJECT` match exactly (case-sensitive) |
| Empty results | PAT missing required scope | Ensure **Code: Read** and **Build: Read** scopes are granted |
| Rate limit warnings | Too many requests | The client retries automatically; wait a moment and re-run |
