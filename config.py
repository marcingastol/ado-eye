"""
Azure DevOps configuration.
Set your values via environment variables or edit the defaults here.
"""
import os

# --- Required ---
# Your Azure DevOps organization name (e.g. "mycompany" from dev.azure.com/mycompany)
ADO_ORG = os.getenv("ADO_ORG", "your-organization")

# Your Azure DevOps project name
ADO_PROJECT = os.getenv("ADO_PROJECT", "your-project")

# Personal Access Token — needs: Code (Read), Build (Read), Graph (Read)
# Generate at: https://dev.azure.com/{org}/_usersSettings/tokens
ADO_PAT = os.getenv("ADO_PAT", "your-pat-token")

# --- Optional ---
# How many days back to look (default: last 30 days)
LOOKBACK_DAYS = int(os.getenv("ADO_LOOKBACK_DAYS", "30"))

# Output directory for JSON/CSV results
OUTPUT_DIR = os.getenv("ADO_OUTPUT_DIR", "output")

# Base API URL (rarely needs changing)
ADO_BASE_URL = f"https://dev.azure.com/{ADO_ORG}/{ADO_PROJECT}/_apis"
ADO_VSAEX_URL = f"https://vsaex.dev.azure.com/{ADO_ORG}/{ADO_PROJECT}/_apis"
