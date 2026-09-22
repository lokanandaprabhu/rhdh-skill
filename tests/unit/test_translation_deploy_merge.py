"""Unit tests for rhdh-translation-deploy locale merge helpers."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "skills"
    / "release"
    / "rhdh-translation-deploy"
    / "scripts"
    / "translation_deploy.py"
)


@pytest.fixture(scope="module")
def deploy_mod():
    spec = importlib.util.spec_from_file_location("translation_deploy", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    scripts_dir = str(SCRIPT.parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec.loader.exec_module(mod)
    return mod


def test_format_ts_string_apostrophe_uses_double_quotes(deploy_mod):
    # Double-quoted literal containing a raw apostrophe
    assert deploy_mod._format_ts_string("l'agent") == '"l\'agent"'


def test_format_ts_string_plain_single_quotes(deploy_mod):
    assert deploy_mod._format_ts_string("Hello") == "'Hello'"


def test_merge_updates_existing_skips_missing(deploy_mod, tmp_path: Path):
    ts = tmp_path / "de.ts"
    ts.write_text(
        "export default {\n"
        "  messages: {\n"
        "    'common.loading': 'Wird geladen',\n"
        "    'common.retry': 'Erneut',\n"
        "  },\n"
        "};\n",
        encoding="utf-8",
    )
    counts = deploy_mod._merge_locale_file(
        ts,
        {
            "common.loading": "Ladevorgang",
            "common.brandNew": "Neu",
        },
    )
    text = ts.read_text(encoding="utf-8")
    assert counts["updated"] == 1
    assert counts["skipped"] == 1
    assert "'common.loading': 'Ladevorgang'" in text
    assert "'common.retry': 'Erneut'" in text
    assert "common.brandNew" not in text


def test_merge_never_removes_keys(deploy_mod, tmp_path: Path):
    ts = tmp_path / "es.ts"
    ts.write_text(
        "const m = {\n  messages: {\n    'a.keep': 'keep',\n    'a.update': 'old',\n  },\n};\n",
        encoding="utf-8",
    )
    deploy_mod._merge_locale_file(ts, {"a.update": "new"})
    text = ts.read_text(encoding="utf-8")
    assert "'a.keep': 'keep'" in text
    assert "'a.update': 'new'" in text


def test_incoming_keys_from_download_flattens_en(deploy_mod):
    data = {
        "plugin.foo": {
            "en": {"page.title": "Título"},
        }
    }
    assert deploy_mod._incoming_keys_from_download(data) == {"plugin.foo": {"page.title": "Título"}}
