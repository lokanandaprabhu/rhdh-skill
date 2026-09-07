---
name: rhdh-release-dashboard
description: >-
  Generate interactive HTML dashboards showing RHDH release progress with
  hierarchical tree view (Feature → Epic → Story/Task), completion percentages,
  bugs grouped by status, backport tracking, team overview, and linked GitHub
  PRs. Use for "dashboard for 2.1", "show 2.1 release progress", or "generate
  release dashboard for 2.2".
compatibility: "Python 3.9+ and uv; JIRA access via acli (preferred) OR JIRA_API_TOKEN environment variable for REST API; gh CLI for GitHub PR data (optional)."
---

# RHDH release dashboard

Generate interactive HTML dashboards showing hierarchical release progress with
clickable links, completion percentages, bugs by status, backport tracking, and
team-level overview.

## Input

- **--fix-version** and/or **--label** (at least one required) — Jira fixVersion
  or label for the release.
- **--team** (optional) — team name or ID to filter by a specific team. Accepts
  partial names (e.g., `"Frontend Plugins"`), which are resolved to the team ID
  via the Jira API.
- **--backport** (optional) — comma-separated backport fixVersions to include
  (e.g., `"1.10.4,1.9.8"`).
- **--team-overview** (optional) — generates an additional all-teams overview
  dashboard alongside the team-specific one.
- **--demo** — generate with sample data when Jira access is unavailable.

## Output

Two standalone HTML files in `./output/`:

- **Team dashboard** — `rhdh-{label}-dashboard.html` with full hierarchy,
  bugs, backports, and PR status.
- **Team overview** (when `--team-overview` is used) —
  `rhdh-{label}-team-overview.html` with all teams side by side, expandable
  Feature → Epic → Story hierarchy, and bugs grouped by status per team.

Opens automatically in the default browser (unless `--no-browser` is used).

## Dashboard features

- **Hierarchical tree**: Feature → Epic → Story/Task with expand/collapse
- **Progress bars**: Completion percentage at Feature and Epic levels
- **Status indicators**: Color-coded by Jira status (Done/In Progress/To Do/Blocked)
- **Bugs section**: Grouped by status with collapsible sections
- **Backport tracking**: Dedicated section per backport version with progress bars
- **Team overview**: All teams at a glance with feature, epic/story, bug, and
  backport counts with completion percentages
- **PR integration**: Shows linked GitHub PRs with merge status
- **Clickable links**: Direct links to Jira issues and GitHub PRs
- **Team name resolution**: Accepts team name or ID, resolves automatically
- **Flexible input**: fixVersion, label, or both (OR query)
- **Demo mode**: Generate with sample data when Jira access is unavailable

## Route

Load `workflows/generate-dashboard.md` which orchestrates:
1. Query Jira for features with the release label/fixVersion
2. Recursively fetch child epics and their issues
3. Query Jira for bugs (RHDHBUGS project)
4. Query backport versions for bugs
5. Query GitHub for linked PR status
6. Calculate completion percentages
7. Generate HTML with embedded CSS and JavaScript
8. Optionally generate team overview (all teams grouped)
9. Open in browser

## Boundary with neighboring skills

- Release status numbers and Slack reports: `/rhdh-release-status`
- Release schedule and milestone dates: `/rhdh-release-schedule`
- Team rosters and assignments: `/rhdh-release-teams`
- Jira reads/writes: `/rhdh-jira-api` and `/rhdh-jira-update`

## Completion

Complete when:
- HTML file(s) are generated at the documented path
- Dashboard opens in browser automatically
- All features, epics, and child issues are present
- Bugs are grouped by status
- Backport section shows bugs per version (when --backport is provided)
- Team overview shows all teams with stats (when --team-overview is used)
- Completion percentages are accurate
- GitHub PR links are working and show current status
