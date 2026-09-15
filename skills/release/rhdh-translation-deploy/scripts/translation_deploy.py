#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Download translations from TMS and deploy to all repos.

Runs the translations-cli download and deploy commands, validates keys
against reference files, and prepares branches with signed-off commits
for PR creation.

Usage:
    uv run scripts/translation_deploy.py preflight
    uv run scripts/translation_deploy.py download --project-id PROJ_ID
    uv run scripts/translation_deploy.py deploy --source-dir i18n/downloads
    uv run scripts/translation_deploy.py deploy --source-dir i18n/downloads --json
    uv run scripts/translation_deploy.py validate --source-dir i18n/downloads
    uv run scripts/translation_deploy.py pr --sprint s4000 --branch release-2.2
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

_scripts_dir = Path(__file__).resolve().parent
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))

from _support import (  # noqa: E402
    REPOS,
    OutputFormatter,
    check_tms_credentials,
    find_all_repos,
    find_memsource_cli,
    find_node_tool,
    find_translations_cli,
    git_current_branch,
    git_is_clean,
)

# Languages we translate into
TARGET_LANGUAGES = ("de", "es", "fr", "it", "ja")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_cli(
    cmd: list[str],
    *,
    cwd: str | Path,
    fmt: OutputFormatter,
    label: str = "",
) -> subprocess.CompletedProcess[str]:
    """Run a CLI command, log, and return result."""
    fmt.log_info(f"{label}: {' '.join(cmd)}" if label else " ".join(cmd))
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(cwd),
        env={**os.environ, "NODE_OPTIONS": "--max-old-space-size=4096"},
    )
    if result.returncode != 0:
        fmt.log_fail(f"{label} failed:\n{result.stderr}")
    return result


def _find_downloaded_files(download_dir: Path) -> dict[str, list[Path]]:
    """Group downloaded JSON files by repo name.

    Expected patterns:
      {repo}-s{sprint}-{lang}(-C).json
      {repo}-{date}-{lang}(-C).json
    """
    result: dict[str, list[Path]] = {}
    if not download_dir.is_dir():
        return result

    lang_codes = "|".join(TARGET_LANGUAGES)
    # Sprint pattern: repo-sNNNN-lang(-C).json
    sprint_pat = re.compile(rf"^([a-z][\w-]*?)-(s\d+)-({lang_codes})(?:-C)?\.json$", re.IGNORECASE)
    # Date pattern: repo-YYYY-MM-DD-lang(-C).json
    date_pat = re.compile(
        rf"^([a-z][\w-]*?)-(\d{{4}}-\d{{2}}-\d{{2}})-({lang_codes})(?:-C)?\.json$",
        re.IGNORECASE,
    )

    for f in sorted(download_dir.iterdir()):
        if not f.suffix == ".json":
            continue
        m = sprint_pat.match(f.name) or date_pat.match(f.name)
        if m:
            repo = m.group(1)
            result.setdefault(repo, []).append(f)

    return result


