---
name: rhdh-translation-deploy
description: >-
  Downloads completed translations from TMS (Memsource), validates them,
  deploys translated strings into TypeScript locale files across rhdh-plugins,
  rhdh, community-plugins, and backstage, and prepares branches for PR creation
  with signed-off commits. Use for "deploy RHDH translations", "download
  translations from TMS", or "create translation PRs for 2.2".
compatibility: "Node 22+, translations-cli built in rhdh-plugins/workspaces/translations, memsource CLI, gh CLI for PRs, all four repos cloned as siblings."
---

# RHDH Translation Deploy

Download translated files from TMS, deploy them into the target repos as
TypeScript locale files, validate key coverage, and create PRs.

## Route

Load `workflows/download-and-deploy.md`. It walks through download, validation,
deployment, and PR creation in sequence.

## Boundary

- Extracting strings and uploading to TMS is `/rhdh-translation-upload`.
- Repository locations and branch context are `/rhdh-context`.
- External writes (creating PRs, pushing branches) go through `/mutation-gate`.

## Completion

Report per-repo: files deployed, key counts, validation results (missing or
excess keys), and PR URLs when created.
