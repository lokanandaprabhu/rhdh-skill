#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""RHDH Release Dashboard Generator

Generates an interactive HTML dashboard showing hierarchical release progress
with features, epics, issues, and linked GitHub PRs.

Usage:
    uv run scripts/dashboard.py 2.1
    uv run scripts/dashboard.py rhdh-2.1
    uv run scripts/dashboard.py rhdh-2.1-candidate
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import platform
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

JIRA_BASE = "https://redhat.atlassian.net"
JIRA_API_BASE = f"{JIRA_BASE}/rest/api/3"


def normalize_label(version: str) -> str:
    """Normalize version input to rhdh-X.Y-candidate label format."""
    version = version.strip()

    # Already in full format
    if version.startswith("rhdh-") and version.endswith("-candidate"):
        return version

    # Remove rhdh- prefix if present
    if version.startswith("rhdh-"):
        version = version[5:]

    # Extract X.Y pattern
    match = re.match(r"^(\d+\.\d+)", version)
    if not match:
        raise ValueError(f"Invalid version format: {version}. Expected X.Y (e.g., 2.1)")

    return f"rhdh-{match.group(1)}-candidate"


def check_dependencies() -> dict[str, bool]:
    """Check if required CLI tools are available."""
    deps = {}

    # Check acli
    try:
        subprocess.run(
            ["acli", "--version"],
            capture_output=True,
            check=True,
            timeout=5
        )
        deps["acli"] = True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        deps["acli"] = False

    # Check for JIRA API credentials (fallback if no acli)
    deps["jira_token"] = bool(os.environ.get("JIRA_API_TOKEN") and os.environ.get("JIRA_EMAIL"))

    # Check gh
    try:
        subprocess.run(
            ["gh", "--version"],
            capture_output=True,
            check=True,
            timeout=5
        )
        deps["gh"] = True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        deps["gh"] = False

    return deps


def resolve_team(team_input: str) -> tuple[str, str]:
    """Resolve team input to (team_id, team_name). Accepts either a team ID or a team name."""
    # If it looks like a Jira team ID (UUID-digits pattern), use directly
    if re.match(r'^[a-f0-9]+-[a-f0-9-]+-\d+$', team_input):
        return team_input, ""

    # Looks like a team name — search recent issues to find the matching ID
    token = os.environ.get("JIRA_API_TOKEN")
    email = os.environ.get("JIRA_EMAIL")
    if not token or not email:
        return team_input, ""

    credentials = f"{email}:{token}"
    encoded = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")

    params = {
        "jql": 'project in ("RHDHPLAN", "RHDHBUGS") AND "Team[Team]" is not EMPTY ORDER BY updated DESC',
        "maxResults": 100,
        "fields": "customfield_10001"
    }
    url = f"{JIRA_API_BASE}/search/jql?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", f"Basic {encoded}")

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
        search_lower = team_input.lower()
        for issue in data.get("issues", []):
            team_field = issue.get("fields", {}).get("customfield_10001")
            if isinstance(team_field, dict):
                name = team_field.get("name", "")
                tid = team_field.get("id", "")
                if name.lower() == search_lower:
                    return tid, name
        # No exact match — try partial match
        for issue in data.get("issues", []):
            team_field = issue.get("fields", {}).get("customfield_10001")
            if isinstance(team_field, dict):
                name = team_field.get("name", "")
                tid = team_field.get("id", "")
                if search_lower in name.lower():
                    return tid, name
    except Exception:
        pass

    print(f"  Warning: Could not resolve team name '{team_input}' to an ID. Using as-is.")
    return team_input, ""


def query_jira_issues_by_jql(jql: str) -> list[dict[str, Any]]:
    """Query JIRA using custom JQL (for parent IN queries). No pagination support in /search/jql."""
    token = os.environ.get("JIRA_API_TOKEN")
    email = os.environ.get("JIRA_EMAIL")

    if not token or not email:
        return []

    # Build API request
    url = f"{JIRA_API_BASE}/search/jql"
    payload = {
        "jql": jql,
        "maxResults": 1000,
        "fields": ["key", "issuetype", "status", "summary", "assignee", "parent", "customfield_10875", "customfield_10001"]
    }
    json_payload = json.dumps(payload).encode("utf-8")

    # Create request with Basic authentication
    req = urllib.request.Request(url, data=json_payload, method="POST")
    credentials = f"{email}:{token}"
    encoded_credentials = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")
    req.add_header("Authorization", f"Basic {encoded_credentials}")
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            data = json.loads(response.read().decode("utf-8"))

        # Parse response into the same format
        issues = []
        for issue in data.get("issues", []):
            fields = issue.get("fields", {})
            # Extract team name from Team field if present
            team_field = fields.get("customfield_10001", {})
            team_name = team_field.get("name", "") if isinstance(team_field, dict) else ""
            team_id = team_field.get("id", "") if isinstance(team_field, dict) else ""

            issues.append({
                "key": issue.get("key", ""),
                "type": fields.get("issuetype", {}).get("name", ""),
                "status": fields.get("status", {}).get("name", ""),
                "summary": fields.get("summary", ""),
                "assignee": fields.get("assignee", {}).get("displayName", "") if fields.get("assignee") else "",
                "parent": fields.get("parent", {}).get("key", "") if fields.get("parent") else "",
                "git_pr": fields.get("customfield_10875", ""),
                "team_name": team_name,
                "team_id": team_id,
            })

        return issues

    except Exception as e:
        print(f"Warning: JQL query failed: {e}", file=sys.stderr)
        return []