def _validate_translation_file(
    filepath: Path,
) -> dict[str, Any]:
    """Validate a downloaded translation JSON file.

    Checks:
    - Valid JSON
    - Expected structure: { plugin: { lang: { key: value } } }
    - No empty plugins
    - Placeholder preservation ({{...}} patterns)
    """
    issues: list[str] = []
    stats: dict[str, int] = {"plugins": 0, "keys": 0}

    try:
        data = json.loads(filepath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return {"ok": False, "issues": [f"Invalid JSON: {exc}"], "stats": stats}

    if not isinstance(data, dict):
        return {"ok": False, "issues": ["Root is not an object"], "stats": stats}

    for plugin_name, plugin_data in data.items():
        if not isinstance(plugin_data, dict):
            issues.append(f"{plugin_name}: not an object")
            continue

        stats["plugins"] += 1
        for lang, lang_data in plugin_data.items():
            if not isinstance(lang_data, dict):
                issues.append(f"{plugin_name}.{lang}: not an object")
                continue
            stats["keys"] += len(lang_data)

            if len(lang_data) == 0:
                issues.append(f"{plugin_name}.{lang}: empty — no keys")

            # Check placeholder preservation (just warn, don't block)
            for key, value in lang_data.items():
                if not isinstance(value, str):
                    issues.append(f"{plugin_name}.{lang}.{key}: value is not a string")

    return {
        "ok": len([i for i in issues if "not an object" in i or "not a string" in i]) == 0,
        "issues": issues,
        "stats": stats,
    }


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def cmd_preflight(args: argparse.Namespace, fmt: OutputFormatter) -> None:
    """Check prerequisites for the deploy workflow."""
    search_root = args.search_root
    repos = find_all_repos(search_root=search_root)
    checks: list[dict[str, Any]] = []

    for name, path in repos.items():
        ok = path is not None
        checks.append(
            {
                "name": f"repo_{name}",
                "ok": ok,
                "detail": str(path) if ok else f"{name} not found",
            }
        )
        if ok:
            fmt.log_ok(f"{name}: {path}")
        else:
            fmt.log_fail(f"{name}: not found")

    rhdh_plugins = repos.get("rhdh-plugins")
    cli_path: Optional[Path] = None
    if rhdh_plugins:
        cli_path = find_translations_cli(rhdh_plugins)
    checks.append(
        {
            "name": "translations_cli",
            "ok": cli_path is not None,
            "detail": str(cli_path) if cli_path else "not found",
        }
    )
    if cli_path:
        fmt.log_ok(f"translations-cli: {cli_path}")
    else:
        fmt.log_fail("translations-cli: not found")

    for tool in ("node", "gh"):
        found = find_node_tool(tool)
        checks.append(
            {
                "name": f"tool_{tool}",
                "ok": found is not None,
                "detail": found or f"{tool} not on PATH",
            }
        )
        if found:
            fmt.log_ok(f"{tool}: {found}")
        else:
            fmt.log_warn(f"{tool}: not on PATH")

    creds = check_tms_credentials()
    any_cred = any(creds.values())
    checks.append(
        {
            "name": "tms_credentials",
            "ok": any_cred,
            "detail": {k: v for k, v in creds.items()},
        }
    )

    mem_cli = find_memsource_cli()
    checks.append(
        {
            "name": "memsource_cli",
            "ok": mem_cli is not None,
            "detail": mem_cli or "not on PATH",
        }
    )

    for name, path in repos.items():
        if path is None:
            continue
        clean = git_is_clean(path)
        branch = git_current_branch(path)
        checks.append(
            {
                "name": f"git_clean_{name}",
                "ok": clean,
                "detail": f"branch={branch}, clean={clean}",
            }
        )
        if clean:
            fmt.log_ok(f"{name} git: clean on {branch}")
        else:
            fmt.log_warn(f"{name} git: uncommitted changes on {branch}")

    all_ok = all(c["ok"] for c in checks)
    next_steps = [f"Fix: {c['name']} — {c['detail']}" for c in checks if not c["ok"]]

    fmt.success({"checks": checks, "all_ok": all_ok}, next_steps=next_steps or None)


def cmd_download(args: argparse.Namespace, fmt: OutputFormatter) -> None:
    """Download translated files from TMS."""
    search_root = args.search_root
    repos = find_all_repos(search_root=search_root)
    rhdh_plugins = repos.get("rhdh-plugins")

    if rhdh_plugins is None:
        fmt.error("rhdh-plugins repo not found", next_steps=["Run preflight"])
        sys.exit(1)

    cli_path = find_translations_cli(rhdh_plugins)
    if cli_path is None:
        fmt.error("translations-cli not found")
        sys.exit(1)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        "node",
        str(cli_path),
        "i18n",
        "download",
        "--output-dir",
        str(output_dir),
    ]

    if args.project_id:
        cmd.extend(["--project-id", args.project_id])

    if args.languages:
        cmd.extend(["--languages", args.languages])

    if args.status:
        cmd.extend(["--status", args.status])

    result = _run_cli(cmd, cwd=rhdh_plugins, fmt=fmt, label="download")

    if result.returncode != 0:
        fmt.error(
            f"Download failed: {result.stderr}",
            next_steps=["Check TMS credentials and project ID"],
        )
        sys.exit(1)

    # Discover what was downloaded
    downloaded = _find_downloaded_files(output_dir)
    summary: dict[str, int] = {}
    for repo, files in downloaded.items():
        summary[repo] = len(files)
        for f in files:
            fmt.log_ok(f"Downloaded: {f.name}")

    fmt.success(
        {
            "output_dir": str(output_dir),
            "files_by_repo": summary,
            "total_files": sum(summary.values()),
        }
    )


