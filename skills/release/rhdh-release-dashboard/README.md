# RHDH Release Dashboard

Generate interactive HTML dashboards showing complete release progress — features, epics, stories, bugs, backports, and GitHub PRs — all in one view.

## Prerequisites

- **Python 3.9+**
- **[uv](https://docs.astral.sh/uv/)** — Python package runner
  ```bash
  brew install uv        # macOS
  curl -LsSf https://astral.sh/uv/install.sh | sh  # Linux
  ```
- **Jira API Token** — [Create one here](https://id.atlassian.com/manage-profile/security/api-tokens)
- **GitHub CLI** (optional, for PR status) — `brew install gh && gh auth login`

## Setup

Export your Jira credentials:

```bash
export JIRA_EMAIL=your-email@redhat.com
export JIRA_API_TOKEN=your_token_here
```

## Quick Start

```bash
# By fix version
uv run scripts/dashboard.py --fix-version "2.1.0"

# By label
uv run scripts/dashboard.py --label "rhdh-2.1-candidate"

# Both (OR query — catches everything)
uv run scripts/dashboard.py --fix-version "2.1.0" --label "rhdh-2.1-candidate"

# Demo mode (no Jira access needed)
uv run scripts/dashboard.py --fix-version "2.1.0" --demo
```

## Command Options

| Flag | Required | Description |
|------|----------|-------------|
| `--fix-version` | One of these | Jira fixVersion (e.g., `"2.1.0"`) |
| `--label` | is required | Jira label (e.g., `"rhdh-2.1-candidate"`) |
| `--team` | No | Filter by team name or ID (e.g., `"RHDH Frontend Plugins & UI"`) |
| `--backport` | No | Comma-separated backport fixVersions (e.g., `"1.10.4,1.9.8"`) |
| `--team-overview` | No | Generate an additional all-teams overview dashboard |
| `--no-browser` | No | Don't open the dashboard in the browser |
| `--demo` | No | Generate with sample data (no Jira access needed) |

## Examples

```bash
# Team-specific dashboard
uv run scripts/dashboard.py \
  --fix-version "2.1.0" \
  --label "rhdh-2.1-candidate" \
  --team "RHDH Frontend Plugins & UI" \
  --no-browser

# With backport tracking
uv run scripts/dashboard.py \
  --fix-version "2.1.0" \
  --label "rhdh-2.1-candidate" \
  --team "Frontend Plugins" \
  --backport "1.10.4,1.9.8" \
  --no-browser

# Team-specific + all-teams overview (two HTML files)
uv run scripts/dashboard.py \
  --fix-version "2.1.0" \
  --label "rhdh-2.1-candidate" \
  --team "Frontend Plugins" \
  --backport "1.10.4,1.9.8" \
  --team-overview \
  --no-browser

# All teams (no --team filter)
uv run scripts/dashboard.py \
  --fix-version "2.1.0" \
  --label "rhdh-2.1-candidate" \
  --team-overview \
  --no-browser
```

## Output

Dashboards are generated in the `./output/` directory:

| File | Description |
|------|-------------|
| `rhdh-2-1-candidate-dashboard.html` | Team-specific dashboard with full hierarchy |
| `rhdh-2-1-candidate-team-overview.html` | All-teams overview (when `--team-overview` is used) |

Open in any browser — self-contained HTML, no server needed.

## Dashboard Sections

### Team Dashboard

- **Features** — Full hierarchy: Feature → Epic → Story/Task with completion %
- **Contributed Work** — When using `--team`, epics owned by your team under another team's features (found by parent relationship, not fixVersion/label on the epic)
- **Independent Epics** — Epics not under any Feature
- **Bugs** — Grouped by status (collapsible)
- **Backports** — Bugs per backport version with progress bars
- **PR Status** — GitHub PR badges inline with each issue

### Team Overview

- **All teams at a glance** — Feature count, epic/story count, bug count per team
- **Completion %** — Progress bars for features and children
- **Expandable** — Click a team to see full Feature → Epic → Story hierarchy
- **Bugs by status** — Grouped and expandable within each team
- **Backport bugs** — Grouped by version within each team

## Team Filter

The `--team` flag accepts either a **team name** or **team ID**:

```bash
# By name (exact or partial match)
--team "RHDH Frontend Plugins & UI"
--team "Frontend Plugins"

# By ID
--team "ec74d716-af36-4b3c-950f-f79213d08f71-2176"
```

The name is automatically resolved to the team ID via the Jira API.

## Queried Projects

| Project | What's queried |
|---------|---------------|
| **RHDHPLAN** | Features, Epics, Stories, Tasks, Spikes |
| **RHDHBUGS** | Bugs, Backport bugs |

## Troubleshooting

### "No JIRA access available"

You need either:
- `JIRA_EMAIL` and `JIRA_API_TOKEN` environment variables set, OR
- `acli` installed and configured, OR
- `--demo` flag for sample data

### "No features found"

- Verify the fixVersion/label exists in Jira
- Check your team name/ID is correct (if using `--team`)
- Ensure your API token has access to RHDHPLAN and RHDHBUGS projects

### GitHub PR status not showing

Install and authenticate the GitHub CLI:
```bash
brew install gh
gh auth login
```

### Dashboard takes a long time

PR status lookup adds ~3-4 minutes (one API call per PR). Use `--no-browser` and let it run. Without `gh` CLI, it skips PR lookups and finishes in ~30 seconds.