def query_jira_by_fix_version(fix_version: str, project: str, issue_type: str | None = None, team: str | None = None) -> list[dict[str, Any]]:
    """Query JIRA by fixVersion and project using REST API with token-based pagination."""
    token = os.environ.get("JIRA_API_TOKEN")
    email = os.environ.get("JIRA_EMAIL")

    if not token:
        raise RuntimeError("JIRA_API_TOKEN environment variable not set")
    if not email:
        raise RuntimeError("JIRA_EMAIL environment variable not set (your Red Hat email)")

    # Build JQL query with fixVersion
    jql = f'project = "{project}" AND fixVersion = "{fix_version}"'
    if issue_type:
        jql += f' AND type = "{issue_type}"'
    if team:
        # Team filter uses exact match with team ID (Cloud ID format)
        jql += f' AND "Team[Team]" = "{team}"'

    all_issues = []
    next_page_token = None

    # Loop through pages using nextPageToken pagination
    while True:
        # Use GET with query parameters (enhanced API mode)
        params = {
            "jql": jql,
            "maxResults": 100,
            "fields": "key,issuetype,status,summary,assignee,parent,customfield_10875,customfield_10001,priority"
        }
        if next_page_token:
            params["nextPageToken"] = next_page_token

        url = f"{JIRA_API_BASE}/search/jql?{urllib.parse.urlencode(params)}"

        # Create request with Basic authentication
        req = urllib.request.Request(url, method="GET")
        credentials = f"{email}:{token}"
        encoded_credentials = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")
        req.add_header("Authorization", f"Basic {encoded_credentials}")

        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                data = json.loads(response.read().decode("utf-8"))

            # Parse response
            for issue in data.get("issues", []):
                fields = issue.get("fields", {})
                team_field = fields.get("customfield_10001", {})
                team_name = team_field.get("name", "") if isinstance(team_field, dict) else ""
                team_id = team_field.get("id", "") if isinstance(team_field, dict) else ""

                all_issues.append({
                    "key": issue.get("key", ""),
                    "type": fields.get("issuetype", {}).get("name", ""),
                    "status": fields.get("status", {}).get("name", ""),
                    "summary": fields.get("summary", ""),
                    "assignee": fields.get("assignee", {}).get("displayName", "") if fields.get("assignee") else "",
                    "parent": fields.get("parent", {}).get("key", "") if fields.get("parent") else "",
                    "git_pr": fields.get("customfield_10875", ""),
                    "team_name": team_name,
                    "team_id": team_id,
                    "priority": fields.get("priority", {}).get("name", "") if fields.get("priority") else "",
                })

            # Check for next page
            next_page_token = data.get("nextPageToken")
            if not next_page_token:
                break

        except urllib.error.HTTPError as e:
            error_msg = e.read().decode("utf-8") if e.fp else str(e)
            raise RuntimeError(f"JIRA API request failed: {e.code} {e.reason}\n{error_msg}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"JIRA API connection failed: {e.reason}")
        except Exception as e:
            raise RuntimeError(f"JIRA query failed: {e}")

    return all_issues


def query_jira_by_parent_keys(parent_keys: list[str], batch_size: int = 20) -> list[dict[str, Any]]:
    """Query JIRA issues by parent keys in batches with token-based pagination."""
    if not parent_keys:
        return []

    token = os.environ.get("JIRA_API_TOKEN")
    email = os.environ.get("JIRA_EMAIL")

    if not token:
        raise RuntimeError("JIRA_API_TOKEN environment variable not set")
    if not email:
        raise RuntimeError("JIRA_EMAIL environment variable not set (your Red Hat email)")

    all_issues = []

    # Process in batches to avoid URL length limits
    for i in range(0, len(parent_keys), batch_size):
        batch = parent_keys[i:i + batch_size]
        parent_list = ",".join(batch)
        # Don't filter by project - child items can be in any project (RHDHPLAN, RHIDP, etc.)
        jql = f'parent IN ({parent_list})'

        # Paginate through results using nextPageToken
        next_page_token = None

        while True:
            # Use GET with query parameters (enhanced API mode)
            params = {
                "jql": jql,
                "maxResults": 100,
                "fields": "key,issuetype,status,summary,assignee,parent,customfield_10875,customfield_10001,priority"
            }
            if next_page_token:
                params["nextPageToken"] = next_page_token

            url = f"{JIRA_API_BASE}/search/jql?{urllib.parse.urlencode(params)}"

            # Create request with Basic authentication
            req = urllib.request.Request(url, method="GET")
            credentials = f"{email}:{token}"
            encoded_credentials = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")
            req.add_header("Authorization", f"Basic {encoded_credentials}")

            try:
                with urllib.request.urlopen(req, timeout=60) as response:
                    data = json.loads(response.read().decode("utf-8"))

                # Parse response
                for issue in data.get("issues", []):
                    fields = issue.get("fields", {})
                    team_field = fields.get("customfield_10001", {})
                    team_name = team_field.get("name", "") if isinstance(team_field, dict) else ""
                    team_id = team_field.get("id", "") if isinstance(team_field, dict) else ""

                    all_issues.append({
                        "key": issue.get("key", ""),
                        "type": fields.get("issuetype", {}).get("name", ""),
                        "status": fields.get("status", {}).get("name", ""),
                        "summary": fields.get("summary", ""),
                        "assignee": fields.get("assignee", {}).get("displayName", "") if fields.get("assignee") else "",
                        "parent": fields.get("parent", {}).get("key", "") if fields.get("parent") else "",
                        "git_pr": fields.get("customfield_10875", ""),
                        "team_name": team_name,
                        "team_id": team_id,
                        "priority": fields.get("priority", {}).get("name", "") if fields.get("priority") else "",
                    })

                # Check for next page
                next_page_token = data.get("nextPageToken")
                if not next_page_token:
                    break

            except urllib.error.HTTPError as e:
                error_msg = e.read().decode("utf-8") if e.fp else str(e)
                raise RuntimeError(f"JIRA API request failed: {e.code} {e.reason}\n{error_msg}")
            except urllib.error.URLError as e:
                raise RuntimeError(f"JIRA API connection failed: {e.reason}")
            except Exception as e:
                raise RuntimeError(f"JIRA query failed: {e}")

    return all_issues


def query_jira_rest_api_raw(jql: str) -> list[dict[str, Any]]:
    """Query JIRA using raw JQL with token-based pagination (GET method for enhanced API mode)."""
    token = os.environ.get("JIRA_API_TOKEN")
    email = os.environ.get("JIRA_EMAIL")

    if not token:
        raise RuntimeError("JIRA_API_TOKEN environment variable not set")
    if not email:
        raise RuntimeError("JIRA_EMAIL environment variable not set")

    all_issues = []
    next_page_token = None

    # Loop through pages using nextPageToken pagination
    while True:
        # Use GET with query parameters (enhanced API mode)
        params = {
            "jql": jql,
            "maxResults": 100,
            "fields": "key,issuetype,status,summary,assignee,parent,customfield_10875,customfield_10001,priority"
        }
        if next_page_token:
            params["nextPageToken"] = next_page_token

        url = f"{JIRA_API_BASE}/search/jql?{urllib.parse.urlencode(params)}"

        req = urllib.request.Request(url, method="GET")
        credentials = f"{email}:{token}"
        encoded_credentials = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")
        req.add_header("Authorization", f"Basic {encoded_credentials}")

        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                data = json.loads(response.read().decode("utf-8"))

            for issue in data.get("issues", []):
                fields = issue.get("fields", {})
                team_field = fields.get("customfield_10001", {})
                team_name = team_field.get("name", "") if isinstance(team_field, dict) else ""
                team_id = team_field.get("id", "") if isinstance(team_field, dict) else ""

                all_issues.append({
                    "key": issue.get("key", ""),
                    "type": fields.get("issuetype", {}).get("name", ""),
                    "status": fields.get("status", {}).get("name", ""),
                    "summary": fields.get("summary", ""),
                    "assignee": fields.get("assignee", {}).get("displayName", "") if fields.get("assignee") else "",
                    "parent": fields.get("parent", {}).get("key", "") if fields.get("parent") else "",
                    "git_pr": fields.get("customfield_10875", ""),
                    "team_name": team_name,
                    "team_id": team_id,
                    "priority": fields.get("priority", {}).get("name", "") if fields.get("priority") else "",
                })

            next_page_token = data.get("nextPageToken")
            if not next_page_token:
                break

        except urllib.error.HTTPError as e:
            error_msg = e.read().decode("utf-8") if e.fp else str(e)
            raise RuntimeError(f"JIRA API request failed: {e.code} {e.reason}\n{error_msg}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"JIRA API connection failed: {e.reason}")
        except Exception as e:
            raise RuntimeError(f"JIRA API error: {e}")

    return all_issues


def query_jira_rest_api(label: str, issue_type: str | None = None, team: str | None = None) -> list[dict[str, Any]]:
    """Query JIRA using REST API with token-based pagination (no acli required)."""
    token = os.environ.get("JIRA_API_TOKEN")
    email = os.environ.get("JIRA_EMAIL")

    if not token:
        raise RuntimeError("JIRA_API_TOKEN environment variable not set")
    if not email:
        raise RuntimeError("JIRA_EMAIL environment variable not set (your Red Hat email)")

    # Build JQL query
    jql = f'labels = "{label}"'
    if issue_type:
        jql += f' AND type = "{issue_type}"'
    if team:
        # Team filter uses exact match with team ID (Cloud ID format)
        jql += f' AND "Team[Team]" = "{team}"'

    all_issues = []
    next_page_token = None

    # Loop through pages using nextPageToken pagination
    while True:
        # Use GET with query parameters (enhanced API mode)
        params = {
            "jql": jql,
            "maxResults": 100,
            "fields": "key,issuetype,status,summary,assignee,parent,customfield_10875,customfield_10001"
        }
        if next_page_token:
            params["nextPageToken"] = next_page_token

        url = f"{JIRA_API_BASE}/search/jql?{urllib.parse.urlencode(params)}"

        # Create request with Basic authentication
        req = urllib.request.Request(url, method="GET")
        credentials = f"{email}:{token}"
        encoded_credentials = base64.b64encode(credentials.encode("utf-8")).decode("utf-8")
        req.add_header("Authorization", f"Basic {encoded_credentials}")

        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                data = json.loads(response.read().decode("utf-8"))

            # Parse response
            for issue in data.get("issues", []):
                fields = issue.get("fields", {})
                team_field = fields.get("customfield_10001", {})
                team_name = team_field.get("name", "") if isinstance(team_field, dict) else ""
                team_id = team_field.get("id", "") if isinstance(team_field, dict) else ""

                all_issues.append({
                    "key": issue.get("key", ""),
                    "type": fields.get("issuetype", {}).get("name", ""),
                    "status": fields.get("status", {}).get("name", ""),
                    "summary": fields.get("summary", ""),
                    "assignee": fields.get("assignee", {}).get("displayName", "") if fields.get("assignee") else "",
                    "parent": fields.get("parent", {}).get("key", "") if fields.get("parent") else "",
                    "git_pr": fields.get("customfield_10875", ""),
                    "team_name": team_name,
                    "team_id": team_id,
                })

            # Check for next page
            next_page_token = data.get("nextPageToken")
            if not next_page_token:
                break

        except urllib.error.HTTPError as e:
            error_msg = e.read().decode("utf-8") if e.fp else str(e)
            raise RuntimeError(f"JIRA API request failed: {e.code} {e.reason}\n{error_msg}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"JIRA API connection failed: {e.reason}")
        except Exception as e:
            raise RuntimeError(f"JIRA API error: {e}")

    return all_issues


def query_jira_issues(label: str, issue_type: str | None = None, team: str | None = None) -> list[dict[str, Any]]:
    """Query JIRA issues using acli or REST API (fallback)."""

    # Try acli first (preferred, consistent with repo)
    acli_available = subprocess.run(
        ["which", "acli"],
        capture_output=True,
        timeout=5
    ).returncode == 0

    if acli_available:
        return query_jira_acli(label, issue_type, team)
    else:
        # Fall back to REST API
        return query_jira_rest_api(label, issue_type, team)


def query_jira_acli(label: str, issue_type: str | None = None, team: str | None = None) -> list[dict[str, Any]]:
    """Query JIRA issues using acli."""
    jql = f'labels = "{label}"'
    if issue_type:
        jql += f' AND type = "{issue_type}"'
    if team:
        # Team filter uses exact match with team ID (Cloud ID format)
        jql += f' AND "Team[Team]" = "{team}"'

    cmd = [
        "acli",
        "issue",
        "list",
        "--jql", jql,
        "--output-format", "999",
        "--columns", "key,type,status,summary,assignee,parent",
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=60
        )

        # Parse acli output (CSV-like format)
        lines = result.stdout.strip().split("\n")
        if len(lines) < 2:
            return []

        issues = []
        for line in lines[1:]:  # Skip header
            if not line.strip():
                continue

            # Simple CSV parsing (acli uses quotes for fields with commas)
            parts = []
            in_quotes = False
            current = []
            for char in line:
                if char == '"':
                    in_quotes = not in_quotes
                elif char == ',' and not in_quotes:
                    parts.append(''.join(current).strip().strip('"'))
                    current = []
                    continue
                current.append(char)
            parts.append(''.join(current).strip().strip('"'))

            if len(parts) >= 5:
                issues.append({
                    "key": parts[0],
                    "type": parts[1],
                    "status": parts[2],
                    "summary": parts[3],
                    "assignee": parts[4] if len(parts) > 4 else "",
                    "parent": parts[5] if len(parts) > 5 else "",
                })

        return issues

    except subprocess.TimeoutExpired:
        print(f"Error: JIRA query timed out for label '{label}'", file=sys.stderr)
        return []
    except subprocess.CalledProcessError as e:
        print(f"Error: acli failed: {e.stderr}", file=sys.stderr)
        return []


def get_issue_links(issue: dict[str, Any]) -> list[str]:
    """Get GitHub PR URLs from the issue's Git Pull Request field (customfield_10875)."""
    pr_urls = []

    # Get the Git Pull Request field value
    git_pr = issue.get("git_pr", "")

    if not git_pr:
        return []

    # Convert to string if it's a dict or other object
    if isinstance(git_pr, dict):
        # It might be a structured object - try to get a URL field
        git_pr_str = git_pr.get("url", "") or git_pr.get("value", "") or str(git_pr)
    elif isinstance(git_pr, str):
        git_pr_str = git_pr
    else:
        git_pr_str = str(git_pr)

    # Extract GitHub PR URLs from the field value
    # The field may contain one or more URLs
    matches = re.findall(r'https://github\.com/[^/\s]+/[^/\s]+/pull/\d+', git_pr_str)

    # Remove duplicates while preserving order
    seen = set()
    for url in matches:
        if url not in seen:
            seen.add(url)
            pr_urls.append(url)

    return pr_urls


def get_pr_status(pr_url: str) -> dict[str, Any]:
    """Get GitHub PR status using gh CLI."""
    # Extract owner, repo, number from URL
    match = re.match(r'https://github\.com/([^/]+)/([^/]+)/pull/(\d+)', pr_url)
    if not match:
        return {"url": pr_url, "state": "unknown"}

    owner, repo, number = match.groups()

    try:
        result = subprocess.run(
            [
                "gh", "pr", "view", number,
                "--repo", f"{owner}/{repo}",
                "--json", "state,isDraft,mergedAt,statusCheckRollup,reviewDecision"
            ],
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode != 0:
            # Debug: log the error
            print(f"Debug - gh command failed for {pr_url}: {result.stderr[:200]}", file=sys.stderr)
            return {"url": pr_url, "state": "unknown"}

        data = json.loads(result.stdout)

        # Determine status
        status = "unknown"
        is_merged = bool(data.get("mergedAt"))

        if is_merged:
            status = "merged"
        elif data.get("isDraft"):
            status = "draft"
        elif data.get("state") == "CLOSED":
            status = "closed"
        elif data.get("statusCheckRollup"):
            # Check CI status
            rollup = data["statusCheckRollup"]
            if any(c.get("state") == "FAILURE" for c in rollup):
                status = "failing"
            elif all(c.get("state") == "SUCCESS" for c in rollup):
                status = "passing"
            else:
                status = "pending"
        elif data.get("reviewDecision") == "APPROVED":
            status = "approved"
        elif data.get("state") == "OPEN":
            status = "open"

        return {
            "url": pr_url,
            "number": number,
            "state": status,
            "merged": is_merged,
        }

    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, json.JSONDecodeError):
        return {"url": pr_url, "state": "unknown"}


def build_hierarchy(label: str, has_gh: bool, team: str | None = None) -> dict[str, Any]:
    """Build the full feature → epic → issue hierarchy."""
    if team:
        print(f"Querying JIRA for label: {label}, filtering Features by team: {team}")

        # Query Features WITH team filter
        features = query_jira_issues(label, issue_type="Feature", team=team)

        if not features:
            return {"features": [], "stats": {"features": 0, "epics": 0, "issues": 0, "prs": 0}}

        # Build JQL to get child Epics and Issues of these specific Features
        feature_keys = [f["key"] for f in features]

        # Query Epics that are children of our Features (NO label filter - if parent is in 2.1, child is in 2.1)
        print(f"Querying all child Epics for {len(features)} Features")
        epics_jql = f'type = "Epic" AND parent IN ({",".join(feature_keys)})'
        epics = query_jira_issues_by_jql(epics_jql)

        # Query other issues (Stories, Tasks, etc.) that are children of our Epics (NO label filter)
        # Batch the queries to avoid hitting maxResults=1000 limit in /search/jql
        if epics:
            epic_keys = [e["key"] for e in epics]
            print(f"Querying all child issues for {len(epics)} Epics (in batches)")

            # Query in batches of 20 epics at a time (each epic might have many children)
            batch_size = 20
            other_issues = []
            for i in range(0, len(epic_keys), batch_size):
                batch = epic_keys[i:i+batch_size]
                batch_jql = f'parent IN ({",".join(batch)})'
                batch_issues = query_jira_issues_by_jql(batch_jql)
                other_issues.extend(batch_issues)
                print(f"  Batch {i//batch_size + 1}/{(len(epic_keys) + batch_size - 1)//batch_size}: {len(batch_issues)} issues", file=sys.stderr)
        else:
            other_issues = []

        # Combine
        all_issues = features + epics + other_issues

        print(f"Found {len(features)} Features for team")
        print(f"Found {len(epics)} child Epics")
        print(f"Found {len(other_issues)} child Issues")
    else:
        print(f"Querying JIRA for label: {label}")
        all_issues = query_jira_issues(label)

    if not all_issues:
        return {"features": [], "stats": {"features": 0, "epics": 0, "issues": 0, "prs": 0}}

    # Build lookup map
    issue_map = {issue["key"]: issue for issue in all_issues}

    # Categorize by type
    features = [i for i in all_issues if i["type"] == "Feature"]
    epics = [i for i in all_issues if i["type"] == "Epic"]
    other_issues = [i for i in all_issues if i["type"] not in ["Feature", "Epic"]]

    print(f"  - {len(features)} Features")
    print(f"  - {len(epics)} Epics")
    print(f"  - {len(other_issues)} other issues")

    # Build hierarchy structure
    hierarchy = []
    total_prs = 0

    for feature in features:
        feature_node = {
            "key": feature["key"],
            "summary": feature["summary"],
            "status": feature["status"],
            "type": "Feature",
            "assignee": feature.get("assignee", ""),
            "epics": [],
            "prs": []
        }

        # Find child epics
        feature_epics = [e for e in epics if e.get("parent") == feature["key"]]

        for epic in feature_epics:
            epic_node = {
                "key": epic["key"],
                "summary": epic["summary"],
                "status": epic["status"],
                "type": "Epic",
                "assignee": epic.get("assignee", ""),
                "issues": [],
                "prs": []
            }

            # Find child issues
            epic_issues = [i for i in other_issues if i.get("parent") == epic["key"]]

            for issue in epic_issues:
                issue_node = {
                    "key": issue["key"],
                    "summary": issue["summary"],
                    "status": issue["status"],
                    "type": issue["type"],
                    "assignee": issue.get("assignee", ""),
                    "prs": []
                }

                # Get PRs if gh is available
                if has_gh:
                    pr_urls = get_issue_links(issue)
                    for pr_url in pr_urls:
                        pr_status = get_pr_status(pr_url)
                        issue_node["prs"].append(pr_status)
                        total_prs += 1

                epic_node["issues"].append(issue_node)

            # Calculate epic completion
            if epic_node["issues"]:
                done_count = sum(1 for i in epic_node["issues"] if is_status_done(i["status"]))
                epic_node["completion"] = int((done_count / len(epic_node["issues"])) * 100)
            else:
                epic_node["completion"] = 100 if is_status_done(epic["status"]) else 0

            feature_node["epics"].append(epic_node)

        # Calculate feature completion
        all_children = []
        for epic in feature_node["epics"]:
            all_children.append(epic)
            all_children.extend(epic["issues"])

        if all_children:
            done_count = sum(1 for i in all_children if is_status_done(i["status"]))
            feature_node["completion"] = int((done_count / len(all_children)) * 100)
        else:
            feature_node["completion"] = 100 if is_status_done(feature["status"]) else 0

        hierarchy.append(feature_node)

    # Extract team display name from first feature if available
    team_display_name = ""
    if features and features[0].get("team_name"):
        team_display_name = features[0]["team_name"]

    return {
        "features": hierarchy,
        "stats": {
            "features": len(features),
            "epics": len(epics),
            "issues": len(other_issues),
            "prs": total_prs
        },
        "team_name": team_display_name
    }


def build_comprehensive_release_view(fix_version: str | None = None, label: str | None = None, has_gh: bool = False, team: str | None = None, backport_versions: list[str] | None = None) -> dict[str, Any]:
    """Build comprehensive release view with Features, Independent Epics/Stories, and Bugs from RHDHPLAN and RHDHBUGS."""

    if fix_version and label:
        print(f"Querying JIRA for fixVersion: {fix_version} OR label: {label}")
    elif fix_version:
        print(f"Querying JIRA for fixVersion: {fix_version}")
    else:
        print(f"Querying JIRA for label: {label}")

    if team:
        print(f"  Filtering Features by team: {team}")

    # Query Features - pass team filter directly in JQL for accurate results
    print("  Querying Features...")
    features = []

    if fix_version and label:
        jql = f'(labels = "{label}" OR fixVersion = "{fix_version}") AND type = "Feature"'
        if team:
            jql += f' AND "Team[Team]" = "{team}"'
        features = query_jira_rest_api_raw(jql)
        print(f"    - Found {len(features)} Features")
    elif fix_version:
        features = query_jira_by_fix_version(fix_version, "RHDHPLAN", issue_type="Feature", team=team)
        print(f"    - Found {len(features)} Features")
    elif label:
        features = query_jira_rest_api(label, issue_type="Feature", team=team)
        print(f"    - Found {len(features)} Features")

    # Query ALL child Epics by parent relationship (no fixVersion/label filter, across all projects)
    print("  Querying child Epics...")
    child_epics = []
    if features:
        feature_keys = [f["key"] for f in features]
        child_epics = query_jira_by_parent_keys(feature_keys)

    # Query ALL child Stories/Tasks/Spikes by parent relationship (no fixVersion/label filter, across all projects)
    print("  Querying child Stories/Tasks/Spikes...")
    child_stories = []
    if child_epics:
        epic_keys = [e["key"] for e in child_epics]
        child_stories = query_jira_by_parent_keys(epic_keys)

    # Query independent epics/stories by fixVersion OR label (those not under any Feature)
    print("  Querying independent Epics with fixVersion/label...")
    all_epics_with_filter = []
    if fix_version:
        all_epics_with_filter.extend(query_jira_by_fix_version(fix_version, "RHDHPLAN", issue_type="Epic", team=None))
    if label:
        epics_by_label = query_jira_rest_api(label, issue_type="Epic", team=None)
        existing_keys = {e["key"] for e in all_epics_with_filter}
        for e in epics_by_label:
            if e["key"] not in existing_keys:
                all_epics_with_filter.append(e)
                existing_keys.add(e["key"])

    print("  Querying independent Stories/Tasks/Spikes with fixVersion/label...")
    all_stories_with_filter = []
    if fix_version:
        all_stories_with_filter.extend(query_jira_by_fix_version(fix_version, "RHDHPLAN", issue_type="Story", team=None))
        all_stories_with_filter.extend(query_jira_by_fix_version(fix_version, "RHDHPLAN", issue_type="Task", team=None))
        all_stories_with_filter.extend(query_jira_by_fix_version(fix_version, "RHDHPLAN", issue_type="Spike", team=None))
    if label:
        existing_keys = {i["key"] for i in all_stories_with_filter}
        for issue_type in ["Story", "Task", "Spike"]:
            items_by_label = query_jira_rest_api(label, issue_type=issue_type, team=None)
            for item in items_by_label:
                if item["key"] not in existing_keys:
                    all_stories_with_filter.append(item)
                    existing_keys.add(item["key"])

    # Combine: child epics + independent epics (dedup)
    epics = child_epics.copy()
    child_epic_keys = {e["key"] for e in child_epics}
    for e in all_epics_with_filter:
        if e["key"] not in child_epic_keys:
            epics.append(e)

    # Combine: child stories + independent stories (dedup)
    other_issues = child_stories.copy()
    child_story_keys = {s["key"] for s in child_stories}
    for s in all_stories_with_filter:
        if s["key"] not in child_story_keys:
            other_issues.append(s)

    # Query Bugs by fixVersion with team filter
    # Derive fixVersion from label if not provided (e.g., rhdh-2.1-candidate → 2.1.0)
    print("  Querying Bugs...")
    all_bugs = []
    bugs_fix_version = fix_version
    if not bugs_fix_version and label:
        ver_match = re.search(r'(\d+\.\d+)', label)
        if ver_match:
            bugs_fix_version = f"{ver_match.group(1)}.0"

    if bugs_fix_version:
        bugs_jql = f'fixVersion = "{bugs_fix_version}" AND type = "Bug"'
        if team:
            bugs_jql += f' AND "Team[Team]" = "{team}"'
        all_bugs = query_jira_rest_api_raw(bugs_jql)
    if label:
        bugs_by_label = query_jira_rest_api(label, issue_type="Bug", team=team)
        existing_keys = {b["key"] for b in all_bugs}
        for b in bugs_by_label:
            if b["key"] not in existing_keys:
                all_bugs.append(b)
                existing_keys.add(b["key"])

    # Query backport versions (user-provided via --backport flag)
    backports = {}
    if backport_versions:
        print(f"  Querying Backport versions: {', '.join(backport_versions)}")
        for bp_ver in backport_versions:
            bp_jql = f'project = "RHDHBUGS" AND fixVersion = "{bp_ver}" AND type = "Bug"'
            if team:
                bp_jql += f' AND "Team[Team]" = "{team}"'
            bp_bugs = query_jira_rest_api_raw(bp_jql)
            backports[bp_ver] = bp_bugs
            print(f"    - {bp_ver}: {len(bp_bugs)} bugs")

    total_backport_bugs = sum(len(bugs) for bugs in backports.values())
    print(f"Found {len(features)} Features, {len(epics)} Epics, {len(other_issues)} other items, {len(all_bugs)} bugs, {total_backport_bugs} backport bugs across {len(backports)} versions")

    # Build feature hierarchy
    feature_hierarchy = []
    total_prs = 0
    feature_keys = set()
    epic_keys = set()

    for feature in features:
        feature_node = {
            "key": feature["key"],
            "summary": feature["summary"],
            "status": feature["status"],
            "type": "Feature",
            "assignee": feature.get("assignee", ""),
            "epics": [],
            "prs": []
        }
        feature_keys.add(feature["key"])

        # Find child epics
        feature_epics = [e for e in epics if e.get("parent") == feature["key"]]

        for epic in feature_epics:
            epic_keys.add(epic["key"])
            epic_node = {
                "key": epic["key"],
                "summary": epic["summary"],
                "status": epic["status"],
                "type": "Epic",
                "assignee": epic.get("assignee", ""),
                "issues": [],
                "prs": []
            }

            # Find child issues
            epic_issues = [i for i in other_issues if i.get("parent") == epic["key"]]

            for issue in epic_issues:
                issue_node = {
                    "key": issue["key"],
                    "summary": issue["summary"],
                    "status": issue["status"],
                    "type": issue["type"],
                    "assignee": issue.get("assignee", ""),
                    "prs": []
                }

                if has_gh:
                    pr_urls = get_issue_links(issue)
                    for pr_url in pr_urls:
                        pr_status = get_pr_status(pr_url)
                        issue_node["prs"].append(pr_status)
                        total_prs += 1

                epic_node["issues"].append(issue_node)

            # Calculate epic completion
            if epic_node["issues"]:
                done_count = sum(1 for i in epic_node["issues"] if is_status_done(i["status"]))
                epic_node["completion"] = int((done_count / len(epic_node["issues"])) * 100)
            else:
                epic_node["completion"] = 100 if is_status_done(epic["status"]) else 0

            feature_node["epics"].append(epic_node)

        # Calculate feature completion
        all_children = []
        for epic in feature_node["epics"]:
            all_children.append(epic)
            all_children.extend(epic["issues"])

        if all_children:
            done_count = sum(1 for i in all_children if is_status_done(i["status"]))
            feature_node["completion"] = int((done_count / len(all_children)) * 100)
        else:
            feature_node["completion"] = 100 if is_status_done(feature["status"]) else 0

        feature_hierarchy.append(feature_node)

    # Find independent epics (not under any feature)
    independent_epics = []
    for epic in epics:
        parent = epic.get("parent", "")
        if not parent or parent not in feature_keys:
            epic_keys.add(epic["key"])
            epic_node = {
                "key": epic["key"],
                "summary": epic["summary"],
                "status": epic["status"],
                "type": "Epic",
                "assignee": epic.get("assignee", ""),
                "issues": [],
                "prs": []
            }

            # Find child issues
            epic_issues = [i for i in other_issues if i.get("parent") == epic["key"]]

            for issue in epic_issues:
                issue_node = {
                    "key": issue["key"],
                    "summary": issue["summary"],
                    "status": issue["status"],
                    "type": issue["type"],
                    "assignee": issue.get("assignee", ""),
                    "prs": []
                }

                if has_gh:
                    pr_urls = get_issue_links(issue)
                    for pr_url in pr_urls:
                        pr_status = get_pr_status(pr_url)
                        issue_node["prs"].append(pr_status)
                        total_prs += 1

                epic_node["issues"].append(issue_node)

            if epic_node["issues"]:
                done_count = sum(1 for i in epic_node["issues"] if is_status_done(i["status"]))
                epic_node["completion"] = int((done_count / len(epic_node["issues"])) * 100)
            else:
                epic_node["completion"] = 100 if is_status_done(epic["status"]) else 0

            independent_epics.append(epic_node)

    # Find independent stories/tasks/spikes
    independent_stories = []
    for issue in other_issues:
        parent = issue.get("parent", "")
        if not parent or (parent not in epic_keys and parent not in feature_keys):
            issue_node = {
                "key": issue["key"],
                "summary": issue["summary"],
                "status": issue["status"],
                "type": issue["type"],
                "assignee": issue.get("assignee", ""),
                "prs": []
            }

            if has_gh:
                pr_urls = get_issue_links(issue)
                for pr_url in pr_urls:
                    pr_status = get_pr_status(pr_url)
                    issue_node["prs"].append(pr_status)
                    total_prs += 1

            independent_stories.append(issue_node)

    # Group bugs by status
    bugs_by_status = {}
    for bug in all_bugs:
        status = bug["status"]
        if status not in bugs_by_status:
            bugs_by_status[status] = []

        bug_node = {
            "key": bug["key"],
            "summary": bug["summary"],
            "status": status,
            "type": bug["type"],
            "assignee": bug.get("assignee", ""),
            "priority": bug.get("priority", ""),
            "parent": bug.get("parent", ""),
            "prs": []
        }

        if has_gh:
            pr_urls = get_issue_links(bug)
            for pr_url in pr_urls:
                pr_status = get_pr_status(pr_url)
                bug_node["prs"].append(pr_status)
                total_prs += 1

        bugs_by_status[status].append(bug_node)

    # Extract team display name
    team_display_name = ""
    if features and features[0].get("team_name"):
        team_display_name = features[0]["team_name"]

    # Build backport bug nodes (grouped by version)
    backport_data = {}
    for bp_version, bp_bugs in sorted(backports.items(), key=lambda x: x[0], reverse=True):
        bp_bugs_by_status = {}
        for bug in bp_bugs:
            status = bug["status"]
            if status not in bp_bugs_by_status:
                bp_bugs_by_status[status] = []
            bug_node = {
                "key": bug["key"],
                "summary": bug["summary"],
                "status": status,
                "type": bug["type"],
                "assignee": bug.get("assignee", ""),
                "priority": bug.get("priority", ""),
                "parent": bug.get("parent", ""),
                "prs": []
            }
            if has_gh:
                pr_urls = get_issue_links(bug)
                for pr_url in pr_urls:
                    pr_status = get_pr_status(pr_url)
                    bug_node["prs"].append(pr_status)
                    total_prs += 1
            bp_bugs_by_status[status].append(bug_node)
        backport_data[bp_version] = {
            "bugs_by_status": bp_bugs_by_status,
            "total": len(bp_bugs),
        }

    print(f"  Built hierarchy:")
    print(f"    - {len(features)} Features")
    print(f"    - {len(independent_epics)} Independent Epics")
    print(f"    - {len(independent_stories)} Independent Stories/Tasks")
    print(f"    - {len(all_bugs)} Bugs ({len(bugs_by_status)} status groups)")
    if backport_data:
        print(f"    - {total_backport_bugs} Backport Bugs across {len(backport_data)} versions")

    return {
        "features": feature_hierarchy,
        "independent_epics": independent_epics,
        "independent_stories": independent_stories,
        "bugs_by_status": bugs_by_status,
        "backports": backport_data,
        "stats": {
            "features": len(features),
            "epics": len(epics),
            "independent_epics": len(independent_epics),
            "independent_stories": len(independent_stories),
            "issues": len(other_issues),
            "bugs": len(all_bugs),
            "backport_bugs": total_backport_bugs,
            "backport_versions": len(backport_data),
            "prs": total_prs
        },
        "team_name": team_display_name
    }


def build_team_overview(fix_version: str | None = None, label: str | None = None, backport_versions: list[str] | None = None) -> dict[str, Any]:
    """Build release overview grouped by team — no team filter, groups results by team_name."""

    if fix_version and label:
        print(f"Querying JIRA for team overview: fixVersion: {fix_version} OR label: {label}")
    elif fix_version:
        print(f"Querying JIRA for team overview: fixVersion: {fix_version}")
    else:
        print(f"Querying JIRA for team overview: label: {label}")

    # Query all features (no team filter)
    print("  Querying all Features...")
    features = []
    if fix_version and label:
        jql = f'(labels = "{label}" OR fixVersion = "{fix_version}") AND type = "Feature"'
        features = query_jira_rest_api_raw(jql)
    elif fix_version:
        features = query_jira_by_fix_version(fix_version, "RHDHPLAN", issue_type="Feature")
    elif label:
        features = query_jira_rest_api(label, issue_type="Feature")
    print(f"    - Found {len(features)} Features")

    # Query child epics and stories
    print("  Querying child Epics...")
    child_epics = []
    if features:
        feature_keys = [f["key"] for f in features]
        child_epics = query_jira_by_parent_keys(feature_keys)
    print(f"    - Found {len(child_epics)} child Epics")

    print("  Querying child Stories/Tasks/Spikes...")
    child_stories = []
    if child_epics:
        epic_keys = [e["key"] for e in child_epics]
        child_stories = query_jira_by_parent_keys(epic_keys)
    print(f"    - Found {len(child_stories)} child Stories/Tasks/Spikes")

    # Build parent→children maps
    epics_by_feature = {}
    for e in child_epics:
        parent = e.get("parent", "")
        if parent:
            epics_by_feature.setdefault(parent, []).append(e)

    stories_by_epic = {}
    for s in child_stories:
        parent = s.get("parent", "")
        if parent:
            stories_by_epic.setdefault(parent, []).append(s)

    # Query all bugs
    print("  Querying all Bugs...")
    all_bugs = []
    bugs_fix_version = fix_version
    if not bugs_fix_version and label:
        ver_match = re.search(r'(\d+\.\d+)', label)
        if ver_match:
            bugs_fix_version = f"{ver_match.group(1)}.0"

    if bugs_fix_version:
        bugs_jql = f'fixVersion = "{bugs_fix_version}" AND type = "Bug"'
        all_bugs = query_jira_rest_api_raw(bugs_jql)
    if label:
        bugs_by_label = query_jira_rest_api(label, issue_type="Bug")
        existing_keys = {b["key"] for b in all_bugs}
        for b in bugs_by_label:
            if b["key"] not in existing_keys:
                all_bugs.append(b)
                existing_keys.add(b["key"])
    print(f"    - Found {len(all_bugs)} Bugs")

    # Query backport bugs
    backport_bugs = []
    if backport_versions:
        print(f"  Querying Backport versions: {', '.join(backport_versions)}")
        for bp_ver in backport_versions:
            bp_jql = f'project = "RHDHBUGS" AND fixVersion = "{bp_ver}" AND type = "Bug"'
            bp_bugs = query_jira_rest_api_raw(bp_jql)
            for b in bp_bugs:
                b["backport_version"] = bp_ver
            backport_bugs.extend(bp_bugs)
            print(f"    - {bp_ver}: {len(bp_bugs)} bugs")

    # Group features by team (skip unassigned)
    teams = {}
    for f in features:
        team_name = f.get("team_name", "").strip()
        if not team_name:
            continue
        if team_name not in teams:
            teams[team_name] = {"features": [], "bugs": [], "backport_bugs": [], "team_id": f.get("team_id", "")}
        teams[team_name]["features"].append(f)

    # Group bugs by team (skip unassigned)
    for b in all_bugs:
        team_name = b.get("team_name", "").strip()
        if not team_name:
            continue
        if team_name not in teams:
            teams[team_name] = {"features": [], "bugs": [], "backport_bugs": [], "team_id": b.get("team_id", "")}
        teams[team_name]["bugs"].append(b)

    # Group backport bugs by team (skip unassigned)
    for b in backport_bugs:
        team_name = b.get("team_name", "").strip()
        if not team_name:
            continue
        if team_name not in teams:
            teams[team_name] = {"features": [], "bugs": [], "backport_bugs": [], "team_id": b.get("team_id", "")}
        teams[team_name]["backport_bugs"].append(b)

    # Compute stats per team with feature hierarchy
    team_stats = {}
    for team_name, tdata in sorted(teams.items()):
        t_features = tdata["features"]
        t_bugs = tdata["bugs"]
        t_bp_bugs = tdata["backport_bugs"]

        # Build feature hierarchy for this team
        feature_hierarchy = []
        all_children_count = 0
        all_children_done = 0
        for f in t_features:
            f_epics = epics_by_feature.get(f["key"], [])
            epic_nodes = []
            for e in f_epics:
                e_stories = stories_by_epic.get(e["key"], [])
                epic_nodes.append({
                    "key": e["key"],
                    "summary": e["summary"],
                    "status": e["status"],
                    "assignee": e.get("assignee", ""),
                    "stories": e_stories,
                })
                all_children_count += 1 + len(e_stories)
                all_children_done += (1 if is_status_done(e["status"]) else 0) + sum(1 for s in e_stories if is_status_done(s["status"]))

            feature_hierarchy.append({
                "key": f["key"],
                "summary": f["summary"],
                "status": f["status"],
                "assignee": f.get("assignee", ""),
                "epics": epic_nodes,
                "epic_count": len(f_epics),
                "story_count": sum(len(stories_by_epic.get(e["key"], [])) for e in f_epics),
            })

        done_features = sum(1 for f in t_features if is_status_done(f["status"]))
        done_bugs = sum(1 for b in t_bugs if is_status_done(b["status"]))

        team_stats[team_name] = {
            "features": len(t_features),
            "features_done": done_features,
            "features_pct": int((done_features / len(t_features)) * 100) if t_features else 0,
            "bugs": len(t_bugs),
            "bugs_done": done_bugs,
            "bugs_pct": int((done_bugs / len(t_bugs)) * 100) if t_bugs else 0,
            "backport_bugs": len(t_bp_bugs),
            "children_count": all_children_count,
            "children_done": all_children_done,
            "children_pct": int((all_children_done / all_children_count) * 100) if all_children_count else 0,
            "team_id": tdata["team_id"],
            "feature_hierarchy": feature_hierarchy,
            "bug_list": t_bugs,
            "backport_list": t_bp_bugs,
        }

    print(f"\n  Team overview: {len(teams)} teams, {len(features)} features, {len(all_bugs)} bugs, {len(backport_bugs)} backport bugs")
    for tn, ts in sorted(team_stats.items()):
        print(f"    - {tn}: {ts['features']} features ({ts['features_pct']}% done), {ts['bugs']} bugs ({ts['bugs_pct']}% done), {ts['backport_bugs']} backport bugs")

    return {
        "team_stats": team_stats,
        "totals": {
            "teams": len(teams),
            "features": len(features),
            "bugs": len(all_bugs),
            "backport_bugs": len(backport_bugs),
        }
    }


def generate_team_overview_html(data: dict[str, Any], label: str, version: str) -> str:
    """Generate HTML dashboard with team-grouped overview."""

    team_stats = data["team_stats"]
    totals = data["totals"]

    dashboard_title = f"RHDH {version} Release — Team Overview"
    header_info = f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"

    # Sort teams by feature count descending
    sorted_teams = sorted(team_stats.items(), key=lambda x: x[1]["features"], reverse=True)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{dashboard_title}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background: #f5f5f5;
            padding: 20px;
            line-height: 1.6;
        }}
        .container {{
            max-width: 1400px;
            margin: 0 auto;
            background: white;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            overflow: hidden;
        }}
        header {{
            background: linear-gradient(135deg, #0066cc 0%, #004499 100%);
            color: white;
            padding: 30px;
        }}
        header h1 {{ font-size: 28px; margin-bottom: 10px; }}
        header p {{ opacity: 0.9; font-size: 14px; }}
        .stats {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 20px;
            padding: 30px;
            background: #f8f9fa;
            border-bottom: 1px solid #dee2e6;
        }}
        .stat-card {{
            background: white;
            padding: 20px;
            border-radius: 8px;
            border-left: 4px solid #0066cc;
        }}
        .stat-card .number {{ font-size: 32px; font-weight: bold; color: #0066cc; }}
        .stat-card .label {{ color: #6c757d; font-size: 14px; margin-top: 5px; }}
        .content {{ padding: 30px; }}

        .team-card {{
            border: 1px solid #dee2e6;
            border-radius: 8px;
            margin-bottom: 20px;
            overflow: hidden;
        }}
        .team-header {{
            background: #f8f9fa;
            padding: 20px;
            cursor: pointer;
            display: grid;
            grid-template-columns: 1fr 150px 150px 150px 200px;
            align-items: center;
            gap: 15px;
            transition: background 0.2s;
        }}
        .team-header:hover {{ background: #e9ecef; }}
        .team-name {{
            font-size: 18px;
            font-weight: 600;
            color: #212529;
        }}
        .team-metric {{
            text-align: center;
        }}
        .team-metric .metric-num {{
            font-size: 22px;
            font-weight: 700;
        }}
        .team-metric .metric-label {{
            font-size: 12px;
            color: #6c757d;
        }}
        .progress-bar {{
            background: #e9ecef;
            border-radius: 4px;
            height: 8px;
            margin-top: 4px;
        }}
        .progress-fill {{
            border-radius: 4px;
            height: 8px;
            transition: width 0.3s;
        }}
        .team-details {{
            display: none;
            padding: 20px;
            border-top: 1px solid #dee2e6;
        }}
        .team-details.expanded {{ display: block; }}
        .detail-section {{
            margin-bottom: 20px;
        }}
        .detail-section h4 {{
            font-size: 15px;
            color: #495057;
            margin-bottom: 10px;
            padding-bottom: 5px;
            border-bottom: 1px solid #e9ecef;
        }}
        .issue-row {{
            display: grid;
            grid-template-columns: 120px 1fr 130px 150px;
            gap: 10px;
            padding: 8px 0;
            border-bottom: 1px solid #f0f0f0;
            font-size: 14px;
            align-items: center;
        }}
        .issue-row:last-child {{ border-bottom: none; }}
        .issue-key {{
            font-weight: 600;
            color: #0066cc;
            text-decoration: none;
        }}
        .issue-key:hover {{ text-decoration: underline; }}
        .status-badge {{
            display: inline-block;
            padding: 2px 8px;
            border-radius: 10px;
            font-size: 11px;
            font-weight: 500;
            white-space: nowrap;
        }}
        .status-done {{ background: #d4edda; color: #155724; }}
        .status-progress {{ background: #cce5ff; color: #004085; }}
        .status-blocked {{ background: #f8d7da; color: #721c24; }}
        .status-todo {{ background: #e2e3e5; color: #383d41; }}
        .color-features {{ color: #0066cc; }}
        .color-bugs {{ color: #dc3545; }}
        .color-backport {{ color: #6f42c1; }}
        .expand-arrow {{
            font-size: 12px;
            color: #6c757d;
            transition: transform 0.2s;
            margin-right: 8px;
        }}
        .expand-arrow.expanded {{ transform: rotate(90deg); }}
        .bp-version {{
            display: inline-block;
            background: #f3eaff;
            color: #6f42c1;
            padding: 1px 6px;
            border-radius: 4px;
            font-size: 11px;
            margin-left: 8px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>{dashboard_title}</h1>
            <p>{header_info}</p>
        </header>

        <div class="stats">
            <div class="stat-card">
                <div class="number">{totals['teams']}</div>
                <div class="label">Teams</div>
            </div>
            <div class="stat-card">
                <div class="number">{totals['features']}</div>
                <div class="label">Features</div>
            </div>
            <div class="stat-card" style="border-left-color: #dc3545;">
                <div class="number" style="color: #dc3545;">{totals['bugs']}</div>
                <div class="label">Bugs</div>
            </div>
            <div class="stat-card" style="border-left-color: #6f42c1;">
                <div class="number" style="color: #6f42c1;">{totals['backport_bugs']}</div>
                <div class="label">Backport Bugs</div>
            </div>
        </div>

        <div class="content">
            <h2 style="margin-bottom: 20px; color: #495057;">Teams</h2>
"""

    for team_name, ts in sorted_teams:
        features_pct = ts["features_pct"]
        bugs_pct = ts["bugs_pct"]
        children_pct = ts.get("children_pct", 0)
        feat_color = "#28a745" if features_pct == 100 else "#0066cc" if features_pct > 50 else "#ffc107" if features_pct > 0 else "#e9ecef"
        bugs_color = "#28a745" if bugs_pct == 100 else "#dc3545" if bugs_pct < 50 else "#ffc107"
        children_color = "#28a745" if children_pct == 100 else "#17a2b8" if children_pct > 50 else "#ffc107" if children_pct > 0 else "#e9ecef"
        safe_id = re.sub(r'[^a-zA-Z0-9]', '-', team_name)

        html += f"""
            <div class="team-card">
                <div class="team-header" onclick="toggleTeam('{safe_id}')" style="grid-template-columns: 1fr 130px 130px 130px 130px 130px;">
                    <div class="team-name"><span class="expand-arrow" id="arrow-{safe_id}">▶</span>{team_name}</div>
                    <div class="team-metric">
                        <div class="metric-num color-features">{ts['features']}</div>
                        <div class="metric-label">Features</div>
                        <div class="progress-bar"><div class="progress-fill" style="width:{features_pct}%;background:{feat_color};"></div></div>
                        <div class="metric-label">{features_pct}% done</div>
                    </div>
                    <div class="team-metric">
                        <div class="metric-num" style="color:#17a2b8;">{ts.get('children_count', 0)}</div>
                        <div class="metric-label">Epics+Stories</div>
                        <div class="progress-bar"><div class="progress-fill" style="width:{children_pct}%;background:{children_color};"></div></div>
                        <div class="metric-label">{children_pct}% done</div>
                    </div>
                    <div class="team-metric">
                        <div class="metric-num color-bugs">{ts['bugs']}</div>
                        <div class="metric-label">Bugs</div>
                        <div class="progress-bar"><div class="progress-fill" style="width:{bugs_pct}%;background:{bugs_color};"></div></div>
                        <div class="metric-label">{bugs_pct}% done</div>
                    </div>
                    <div class="team-metric">
                        <div class="metric-num color-backport">{ts['backport_bugs']}</div>
                        <div class="metric-label">Backport Bugs</div>
                    </div>
                    <div class="team-metric">
                        <div class="metric-num" style="color:#495057;">{ts['features'] + ts.get('children_count', 0) + ts['bugs'] + ts['backport_bugs']}</div>
                        <div class="metric-label">Total Items</div>
                    </div>
                </div>
                <div class="team-details" id="details-{safe_id}">
"""

        # Feature hierarchy
        if ts["feature_hierarchy"]:
            html += '                    <div class="detail-section"><h4>Features</h4>\n'
            for f in ts["feature_hierarchy"]:
                status_class = get_status_class(f["status"])
                epic_count = f.get("epic_count", 0)
                story_count = f.get("story_count", 0)
                child_info = f'<span style="color:#6c757d;font-size:12px;margin-left:8px;">{epic_count} epics, {story_count} stories</span>' if epic_count else ''
                f_safe_id = re.sub(r'[^a-zA-Z0-9]', '-', f["key"])
                html += f"""                        <div style="margin-bottom:8px;">
                            <div class="issue-row" style="cursor:pointer;" onclick="toggleFeature('{f_safe_id}')">
                                <a href="{JIRA_BASE}/browse/{f['key']}" class="issue-key" target="_blank" onclick="event.stopPropagation()">{f['key']}</a>
                                <span><span class="expand-arrow" id="farrow-{f_safe_id}" style="font-size:10px;">▶</span>{f['summary']}{child_info}</span>
                                <span class="status-badge {status_class}">{f['status']}</span>
                                <span style="color:#6c757d;font-size:13px;">{f.get('assignee','')}</span>
                            </div>
                            <div id="fdetails-{f_safe_id}" style="display:none;margin-left:20px;">\n"""

                for e in f.get("epics", []):
                    e_status_class = get_status_class(e["status"])
                    e_story_count = len(e.get("stories", []))
                    e_safe_id = re.sub(r'[^a-zA-Z0-9]', '-', e["key"])
                    html += f"""                                <div style="margin:4px 0;">
                                    <div class="issue-row" style="background:#fff8e1;border-radius:4px;padding:6px 8px;cursor:pointer;" onclick="toggleFeature('{e_safe_id}')">
                                        <a href="{JIRA_BASE}/browse/{e['key']}" class="issue-key" target="_blank" onclick="event.stopPropagation()">{e['key']}</a>
                                        <span><span class="expand-arrow" id="farrow-{e_safe_id}" style="font-size:10px;">▶</span>{e['summary']} <span style="color:#6c757d;font-size:12px;">({e_story_count} stories)</span></span>
                                        <span class="status-badge {e_status_class}">{e['status']}</span>
                                        <span style="color:#6c757d;font-size:13px;">{e.get('assignee','')}</span>
                                    </div>
                                    <div id="fdetails-{e_safe_id}" style="display:none;margin-left:20px;">\n"""

                    for s in e.get("stories", []):
                        s_status_class = get_status_class(s["status"])
                        html += f"""                                        <div class="issue-row" style="padding:4px 8px;font-size:13px;">
                                            <a href="{JIRA_BASE}/browse/{s['key']}" class="issue-key" target="_blank" style="font-size:13px;">{s['key']}</a>
                                            <span>{s['summary']}</span>
                                            <span class="status-badge {s_status_class}" style="font-size:10px;">{s['status']}</span>
                                            <span style="color:#6c757d;font-size:12px;">{s.get('assignee','')}</span>
                                        </div>\n"""

                    html += '                                    </div>\n                                </div>\n'

                html += '                            </div>\n                        </div>\n'
            html += '                    </div>\n'

        # Bugs grouped by status
        if ts["bug_list"]:
            bugs_by_status = {}
            for b in ts["bug_list"]:
                bugs_by_status.setdefault(b["status"], []).append(b)
            sorted_statuses = sorted(bugs_by_status.keys(), key=lambda s: (is_status_done(s), s))

            html += '                    <div class="detail-section"><h4>Bugs</h4>\n'
            for status in sorted_statuses:
                status_bugs = bugs_by_status[status]
                status_class = get_status_class(status)
                sg_safe_id = re.sub(r'[^a-zA-Z0-9]', '-', f"{safe_id}-bugs-{status}")
                html += f"""                        <div style="margin-bottom:6px;">
                            <div style="cursor:pointer;padding:6px 10px;background:#f8f9fa;border-radius:4px;display:flex;align-items:center;gap:10px;" onclick="toggleFeature('{sg_safe_id}')">
                                <span class="expand-arrow" id="farrow-{sg_safe_id}" style="font-size:10px;">▶</span>
                                <span class="status-badge {status_class}">{status}</span>
                                <span style="font-weight:600;font-size:14px;">{len(status_bugs)} bugs</span>
                            </div>
                            <div id="fdetails-{sg_safe_id}" style="display:none;margin-left:20px;">\n"""
                for b in status_bugs:
                    b_status_class = get_status_class(b["status"])
                    priority = f'<span style="color:#dc3545;font-size:11px;font-weight:600;">[{b.get("priority","")}]</span> ' if b.get("priority") else ""
                    html += f"""                                <div class="issue-row" style="font-size:13px;padding:4px 8px;">
                                    <a href="{JIRA_BASE}/browse/{b['key']}" class="issue-key" target="_blank" style="font-size:13px;">{b['key']}</a>
                                    <span>{priority}{b['summary']}</span>
                                    <span class="status-badge {b_status_class}" style="font-size:10px;">{b['status']}</span>
                                    <span style="color:#6c757d;font-size:12px;">{b.get('assignee','')}</span>
                                </div>\n"""
                html += '                            </div>\n                        </div>\n'
            html += '                    </div>\n'

        # Backport bugs grouped by version then status
        if ts["backport_list"]:
            bp_by_version = {}
            for b in ts["backport_list"]:
                bp_by_version.setdefault(b.get("backport_version", ""), []).append(b)

            html += '                    <div class="detail-section"><h4>Backport Bugs</h4>\n'
            for bp_ver, bp_bugs in sorted(bp_by_version.items()):
                bp_by_status = {}
                for b in bp_bugs:
                    bp_by_status.setdefault(b["status"], []).append(b)
                bp_sorted = sorted(bp_by_status.keys(), key=lambda s: (is_status_done(s), s))

                bv_safe_id = re.sub(r'[^a-zA-Z0-9]', '-', f"{safe_id}-bp-{bp_ver}")
                html += f"""                        <div style="margin-bottom:6px;">
                            <div style="cursor:pointer;padding:6px 10px;background:#f3eaff;border-radius:4px;display:flex;align-items:center;gap:10px;" onclick="toggleFeature('{bv_safe_id}')">
                                <span class="expand-arrow" id="farrow-{bv_safe_id}" style="font-size:10px;">▶</span>
                                <span style="font-weight:600;color:#6f42c1;">{bp_ver}</span>
                                <span style="font-size:14px;">{len(bp_bugs)} bugs</span>
                            </div>
                            <div id="fdetails-{bv_safe_id}" style="display:none;margin-left:20px;">\n"""
                for status in bp_sorted:
                    for b in bp_by_status[status]:
                        b_status_class = get_status_class(b["status"])
                        html += f"""                                <div class="issue-row" style="font-size:13px;padding:4px 8px;">
                                    <a href="{JIRA_BASE}/browse/{b['key']}" class="issue-key" target="_blank" style="font-size:13px;">{b['key']}</a>
                                    <span>{b['summary']}</span>
                                    <span class="status-badge {b_status_class}" style="font-size:10px;">{b['status']}</span>
                                    <span style="color:#6c757d;font-size:12px;">{b.get('assignee','')}</span>
                                </div>\n"""
                html += '                            </div>\n                        </div>\n'
            html += '                    </div>\n'

        html += """
                </div>
            </div>
"""

    html += """
        </div>
    </div>

    <script>
        function toggleTeam(id) {
            const details = document.getElementById('details-' + id);
            const arrow = document.getElementById('arrow-' + id);
            details.classList.toggle('expanded');
            arrow.classList.toggle('expanded');
        }
        function toggleFeature(id) {
            const details = document.getElementById('fdetails-' + id);
            const arrow = document.getElementById('farrow-' + id);
            if (details) {
                const isHidden = details.style.display === 'none';
                details.style.display = isHidden ? 'block' : 'none';
                if (arrow) arrow.classList.toggle('expanded');
            }
        }
    </script>
</body>
</html>
"""

    return html


def generate_html(data: dict[str, Any], label: str, version: str, is_comprehensive: bool = False) -> str:
    """Generate the HTML dashboard."""

    stats = data["stats"]
    features = data["features"]
    team_name = data.get("team_name", "")

    # Comprehensive view has additional sections
    independent_epics = data.get("independent_epics", [])
    independent_stories = data.get("independent_stories", [])
    bugs_by_status = data.get("bugs_by_status", {})
    backports = data.get("backports", {})

    # Build header info line
    header_info = f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    if team_name:
        header_info += f" | Team: {team_name}"

    # Extract project name from label if possible (e.g., "rhdh-2.1-candidate" -> "RHDH")
    project_match = re.match(r'^([a-zA-Z]+)', label)
    project_name = project_match.group(1).upper() if project_match else ""

    dashboard_title = f"{project_name} {version} Release Dashboard" if project_name else f"{version} Release Dashboard"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{dashboard_title}</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background: #f5f5f5;
            padding: 20px;
            line-height: 1.6;
        }}

        .container {{
            max-width: 1400px;
            margin: 0 auto;
            background: white;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            overflow: hidden;
        }}

        header {{
            background: linear-gradient(135deg, #0066cc 0%, #004499 100%);
            color: white;
            padding: 30px;
        }}

        header h1 {{
            font-size: 28px;
            margin-bottom: 10px;
        }}

        header p {{
            opacity: 0.9;
            font-size: 14px;
        }}

        .stats {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            padding: 30px;
            background: #f8f9fa;
            border-bottom: 1px solid #dee2e6;
        }}

        .stat-card {{
            background: white;
            padding: 20px;
            border-radius: 8px;
            border-left: 4px solid #0066cc;
        }}

        .stat-card .number {{
            font-size: 32px;
            font-weight: bold;
            color: #0066cc;
        }}

        .stat-card .label {{
            color: #6c757d;
            font-size: 14px;
            margin-top: 5px;
        }}

        .content {{
            padding: 30px;
        }}

        .tree {{
            list-style: none;
        }}

        .tree-item {{
            margin: 15px 0;
        }}

        .tree-node {{
            background: #f8f9fa;
            border: 1px solid #dee2e6;
            border-radius: 6px;
            padding: 15px;
            cursor: pointer;
            transition: all 0.2s;
        }}

        .tree-node:hover {{
            background: #e9ecef;
            border-color: #adb5bd;
        }}

        .tree-node.feature {{
            background: #e7f3ff;
            border-color: #0066cc;
        }}

        .tree-node.epic {{
            background: #fff4e6;
            border-color: #ff9800;
            margin-left: 30px;
        }}

        .tree-node.issue {{
            background: white;
            border-color: #dee2e6;
            margin-left: 60px;
            font-size: 14px;
        }}

        .node-header {{
            display: grid;
            grid-template-columns: 20px 150px 1fr 150px 150px 200px;
            align-items: center;
            gap: 10px;
        }}

        .expand-icon {{
            font-size: 12px;
            color: #6c757d;
            transition: transform 0.2s;
        }}

        .expand-icon.expanded {{
            transform: rotate(90deg);
        }}

        .issue-key {{
            font-weight: 600;
            color: #0066cc;
            text-decoration: none;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}

        .issue-key:hover {{
            text-decoration: underline;
        }}

        .status-badge {{
            display: inline-block;
            padding: 3px 10px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: 500;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}

        .status-done {{
            background: #d4edda;
            color: #155724;
        }}

        .status-progress {{
            background: #fff3cd;
            color: #856404;
        }}

        .status-todo {{
            background: #e2e3e5;
            color: #383d41;
        }}

        .status-blocked {{
            background: #f8d7da;
            color: #721c24;
        }}

        .assignee {{
            display: inline-block;
            padding: 3px 10px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: 500;
            background: #e7f3ff;
            color: #004085;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}

        .assignee:empty {{
            display: none;
        }}

        /* Issue level (no expand icon, no progress bar) */
        .issue .node-header {{
            grid-template-columns: 150px 1fr 150px 200px;
        }}

        .summary {{
            overflow: hidden;
            text-overflow: ellipsis;
        }}

        .progress-bar {{
            width: 150px;
            height: 24px;
            background: #e9ecef;
            border-radius: 12px;
            overflow: hidden;
            position: relative;
            flex-shrink: 0;
        }}

        .progress-fill {{
            height: 100%;
            background: linear-gradient(90deg, #28a745 0%, #20c997 100%);
            transition: width 0.3s;
            display: flex;
            align-items: center;
            justify-content: center;
            color: white;
            font-size: 12px;
            font-weight: 600;
        }}

        .progress-fill.low {{
            background: linear-gradient(90deg, #dc3545 0%, #c82333 100%);
        }}

        .progress-fill.medium {{
            background: linear-gradient(90deg, #ffc107 0%, #ff9800 100%);
        }}

        .summary {{
            flex: 1;
            min-width: 300px;
        }}

        .pr-badges {{
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
        }}

        .pr-badge {{
            display: inline-flex;
            align-items: center;
            gap: 5px;
            padding: 4px 10px;
            border-radius: 12px;
            font-size: 12px;
            text-decoration: none;
            color: white;
            font-weight: 500;
        }}

        .pr-merged {{
            background: #6f42c1;
        }}

        .pr-open {{
            background: #28a745;
        }}

        .pr-draft {{
            background: #6c757d;
        }}

        .pr-failing {{
            background: #dc3545;
        }}

        .pr-passing {{
            background: #20c997;
        }}

        .pr-approved {{
            background: #17a2b8;
        }}

        .pr-pending {{
            background: #ffc107;
            color: #000;
        }}

        .pr-unknown {{
            background: #adb5bd;
        }}

        .children {{
            display: none;
            margin-top: 15px;
        }}

        .children.expanded {{
            display: block;
        }}

        .empty-state {{
            text-align: center;
            padding: 60px 20px;
            color: #6c757d;
        }}

        .empty-state h2 {{
            margin-bottom: 10px;
        }}

        @media (max-width: 768px) {{
            .node-header {{
                grid-template-columns: 20px 1fr;
                row-gap: 8px;
            }}

            .issue .node-header {{
                grid-template-columns: 1fr;
            }}

            .progress-bar {{
                width: 100%;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>{dashboard_title}</h1>
            <p>{header_info}</p>
        </header>

        <div class="stats">
            <div class="stat-card">
                <div class="number">{stats['features']}</div>
                <div class="label">Features</div>
            </div>
            <div class="stat-card">
                <div class="number">{stats['epics']}</div>
                <div class="label">Epics</div>
            </div>
            <div class="stat-card">
                <div class="number">{stats['issues']}</div>
                <div class="label">Issues</div>
            </div>"""

    # Add bugs stat if present
    if 'bugs' in stats and stats['bugs'] > 0:
        html += f"""
            <div class="stat-card">
                <div class="number">{stats['bugs']}</div>
                <div class="label">Bugs</div>
            </div>"""

    # Add backport stats if present
    if stats.get('backport_bugs', 0) > 0:
        html += f"""
            <div class="stat-card" style="border-left-color: #6f42c1;">
                <div class="number" style="color: #6f42c1;">{stats['backport_bugs']}</div>
                <div class="label">Backport Bugs ({stats['backport_versions']} versions)</div>
            </div>"""

    html += f"""
            <div class="stat-card">
                <div class="number">{stats['prs']}</div>
                <div class="label">Pull Requests</div>
            </div>
        </div>

        <div class="content">
"""

    if not features:
        html += """
            <div class="empty-state">
                <h2>No features found</h2>
                <p>No features found with the label: """ + label + """</p>
            </div>
"""
    else:
        html += '<ul class="tree">\n'

        for feature in features:
            completion = feature["completion"]
            progress_class = "low" if completion < 33 else "medium" if completion < 67 else ""
            status_class = get_status_class(feature["status"])

            assignee = feature.get('assignee', '')
            html += f"""
                <li class="tree-item">
                    <div class="tree-node feature" onclick="toggleNode(event, this)">
                        <div class="node-header">
                            <span class="expand-icon">▶</span>
                            <a href="{JIRA_BASE}/browse/{feature['key']}" class="issue-key" target="_blank" onclick="event.stopPropagation()">{feature['key']}</a>
                            <span class="summary">{feature['summary']}</span>
                            <div class="progress-bar">
                                <div class="progress-fill {progress_class}" style="width: {completion}%">{completion}%</div>
                            </div>
                            <span class="status-badge {status_class}">{feature['status']}</span>
                            <span class="assignee">{assignee}</span>
                        </div>
                        <div class="children">
                            <ul class="tree">
"""

            for epic in feature.get("epics", []):
                epic_completion = epic["completion"]
                epic_progress_class = "low" if epic_completion < 33 else "medium" if epic_completion < 67 else ""
                epic_status_class = get_status_class(epic["status"])
                epic_assignee = epic.get('assignee', '')

                html += f"""
                                <li class="tree-item">
                                    <div class="tree-node epic" onclick="toggleNode(event, this)">
                                        <div class="node-header">
                                            <span class="expand-icon">▶</span>
                                            <a href="{JIRA_BASE}/browse/{epic['key']}" class="issue-key" target="_blank" onclick="event.stopPropagation()">{epic['key']}</a>
                                            <span class="summary">{epic['summary']}</span>
                                            <div class="progress-bar">
                                                <div class="progress-fill {epic_progress_class}" style="width: {epic_completion}%">{epic_completion}%</div>
                                            </div>
                                            <span class="status-badge {epic_status_class}">{epic['status']}</span>
                                            <span class="assignee">{epic_assignee}</span>
                                        </div>
                                        <div class="children">
                                            <ul class="tree">
"""

                for issue in epic.get("issues", []):
                    issue_status_class = get_status_class(issue["status"])
                    issue_assignee = issue.get('assignee', '')
                    pr_badges_html = ""

                    if issue.get("prs"):
                        pr_badges_html = '<div class="pr-badges">'
                        for pr in issue["prs"]:
                            pr_state = pr["state"]
                            pr_number = pr.get("number", "PR")
                            pr_badges_html += f'<a href="{pr["url"]}" class="pr-badge pr-{pr_state}" target="_blank" onclick="event.stopPropagation()">#{pr_number} {pr_state}</a>'
                        pr_badges_html += '</div>'

                    html += f"""
                                                <li class="tree-item">
                                                    <div class="tree-node issue">
                                                        <div class="node-header">
                                                            <a href="{JIRA_BASE}/browse/{issue['key']}" class="issue-key" target="_blank">{issue['key']}</a>
                                                            <span class="summary">{issue['summary']}</span>
                                                            <span class="status-badge {issue_status_class}">{issue['status']}</span>
                                                            <span class="assignee">{issue_assignee}</span>
                                                        </div>
                                                        {pr_badges_html}
                                                    </div>
                                                </li>
"""

                html += """
                                            </ul>
                                        </div>
                                    </div>
                                </li>
"""

            html += """
                            </ul>
                        </div>
                    </div>
                </li>
"""

        html += '</ul>\n'

    # Independent Epics and Stories sections removed per user request
    # Only showing Features (with hierarchy) and Bugs

    # Render Independent Epics section (DISABLED)
    if False and independent_epics:
        html += '<h2 style="margin-top: 40px; margin-bottom: 20px; color: #495057;">Independent Epics</h2>\n'
        html += '<ul class="tree">\n'

        for epic in independent_epics:
            epic_completion = epic["completion"]
            epic_progress_class = "low" if epic_completion < 33 else "medium" if epic_completion < 67 else ""
            epic_status_class = get_status_class(epic["status"])
            epic_assignee = epic.get('assignee', '')

            html += f"""
                <li class="tree-item">
                    <div class="tree-node epic" onclick="toggleNode(event, this)">
                        <div class="node-header">
                            <span class="expand-icon">▶</span>
                            <a href="{JIRA_BASE}/browse/{epic['key']}" class="issue-key" target="_blank" onclick="event.stopPropagation()">{epic['key']}</a>
                            <span class="summary">{epic['summary']}</span>
                            <div class="progress-bar">
                                <div class="progress-fill {epic_progress_class}" style="width: {epic_completion}%">{epic_completion}%</div>
                            </div>
                            <span class="status-badge {epic_status_class}">{epic['status']}</span>
                            <span class="assignee">{epic_assignee}</span>
                        </div>
                        <div class="children">
                            <ul class="tree">
"""

            for issue in epic.get("issues", []):
                issue_status_class = get_status_class(issue["status"])
                issue_assignee = issue.get('assignee', '')
                pr_badges_html = ""

                if issue.get("prs"):
                    pr_badges_html = '<div class="pr-badges">'
                    for pr in issue["prs"]:
                        pr_state = pr["state"]
                        pr_number = pr.get("number", "PR")
                        pr_badges_html += f'<a href="{pr["url"]}" class="pr-badge pr-{pr_state}" target="_blank" onclick="event.stopPropagation()">#{pr_number} {pr_state}</a>'
                    pr_badges_html += '</div>'

                html += f"""
                                <li class="tree-item">
                                    <div class="tree-node issue">
                                        <div class="node-header">
                                            <a href="{JIRA_BASE}/browse/{issue['key']}" class="issue-key" target="_blank">{issue['key']}</a>
                                            <span class="summary">{issue['summary']}</span>
                                            <span class="status-badge {issue_status_class}">{issue['status']}</span>
                                            <span class="assignee">{issue_assignee}</span>
                                        </div>
                                        {pr_badges_html}
                                    </div>
                                </li>
"""

            html += """
                            </ul>
                        </div>
                    </div>
                </li>
"""

        html += '</ul>\n'

    # Render Independent Stories section (DISABLED)
    if False and independent_stories:
        html += '<h2 style="margin-top: 40px; margin-bottom: 20px; color: #495057;">Independent Stories & Tasks</h2>\n'
        html += '<ul class="tree">\n'

        for issue in independent_stories:
            issue_status_class = get_status_class(issue["status"])
            issue_assignee = issue.get('assignee', '')
            pr_badges_html = ""

            if issue.get("prs"):
                pr_badges_html = '<div class="pr-badges">'
                for pr in issue["prs"]:
                    pr_state = pr["state"]
                    pr_number = pr.get("number", "PR")
                    pr_badges_html += f'<a href="{pr["url"]}" class="pr-badge pr-{pr_state}" target="_blank" onclick="event.stopPropagation()">#{pr_number} {pr_state}</a>'
                pr_badges_html += '</div>'

            html += f"""
                <li class="tree-item">
                    <div class="tree-node issue">
                        <div class="node-header">
                            <a href="{JIRA_BASE}/browse/{issue['key']}" class="issue-key" target="_blank">{issue['key']}</a>
                            <span class="summary">{issue['summary']}</span>
                            <span class="status-badge {issue_status_class}">{issue['status']}</span>
                            <span class="assignee">{issue_assignee}</span>
                        </div>
                        {pr_badges_html}
                    </div>
                </li>
"""

        html += '</ul>\n'

    # Render Bugs section grouped by status
    if bugs_by_status:
        html += '<h2 style="margin-top: 40px; margin-bottom: 20px; color: #495057;">Bugs</h2>\n'

        # Sort statuses - done statuses last
        sorted_statuses = sorted(bugs_by_status.keys(), key=lambda s: (is_status_done(s), s))

        for status in sorted_statuses:
            bugs = bugs_by_status[status]
            status_class = get_status_class(status)
            bug_count = len(bugs)

            html += f"""
            <div style="margin-bottom: 20px;">
                <div class="tree-node" style="background: #f8f9fa; margin-left: 0;" onclick="toggleNode(event, this)">
                    <div class="node-header">
                        <span class="expand-icon">▶</span>
                        <span style="font-weight: 600;">{status} ({bug_count})</span>
                        <span></span>
                        <span></span>
                        <span class="status-badge {status_class}">{status}</span>
                        <span></span>
                    </div>
                    <div class="children">
                        <ul class="tree">
"""

            for bug in bugs:
                bug_status_class = get_status_class(bug["status"])
                bug_assignee = bug.get('assignee', '')
                bug_priority = bug.get('priority', '')
                pr_badges_html = ""

                if bug.get("prs"):
                    pr_badges_html = '<div class="pr-badges">'
                    for pr in bug["prs"]:
                        pr_state = pr["state"]
                        pr_number = pr.get("number", "PR")
                        pr_badges_html += f'<a href="{pr["url"]}" class="pr-badge pr-{pr_state}" target="_blank" onclick="event.stopPropagation()">#{pr_number} {pr_state}</a>'
                    pr_badges_html += '</div>'

                priority_badge = f'<span style="font-size: 11px; color: #dc3545; font-weight: 600;">[{bug_priority}]</span> ' if bug_priority else ''

                html += f"""
                            <li class="tree-item">
                                <div class="tree-node issue">
                                    <div class="node-header">
                                        <a href="{JIRA_BASE}/browse/{bug['key']}" class="issue-key" target="_blank">{bug['key']}</a>
                                        <span class="summary">{priority_badge}{bug['summary']}</span>
                                        <span class="status-badge {bug_status_class}">{bug['status']}</span>
                                        <span class="assignee">{bug_assignee}</span>
                                    </div>
                                    {pr_badges_html}
                                </div>
                            </li>
"""

            html += """
                        </ul>
                    </div>
                </div>
            </div>
"""

    # Render Backports section
    if backports:
        total_bp = sum(bp["total"] for bp in backports.values())
        html += f'<h2 style="margin-top: 40px; margin-bottom: 10px; color: #495057;">Backports <span style="font-size: 16px; color: #6c757d;">({total_bp} bugs across {len(backports)} unreleased versions)</span></h2>\n'

        for bp_version, bp_data in sorted(backports.items(), key=lambda x: x[0], reverse=True):
            bp_total = bp_data["total"]
            bp_bugs_by_status = bp_data["bugs_by_status"]
            bp_done = sum(len(bugs) for st, bugs in bp_bugs_by_status.items() if is_status_done(st))
            bp_pct = int((bp_done / bp_total) * 100) if bp_total else 0

            html += f"""
            <div style="margin-bottom: 20px;">
                <div class="tree-node" style="background: #f3eaff; border-color: #6f42c1; margin-left: 0;" onclick="toggleNode(event, this)">
                    <div class="node-header">
                        <span class="expand-icon">▶</span>
                        <span style="font-weight: 600; color: #6f42c1;">{bp_version}</span>
                        <span style="font-size: 13px; color: #6c757d;">{bp_total} bugs &middot; {bp_done} done</span>
                        <span></span>
                        <span>
                            <div style="background: #e9ecef; border-radius: 4px; height: 8px; width: 100px; display: inline-block; vertical-align: middle;">
                                <div style="background: {'#28a745' if bp_pct == 100 else '#6f42c1'}; border-radius: 4px; height: 8px; width: {bp_pct}%;"></div>
                            </div>
                            <span style="font-size: 12px; margin-left: 5px;">{bp_pct}%</span>
                        </span>
                        <span></span>
                    </div>
                    <div class="children">
"""
            sorted_statuses = sorted(bp_bugs_by_status.keys(), key=lambda s: (is_status_done(s), s))

            for status in sorted_statuses:
                bugs = bp_bugs_by_status[status]
                status_class = get_status_class(status)
                bug_count = len(bugs)

                html += f"""
                        <div style="margin: 10px 0 10px 20px;">
                            <div class="tree-node" style="background: #f8f9fa; margin-left: 0;" onclick="toggleNode(event, this)">
                                <div class="node-header">
                                    <span class="expand-icon">▶</span>
                                    <span style="font-weight: 600;">{status} ({bug_count})</span>
                                    <span></span>
                                    <span></span>
                                    <span class="status-badge {status_class}">{status}</span>
                                    <span></span>
                                </div>
                                <div class="children">
                                    <ul class="tree">
"""

                for bug in bugs:
                    bug_status_class = get_status_class(bug["status"])
                    bug_assignee = bug.get('assignee', '')
                    bug_priority = bug.get('priority', '')
                    pr_badges_html = ""

                    if bug.get("prs"):
                        pr_badges_html = '<div class="pr-badges">'
                        for pr in bug["prs"]:
                            pr_state = pr["state"]
                            pr_number = pr.get("number", "PR")
                            pr_badges_html += f'<a href="{pr["url"]}" class="pr-badge pr-{pr_state}" target="_blank" onclick="event.stopPropagation()">#{pr_number} {pr_state}</a>'
                        pr_badges_html += '</div>'

                    priority_badge = f'<span style="font-size: 11px; color: #dc3545; font-weight: 600;">[{bug_priority}]</span> ' if bug_priority else ''

                    html += f"""
                                        <li class="tree-item">
                                            <div class="tree-node issue">
                                                <div class="node-header">
                                                    <a href="{JIRA_BASE}/browse/{bug['key']}" class="issue-key" target="_blank">{bug['key']}</a>
                                                    <span class="summary">{priority_badge}{bug['summary']}</span>
                                                    <span class="status-badge {bug_status_class}">{bug['status']}</span>
                                                    <span class="assignee">{bug_assignee}</span>
                                                </div>
                                                {pr_badges_html}
                                            </div>
                                        </li>
"""

                html += """
                                    </ul>
                                </div>
                            </div>
                        </div>
"""

            html += """
                    </div>
                </div>
            </div>
"""

    html += """
        </div>
    </div>

    <script>
        function toggleNode(event, node) {
            event.stopPropagation(); // Prevent event from bubbling to parent nodes

            const children = node.querySelector('.children');
            const icon = node.querySelector('.expand-icon');

            if (children) {
                children.classList.toggle('expanded');
                icon.classList.toggle('expanded');
            }
        }

        // Expand all features by default
        document.addEventListener('DOMContentLoaded', () => {
            document.querySelectorAll('.tree-node.feature').forEach(node => {
                const children = node.querySelector('.children');
                const icon = node.querySelector('.expand-icon');
                if (children) {
                    children.classList.add('expanded');
                    icon.classList.add('expanded');
                }
            });
        });
    </script>
</body>
</html>
"""

    return html


def is_status_done(status: str) -> bool:
    """Check if a status represents completion."""
    status_lower = status.lower()
    return ("done" in status_lower or
            "closed" in status_lower or
            "resolved" in status_lower or
            "complete" in status_lower or      # Matches "Dev Complete", "QA Complete", etc.
            "release pending" in status_lower) # Work is done, waiting for release


def get_status_class(status: str) -> str:
    """Get CSS class for status badge."""
    status_lower = status.lower()
    if is_status_done(status):
        return "status-done"
    elif "progress" in status_lower or "review" in status_lower:
        return "status-progress"
    elif "block" in status_lower:
        return "status-blocked"
    else:
        return "status-todo"


def open_in_browser(file_path: Path):
    """Open HTML file in default browser."""
    system = platform.system()

    try:
        if system == "Darwin":  # macOS
            subprocess.run(["open", str(file_path)], check=True)
        elif system == "Linux":
            subprocess.run(["xdg-open", str(file_path)], check=True)
        elif system == "Windows":
            subprocess.run(["start", str(file_path)], shell=True, check=True)
        else:
            print(f"Please open manually: {file_path}")
    except subprocess.CalledProcessError:
        print(f"Could not open browser. Please open manually: {file_path}")


def generate_demo_data(version: str) -> dict[str, Any]:
    """Generate realistic demo data for testing the dashboard without JIRA access."""
    print(f"Generating demo data for RHDH {version}...")

    demo_features = [
        {
            "key": "RHDHPLAN-1722",
            "summary": "New Plugin System Architecture",
            "status": "In Progress",
            "type": "Feature",
            "completion": 65,
            "epics": [
                {
                    "key": "RHDH-2001",
                    "summary": "Backend Plugin Framework",
                    "status": "Done",
                    "type": "Epic",
                    "completion": 100,
                    "issues": [
                        {
                            "key": "RHDH-2002",
                            "summary": "Implement createBackendPlugin API",
                            "status": "Done",
                            "type": "Story",
                            "assignee": "Alice Developer",
                            "prs": [
                                {"url": "https://github.com/redhat-developer/rhdh/pull/456", "number": "456", "state": "merged", "merged": True}
                            ]
                        },
                        {
                            "key": "RHDH-2003",
                            "summary": "Add extension points system",
                            "status": "Done",
                            "type": "Story",
                            "assignee": "Bob Engineer",
                            "prs": [
                                {"url": "https://github.com/redhat-developer/rhdh/pull/457", "number": "457", "state": "merged", "merged": True}
                            ]
                        }
                    ],
                    "prs": []
                },
                {
                    "key": "RHDH-2010",
                    "summary": "Frontend Plugin Migration",
                    "status": "In Progress",
                    "type": "Epic",
                    "completion": 50,
                    "issues": [
                        {
                            "key": "RHDH-2011",
                            "summary": "Update component library to NFS",
                            "status": "In Progress",
                            "type": "Story",
                            "assignee": "Charlie Developer",
                            "prs": [
                                {"url": "https://github.com/redhat-developer/rhdh/pull/458", "number": "458", "state": "open", "merged": False}
                            ]
                        },
                        {
                            "key": "RHDH-2012",
                            "summary": "Create frontend blueprints",
                            "status": "To Do",
                            "type": "Story",
                            "assignee": "Diana Coder",
                            "prs": []
                        },
                        {
                            "key": "RHDH-2013",
                            "summary": "Integration testing",
                            "status": "In Progress",
                            "type": "Task",
                            "assignee": "Eve QA",
                            "prs": [
                                {"url": "https://github.com/redhat-developer/rhdh/pull/459", "number": "459", "state": "draft", "merged": False}
                            ]
                        }
                    ],
                    "prs": []
                },
                {
                    "key": "RHDH-2020",
                    "summary": "Documentation Updates",
                    "status": "To Do",
                    "type": "Epic",
                    "completion": 0,
                    "issues": [
                        {
                            "key": "RHDH-2021",
                            "summary": "Write migration guide",
                            "status": "To Do",
                            "type": "Task",
                            "assignee": "",
                            "prs": []
                        }
                    ],
                    "prs": []
                }
            ],
            "prs": []
        },
        {
            "key": "RHDHPLAN-1800",
            "summary": "Performance Improvements",
            "status": "In Progress",
            "type": "Feature",
            "completion": 40,
            "epics": [
                {
                    "key": "RHDH-2100",
                    "summary": "Database Query Optimization",
                    "status": "In Progress",
                    "type": "Epic",
                    "completion": 60,
                    "issues": [
                        {
                            "key": "RHDH-2101",
                            "summary": "Add connection pooling",
                            "status": "Done",
                            "type": "Story",
                            "assignee": "Frank Backend",
                            "prs": [
                                {"url": "https://github.com/redhat-developer/rhdh/pull/460", "number": "460", "state": "merged", "merged": True}
                            ]
                        },
                        {
                            "key": "RHDH-2102",
                            "summary": "Optimize catalog queries",
                            "status": "In Progress",
                            "type": "Story",
                            "assignee": "Grace DBA",
                            "prs": [
                                {"url": "https://github.com/redhat-developer/rhdh/pull/461", "number": "461", "state": "passing", "merged": False}
                            ]
                        },
                        {
                            "key": "RHDH-2103",
                            "summary": "Add query caching",
                            "status": "To Do",
                            "type": "Story",
                            "assignee": "Henry Cache",
                            "prs": []
                        }
                    ],
                    "prs": []
                },
                {
                    "key": "RHDH-2110",
                    "summary": "Frontend Bundle Size Reduction",
                    "status": "To Do",
                    "type": "Epic",
                    "completion": 0,
                    "issues": [
                        {
                            "key": "RHDH-2111",
                            "summary": "Tree-shake unused dependencies",
                            "status": "To Do",
                            "type": "Task",
                            "assignee": "",
                            "prs": []
                        },
                        {
                            "key": "RHDH-2112",
                            "summary": "Enable code splitting",
                            "status": "To Do",
                            "type": "Task",
                            "assignee": "",
                            "prs": []
                        }
                    ],
                    "prs": []
                }
            ],
            "prs": []
        },
        {
            "key": "RHDHPLAN-1900",
            "summary": "Security Enhancements",
            "status": "Done",
            "type": "Feature",
            "completion": 100,
            "epics": [
                {
                    "key": "RHDH-2200",
                    "summary": "Authentication Hardening",
                    "status": "Done",
                    "type": "Epic",
                    "completion": 100,
                    "issues": [
                        {
                            "key": "RHDH-2201",
                            "summary": "Implement MFA support",
                            "status": "Done",
                            "type": "Story",
                            "assignee": "Ivy Security",
                            "prs": [
                                {"url": "https://github.com/redhat-developer/rhdh/pull/462", "number": "462", "state": "merged", "merged": True}
                            ]
                        },
                        {
                            "key": "RHDH-2202",
                            "summary": "Add session timeout",
                            "status": "Done",
                            "type": "Story",
                            "assignee": "Jack Auth",
                            "prs": [
                                {"url": "https://github.com/redhat-developer/rhdh/pull/463", "number": "463", "state": "merged", "merged": True}
                            ]
                        }
                    ],
                    "prs": []
                }
            ],
            "prs": []
        }
    ]

    # Calculate stats
    total_epics = sum(len(f["epics"]) for f in demo_features)
    total_issues = sum(
        len(epic["issues"])
        for f in demo_features
        for epic in f["epics"]
    )
    total_prs = sum(
        len(issue["prs"])
        for f in demo_features
        for epic in f["epics"]
        for issue in epic["issues"]
    )

    return {
        "features": demo_features,
        "stats": {
            "features": len(demo_features),
            "epics": total_epics,
            "issues": total_issues,
            "prs": total_prs
        },
        "team_name": ""  # No team filter in demo mode
    }


def main():
    parser = argparse.ArgumentParser(
        description="Generate a comprehensive release dashboard from JIRA using fixVersion and/or label"
    )
    parser.add_argument(
        "--fix-version",
        help='JIRA fix version (e.g., "2.1.0", "1.0.0")'
    )
    parser.add_argument(
        "--label",
        help='JIRA label (e.g., "rhdh-2.1-candidate") - can be combined with --fix-version'
    )
    parser.add_argument(
        "--team",
        help='Optional: Filter by team name or ID (e.g., "RHDH Frontend Plugins & UI" or "ec74d716-...-2176")'
    )
    parser.add_argument(
        "--backport",
        help='Comma-separated backport fixVersions to include (e.g., "1.10.4" or "1.10.4,1.9.8")'
    )
    parser.add_argument(
        "--team-overview",
        action="store_true",
        help="Generate an additional team-grouped overview dashboard (all teams, no team filter)"
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Don't open browser automatically"
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Generate dashboard with demo data (no JIRA access required)"
    )

    args = parser.parse_args()

    # Validate: need at least one of fix-version or label
    if not args.demo and not args.fix_version and not args.label:
        parser.error("At least one of --fix-version or --label is required (or use --demo)")

    # Determine version string for display
    if args.fix_version and args.label:
        version = args.fix_version
        label = args.label
    elif args.fix_version:
        version = args.fix_version
        label = f"fixVersion={args.fix_version}"
    else:  # label only
        label = args.label
        # Try to extract version from label (e.g., "rhdh-2.1-candidate" -> "2.1")
        version_match = re.search(r'(\d+\.\d+)', label)
        version = version_match.group(1) if version_match else label

    # Use demo data if requested
    if args.demo:
        print("Demo mode enabled - using sample data")
        data = generate_demo_data(version)
        label = f"rhdh-{version}-candidate (DEMO)"
        has_gh = True  # Demo data includes PRs
    else:
        # Check dependencies
        print("Checking dependencies...")
        deps = check_dependencies()

        # Check for JIRA access (acli or API credentials)
        if not deps["acli"] and not deps["jira_token"]:
            print("Error: No JIRA access available.", file=sys.stderr)
            print("\nOption 1: Install acli (preferred, consistent with RHDH repo)", file=sys.stderr)
            print("  See: https://bobswift.atlassian.net/wiki/spaces/ACLI/overview", file=sys.stderr)
            print("\nOption 2: Use JIRA REST API (no acli needed)", file=sys.stderr)
            print("  1. Get API token from: https://id.atlassian.com/manage-profile/security/api-tokens", file=sys.stderr)
            print("     Click 'Create API token'", file=sys.stderr)
            print("  2. Export credentials:", file=sys.stderr)
            print("     export JIRA_EMAIL=your-email@redhat.com", file=sys.stderr)
            print("     export JIRA_API_TOKEN=your_token_here", file=sys.stderr)
            print("\nOption 3: Use demo mode", file=sys.stderr)
            print("  Add --demo flag to generate a dashboard with sample data", file=sys.stderr)
            return 1

        # Show which JIRA method is being used
        if deps["acli"]:
            print("Using acli for JIRA queries")
        else:
            print("Using JIRA REST API (JIRA_EMAIL and JIRA_API_TOKEN found)")

        has_gh = deps["gh"]
        if not has_gh:
            print("Warning: gh CLI not available. PR status will not be included.")

        # Parse backport versions from comma-separated input
        backport_versions = None
        if args.backport:
            backport_versions = [v.strip() for v in args.backport.split(",") if v.strip()]

        # Resolve team name to ID if needed
        team = args.team
        if team:
            team_id, team_name = resolve_team(team)
            if team_name:
                print(f"Resolved team: '{team}' → ID: {team_id} ({team_name})")
            team = team_id

        # Build comprehensive release view
        data = build_comprehensive_release_view(
            fix_version=args.fix_version,
            label=args.label,
            has_gh=has_gh,
            team=team,
            backport_versions=backport_versions
        )

    if data["stats"]["features"] == 0:
        print(f"\nNo features found with label: {label}")
        if args.team:
            print(f"Please verify the label and team exist in JIRA: {JIRA_BASE}/issues/?jql=labels%3D{label}")
        else:
            print(f"Please verify the label exists in JIRA: {JIRA_BASE}/issues/?jql=labels%3D{label}")
        return 1

    # Generate HTML
    print("\nGenerating dashboard...")
    html = generate_html(data, label, version, is_comprehensive=True)

    # Create output directory
    output_dir = Path("./output")
    output_dir.mkdir(exist_ok=True)

    # Write HTML file - use sanitized label for filename
    safe_label = re.sub(r'[^a-zA-Z0-9-]', '-', label)
    output_file = output_dir / f"{safe_label}-dashboard.html"
    output_file.write_text(html, encoding="utf-8")

    print(f"\n✅ Dashboard generated successfully!")
    print(f"   File: {output_file}")
    print(f"   Features: {data['stats']['features']}")
    print(f"   Epics: {data['stats']['epics']}")
    print(f"   Issues: {data['stats']['issues']}")
    if 'bugs' in data['stats'] and data['stats']['bugs'] > 0:
        print(f"   Bugs: {data['stats']['bugs']}")
    if data['stats'].get('backport_bugs', 0) > 0:
        print(f"   Backport Bugs: {data['stats']['backport_bugs']} ({data['stats']['backport_versions']} versions)")
    if has_gh and data['stats']['prs'] > 0:
        print(f"   PRs: {data['stats']['prs']}")

    # Generate team overview if requested
    if args.team_overview and not args.demo:
        print("\nGenerating team overview dashboard...")
        overview_data = build_team_overview(
            fix_version=args.fix_version,
            label=args.label,
            backport_versions=backport_versions
        )
        overview_html = generate_team_overview_html(overview_data, label, version)
        overview_file = output_dir / f"{safe_label}-team-overview.html"
        overview_file.write_text(overview_html, encoding="utf-8")
        print(f"\n✅ Team overview generated!")
        print(f"   File: {overview_file}")
        print(f"   Teams: {overview_data['totals']['teams']}")
        print(f"   Features: {overview_data['totals']['features']}")
        print(f"   Bugs: {overview_data['totals']['bugs']}")
        if overview_data['totals']['backport_bugs'] > 0:
            print(f"   Backport Bugs: {overview_data['totals']['backport_bugs']}")

    # Open in browser
    if not args.no_browser:
        print(f"\nOpening dashboard in browser...")
        open_in_browser(output_file)

    return 0


if __name__ == "__main__":
    sys.exit(main())
