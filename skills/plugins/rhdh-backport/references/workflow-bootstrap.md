# Version Packages Workflow Bootstrap (#4173)

One-time setup required before the first changeset backport to a **per-plugin**
`release-x.y/{plugin}` branch (< 2.1) in `redhat-developer/rhdh-plugins`.

**Does not apply to 2.1+ unified release branches** (`release-2.1`, etc.). Those
use a single branch per release; the Version Packages workflow there is maintained
by the release team.

## Why this exists

GitHub Actions runs `release_workspace_version.yml` from the **target branch** of the
merged PR, not from `main`. Until
[#4173](https://github.com/redhat-developer/rhdh-plugins/pull/4173) landed (commit
`ef07585`), the workflow only triggered on `workspace/**` merges.

Release branches cut from older tags therefore cannot open Version Packages PRs when
you merge a backport directly into `release-x.y/{plugin}` — even though `main` already
has the fix.

Learned from the lightspeed `release-1.10` backport (PRs
[#4811](https://github.com/redhat-developer/rhdh-plugins/pull/4811) /
[#4817](https://github.com/redhat-developer/rhdh-plugins/pull/4817)).

## When to bootstrap

| Scenario | Bootstrap needed? |
|----------|-------------------|
| First backport to `release-x.y/{plugin}` (< 2.1) with a changeset | Yes, once per release branch |
| Subsequent backports to same `release-x.y/{plugin}` | No — already bootstrapped |
| Backport to unified `release-2.1+` branch | No — release-team workflow |
| Yarn.lock-only CVE fix (no npm release) | No — Version Packages is skipped |
| Legacy `workspace/{plugin}` flow | No — old trigger still works |

## Required workflow markers

On the target release branch, `.github/workflows/release_workspace_version.yml` must include:

```yaml
branches: ['workspace/**', 'release-*/*']
```

Plus logic for:

- `version_branch_id` output when the base ref matches `release-*/*`
- `versionBranch: maintenance-changesets-release/${{ version_branch_id }}`
- Stale-branch check against `maintenance-changesets-release/release-x.y/{plugin}` (full base ref, not plugin name alone)

Version Packages PR branch naming for release branches:

```
maintenance-changesets-release/release-1.10/lightspeed
```

Legacy `workspace/{plugin}` branches keep:

```
maintenance-changesets-release/lightspeed
```

## Automatic bootstrap (script)

`backport.py` checks after plugin detection in `--mode auto` and `--mode create`. If
markers are missing, it:

1. Checks out `release-x.y/{plugin}`
2. Cherry-picks `ef07585`, or syncs the workflow file from `upstream/main` on conflict
3. Pushes once to `upstream`

## Manual bootstrap

Run **once per release branch**, not per backport:

```bash
git fetch upstream
git checkout release-x.y/<plugin>
git cherry-pick ef07585
# or, if cherry-pick conflicts:
git checkout upstream/main -- .github/workflows/release_workspace_version.yml CONTRIBUTING.md
git commit -m "chore: sync Version Packages workflow for release-x.y branches (#4173)"
git push upstream release-x.y/<plugin>
```

## Verify

```bash
git fetch upstream
git show upstream/release-x.y/<plugin>:.github/workflows/release_workspace_version.yml | grep -E "release-\*/\*|version_branch_id"
```

If both patterns appear, the branch is ready for changeset backports.

## Troubleshooting

**Version Packages PR never appears after backport merge:**

1. Confirm the backport PR targeted `release-x.y/{plugin}`, not `workspace/{plugin}`
2. Check workflow file on that release branch (not `main`)
3. Bootstrap if markers are missing, then re-merge or re-trigger the changesets workflow
4. Check Actions tab for `Prior Version Release Workspace` failures

**Stale Version Packages changesets branch:**

| Release model | Branch cleaned before VP |
|---------------|--------------------------|
| Pre-2.1 per-plugin | `maintenance-changesets-release/release-x.y/{plugin}` |
| 2.1+ unified | `changesets-release/{plugin}/release-x.y` (per workspace) |
| `main` | `changesets-release/{plugin}/main` |

Unified release branches follow the same per-workspace pattern as `main`, not a
single branch-wide `maintenance-changesets-release/release-2.1` path.