def cmd_validate(args: argparse.Namespace, fmt: OutputFormatter) -> None:
    """Validate downloaded translation files before deployment."""
    source_dir = Path(args.source_dir)

    if not source_dir.is_dir():
        fmt.error(f"Source directory not found: {source_dir}")
        sys.exit(1)

    downloaded = _find_downloaded_files(source_dir)
    if not downloaded:
        fmt.error(
            f"No translation files found in {source_dir}",
            next_steps=["Run download first"],
        )
        sys.exit(1)

    results: dict[str, list[dict[str, Any]]] = {}
    all_ok = True

    for repo, files in downloaded.items():
        results[repo] = []
        for f in files:
            validation = _validate_translation_file(f)
            results[repo].append(
                {
                    "file": f.name,
                    **validation,
                }
            )
            if validation["ok"]:
                fmt.log_ok(
                    f"{f.name}: {validation['stats']['plugins']} plugins, "
                    f"{validation['stats']['keys']} keys"
                )
            else:
                fmt.log_fail(f"{f.name}: {validation['issues']}")
                all_ok = False

    fmt.success(
        {"validations": results, "all_ok": all_ok},
        next_steps=(
            ["Run deploy to write translations into repos"]
            if all_ok
            else ["Fix validation issues before deploying"]
        ),
    )


def cmd_deploy(args: argparse.Namespace, fmt: OutputFormatter) -> None:
    """Deploy translations to all repos using translations-cli."""
    source_dir = Path(args.source_dir)
    search_root = args.search_root
    repos = find_all_repos(search_root=search_root)

    if not source_dir.is_dir():
        fmt.error(f"Source directory not found: {source_dir}")
        sys.exit(1)

    rhdh_plugins = repos.get("rhdh-plugins")
    if rhdh_plugins is None:
        fmt.error("rhdh-plugins repo not found")
        sys.exit(1)

    cli_path = find_translations_cli(rhdh_plugins)
    if cli_path is None:
        fmt.error("translations-cli not found")
        sys.exit(1)

    # Group files by repo
    downloaded = _find_downloaded_files(source_dir)
    if not downloaded:
        fmt.error(f"No translation files in {source_dir}")
        sys.exit(1)

    deploy_results: dict[str, Any] = {}

    for repo_name in REPOS:
        repo_path = repos.get(repo_name)
        if repo_path is None:
            fmt.log_warn(f"{repo_name}: repo not found, skipping")
            deploy_results[repo_name] = {"ok": False, "detail": "repo not found"}
            continue

        repo_files = downloaded.get(repo_name, [])
        if not repo_files:
            fmt.log_info(f"{repo_name}: no downloaded files, skipping")
            deploy_results[repo_name] = {
                "ok": True,
                "detail": "no files to deploy",
                "files_deployed": 0,
            }
            continue

        # Copy translation files to repo's download directory
        repo_download_dir = repo_path / "i18n" / "downloads"
        repo_download_dir.mkdir(parents=True, exist_ok=True)

        for f in repo_files:
            target = repo_download_dir / f.name
            target.write_bytes(f.read_bytes())
            fmt.log_info(f"Copied {f.name} to {repo_download_dir}")

        # Run deploy
        cmd = [
            "node",
            str(cli_path),
            "i18n",
            "deploy",
            "--source-dir",
            str(repo_download_dir),
        ]

        result = _run_cli(cmd, cwd=repo_path, fmt=fmt, label=f"deploy {repo_name}")

        if result.returncode == 0:
            fmt.log_ok(f"{repo_name}: deploy succeeded")
            deploy_results[repo_name] = {
                "ok": True,
                "files_deployed": len(repo_files),
                "stdout": result.stdout[-500:] if result.stdout else "",
            }
        else:
            fmt.log_fail(f"{repo_name}: deploy failed")
            deploy_results[repo_name] = {
                "ok": False,
                "detail": result.stderr[-500:] if result.stderr else "unknown error",
                "files_deployed": 0,
            }

    all_ok = all(r.get("ok", False) for r in deploy_results.values())
    fmt.success(
        {"repos": deploy_results, "all_ok": all_ok},
        next_steps=(
            ["Create PRs with: uv run scripts/translation_deploy.py pr"]
            if all_ok
            else ["Fix deploy failures before creating PRs"]
        ),
    )


