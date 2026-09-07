# Generate release dashboard workflow

## Input parsing

Accept release version in any of these formats:
- `2.1` → constructs label `rhdh-2.1-candidate`
- `rhdh-2.1` → constructs label `rhdh-2.1-candidate`
- `rhdh-2.1-candidate` → uses as-is

Normalize to the canonical label format: `rhdh-X.Y-candidate`

## Authentication options

The dashboard supports two methods for JIRA access (tries acli first, falls back to REST API):

**Option 1: acli (Preferred)**
- Consistent with other RHDH skills
- Requires acli installation and authentication
- No environment variables needed

**Option 2: JIRA REST API**
- No acli installation required
- Requires `JIRA_API_TOKEN` environment variable
- Uses Python stdlib (urllib) - no extra dependencies

Get a JIRA API token:
1. Go to https://issues.redhat.com
2. Profile → Settings → Security → API tokens
3. Create token → Copy it
4. Export: `export JIRA_API_TOKEN=your_token_here`

## Data gathering

1. **Query features**: Use `acli` OR REST API to search for features with the release label:
   ```
   acli issue list --jql "labels = rhdh-X.Y-candidate AND type = Feature"
   ```
   
   Or via REST API:
   ```
   GET https://issues.redhat.com/rest/api/2/search?jql=labels="rhdh-X.Y-candidate"
   Authorization: Bearer {JIRA_API_TOKEN}
   ```
   
   Optional team filter:
   ```
   acli issue list --jql "labels = rhdh-X.Y-candidate AND \"Team[Team]\" ~ \"RHDH Frontend Plugins & UI\""
   ```

2. **Fetch hierarchy**: For each feature, recursively fetch:
   - Child epics via issue links
   - For each epic, fetch all child issues (Story, Task, Bug, Spike, Sub-task)
   - Collect status, assignee, and custom fields

3. **GitHub PR data**: For each issue with a GitHub link in Web Links or Issue Links:
   - Extract PR URL
   - Use `gh pr view {number} --json state,reviews,statusCheckRollup,mergedAt`
   - Determine status: merged, approved, failing CI, draft, etc.

## Completion calculation

For each epic and feature:
- Count total child items
- Count items with status in Done resolution category
- Calculate percentage: `(done_count / total_count) * 100`

For issues without children, completion is binary: 100% if Done, 0% otherwise.

## HTML generation

Generate standalone HTML with:
- Embedded CSS (no external stylesheets)
- Embedded JavaScript for expand/collapse
- Responsive layout with progress bars
- Color-coded status badges
- Clickable issue keys linking to JIRA
- PR badges linking to GitHub with status icons

Use template literals in Python (no Jinja2 dependency per ADR-0002).

## Output

Write HTML to: `./output/rhdh-{version}-dashboard.html`

Create `./output/` directory if it doesn't exist.

Open the file in the default browser using:
- macOS: `open {file}`
- Linux: `xdg-open {file}`
- Windows: `start {file}`

## Demo mode

For testing or when JIRA access is unavailable:

```bash
uv run scripts/dashboard.py 2.1 --demo
```

Generates a dashboard with realistic sample data including:
- 3 features with varying completion rates
- 6 epics across different statuses
- 15+ issues with mixed states
- Sample PR links with different statuses (merged, open, draft, passing)

Useful for UI testing, demonstrations, or when acli is not configured.

## Error handling

- Missing JIRA access: Show both acli and REST API options with setup instructions
- JIRA API token not set: Instructions to get token from issues.redhat.com
- No features found: Report the label used and suggest verification
- GitHub CLI unavailable: Generate dashboard without PR status
- JIRA auth failure: Report authentication issue and suggest re-auth

## Completion criteria

Report complete when:
- HTML file exists at the documented path
- Browser opens automatically
- Terminal shows: feature count, epic count, issue count, PR count
- Any errors or warnings are clearly stated
