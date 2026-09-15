# Workflow: Download translations and deploy to repos

Download completed translations from TMS, validate them, deploy into each
repo's TypeScript locale files, and prepare branches for PR creation.

<prerequisites>

All four repos (rhdh-plugins, rhdh, community-plugins, backstage) must be
cloned as siblings. The translations-cli must be built. TMS credentials must
be available (environment variables or `~/.i18n.auth.json`). The `gh` CLI
must be authenticated for PR creation.

Run the preflight check first:

```bash
uv run scripts/translation_deploy.py --json preflight
```

Follow `next_steps` to resolve any failures before continuing.

</prerequisites>

<process>

## Step 1: Confirm readiness

Ask the user:
- The TMS project ID (or confirm it is in `.i18n.config.json`)
- Which languages to download (default: de, es, fr, it, ja)
- The sprint identifier (for branch naming)

## Step 2: Download from TMS

```bash
uv run scripts/translation_deploy.py --json download \
  --project-id {{PROJECT_ID}} \
  --output-dir i18n/downloads
```

Check the output — confirm files were downloaded for the expected repos and
languages. If any repo has zero files, warn the user.

## Step 3: Validate downloads

```bash
uv run scripts/translation_deploy.py --json validate \
  --source-dir i18n/downloads
```

Check for:
- Invalid JSON structure
- Empty plugins or missing keys
- Placeholder preservation ({{...}} patterns)

If validation fails, stop and report issues to the user.

## Step 4: Deploy translations

```bash
uv run scripts/translation_deploy.py --json deploy \
  --source-dir i18n/downloads
```

This runs `translations-cli i18n deploy` in each repo, which:
- Parses downloaded JSON files
- Generates TypeScript locale files (e.g., `it.ts`, `ja.ts`)
- Validates keys against reference files (`ref.ts`)
- Reports any missing or excess keys

Present the deploy summary per repo.

## Step 5: Prepare PRs

```bash
uv run scripts/translation_deploy.py --json pr \
  --sprint {{SPRINT}}
```

For each repo with changes, use `/mutation-gate` to:

1. Create a branch: `translation/s{{SPRINT}}`
2. Stage changed files: `git add -A`
3. Commit with message:
   ```
   chore(i18n): update translations for sprint {{SPRINT}}

   Signed-off-by: {{USER_NAME}} <{{USER_EMAIL}}>
   ```
4. Push the branch
5. Create a PR via `gh pr create`

For repos that use changesets (rhdh-plugins, community-plugins), add a
changeset file before committing.

</process>

<completion>

Report:
1. Per-repo deploy summary (files deployed, key counts)
2. Validation results (missing/excess keys per repo)
3. PR URLs for each repo (or note if no changes)
4. Any warnings about placeholder or key coverage issues

</completion>