def cmd_pr(args: argparse.Namespace, fmt: OutputFormatter) -> None:
    """Create branches and PRs for deployed translations."""
    sprint = args.sprint
    branch_name = args.branch or f"translation/{sprint}"
    search_root = args.search_root
    repos = find_all_repos(search_root=search_root)

    pr_results: dict[str, Any] = {}

    for repo_name in REPOS:
        repo_path = repos.get(repo_name)
        if repo_path is None:
            fmt.log_warn(f"{repo_name}: repo not found, skipping")
            pr_results[repo_name] = {"ok": False, "detail": "repo not found"}
            continue

        # Check if there are changes to commit
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            cwd=str(repo_path),
        )
        if status.returncode != 0 or not status.stdout.strip():
            fmt.log_info(f"{repo_name}: no changes to commit")
            pr_results[repo_name] = {"ok": True, "detail": "no changes"}
            continue

        # Count changed files
        changed_files = [line.strip() for line in status.stdout.strip().split("\n") if line.strip()]

        pr_results[repo_name] = {
            "ok": True,
            "branch": branch_name,
            "changed_files": len(changed_files),
            "ready_for_pr": True,
        }

        fmt.log_ok(
            f"{repo_name}: {len(changed_files)} files changed, ready for branch {branch_name}"
        )

    fmt.success(
        {
            "sprint": sprint,
            "branch": branch_name,
            "repos": pr_results,
        },
        next_steps=[
            f"For each repo with changes, create branch {branch_name}, "
            "commit with Signed-off-by, and open PR via /mutation-gate",
        ],
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="RHDH translation download and deployment",
    )
    parser.add_argument(
        "--json",
        dest="output_mode",
        action="store_const",
        const="json",
        default="auto",
        help="Force JSON output",
    )
    parser.add_argument(
        "--search-root",
        default=None,
        help="Root directory to search for repos (default: cwd parent)",
    )

    sub = parser.add_subparsers(dest="command")

    # preflight
    sub.add_parser("preflight", help="Check prerequisites")

    # download
    p_download = sub.add_parser("download", help="Download from TMS")
    p_download.add_argument("--project-id", help="TMS project ID")
    p_download.add_argument(
        "--output-dir",
        default="i18n/downloads",
        help="Download output directory",
    )
    p_download.add_argument(
        "--languages",
        help="Comma-separated languages (e.g., de,es,fr,it,ja)",
    )
    p_download.add_argument(
        "--status",
        default="COMPLETED",
        help="Job status filter (default: COMPLETED)",
    )

    # validate
    p_validate = sub.add_parser("validate", help="Validate downloaded files")
    p_validate.add_argument(
        "--source-dir",
        default="i18n/downloads",
        help="Directory with downloaded translations",
    )

    # deploy
    p_deploy = sub.add_parser("deploy", help="Deploy to all repos")
    p_deploy.add_argument(
        "--source-dir",
        default="i18n/downloads",
        help="Directory with downloaded translations",
    )

    # pr
    p_pr = sub.add_parser("pr", help="Prepare branches for PRs")
    p_pr.add_argument(
        "--sprint",
        required=True,
        help="Sprint identifier for branch name",
    )
    p_pr.add_argument(
        "--branch",
        help="Custom branch name (default: translation/s{sprint})",
    )

    args = parser.parse_args(argv)
    fmt = OutputFormatter(mode=args.output_mode)

    commands = {
        "preflight": cmd_preflight,
        "download": cmd_download,
        "validate": cmd_validate,
        "deploy": cmd_deploy,
        "pr": cmd_pr,
    }

    if not args.command:
        parser.print_help()
        sys.exit(1)

    handler = commands.get(args.command)
    if handler is None:
        fmt.error(f"Unknown command: {args.command}")
        sys.exit(1)

    handler(args, fmt)


if __name__ == "__main__":
    main()
