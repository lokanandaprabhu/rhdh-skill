"""Unit tests for the rhdh-release-dashboard script."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DASHBOARD_SCRIPTS = PROJECT_ROOT / "skills" / "release" / "rhdh-release-dashboard" / "scripts"
if str(_DASHBOARD_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD_SCRIPTS))

import dashboard  # noqa: E402


class TestNormalizeLabel:
    """Test label normalization for different input formats."""

    def test_full_label_unchanged(self):
        """Full label format should pass through unchanged."""
        assert dashboard.normalize_label("rhdh-2.1-candidate") == "rhdh-2.1-candidate"

    def test_version_only(self):
        """Version number alone should be expanded."""
        assert dashboard.normalize_label("2.1") == "rhdh-2.1-candidate"

    def test_with_rhdh_prefix(self):
        """rhdh- prefix without -candidate should add -candidate."""
        assert dashboard.normalize_label("rhdh-2.1") == "rhdh-2.1-candidate"

    def test_version_with_patch(self):
        """Patch versions should be truncated to X.Y."""
        assert dashboard.normalize_label("2.1.0") == "rhdh-2.1-candidate"

    def test_invalid_format_raises(self):
        """Invalid version format should raise ValueError."""
        try:
            dashboard.normalize_label("invalid")
            assert False, "Expected ValueError"
        except ValueError as e:
            assert "Invalid version format" in str(e)

    def test_empty_string_raises(self):
        """Empty string should raise ValueError."""
        try:
            dashboard.normalize_label("")
            assert False, "Expected ValueError"
        except ValueError:
            pass


class TestIsStatusDone:
    """Test completion status detection."""

    def test_done_statuses(self):
        assert dashboard.is_status_done("Done") is True
        assert dashboard.is_status_done("Closed") is True
        assert dashboard.is_status_done("Resolved") is True
        assert dashboard.is_status_done("Dev Complete") is True
        assert dashboard.is_status_done("QA Complete") is True
        assert dashboard.is_status_done("Release Pending") is True

    def test_incomplete_statuses(self):
        assert dashboard.is_status_done("In Progress") is False
        assert dashboard.is_status_done("To Do") is False
        assert dashboard.is_status_done("New") is False
        assert dashboard.is_status_done("Backlog") is False
        assert dashboard.is_status_done("Blocked") is False


class TestGetStatusClass:
    """Test status badge CSS class selection."""

    def test_done_status(self):
        assert dashboard.get_status_class("Done") == "status-done"
        assert dashboard.get_status_class("Closed") == "status-done"
        assert dashboard.get_status_class("Resolved") == "status-done"
        assert dashboard.get_status_class("Dev Complete") == "status-done"
        assert dashboard.get_status_class("QA Complete") == "status-done"
        assert dashboard.get_status_class("Release Pending") == "status-done"

    def test_progress_status(self):
        assert dashboard.get_status_class("In Progress") == "status-progress"
        assert dashboard.get_status_class("In Review") == "status-progress"

    def test_blocked_status(self):
        assert dashboard.get_status_class("Blocked") == "status-blocked"

    def test_todo_status(self):
        assert dashboard.get_status_class("To Do") == "status-todo"
        assert dashboard.get_status_class("Open") == "status-todo"
        assert dashboard.get_status_class("New") == "status-todo"

    def test_case_insensitive(self):
        """Status matching should be case-insensitive."""
        assert dashboard.get_status_class("DONE") == "status-done"
        assert dashboard.get_status_class("in progress") == "status-progress"
        assert dashboard.get_status_class("BLOCKED") == "status-blocked"


class TestGetIssueLinks:
    """Test PR link extraction and deduplication."""

    def test_extracts_single_pr(self):
        issue = {"git_pr": "https://github.com/org/repo/pull/123"}
        links = dashboard.get_issue_links(issue)
        assert links == ["https://github.com/org/repo/pull/123"]

    def test_removes_duplicate_prs(self):
        """Should deduplicate if the same PR appears twice in the field."""
        issue = {"git_pr": "https://github.com/org/repo/pull/123 https://github.com/org/repo/pull/123"}
        links = dashboard.get_issue_links(issue)
        assert links == ["https://github.com/org/repo/pull/123"]

    def test_extracts_multiple_unique_prs(self):
        issue = {"git_pr": "https://github.com/org/repo/pull/123 https://github.com/org/repo/pull/456"}
        links = dashboard.get_issue_links(issue)
        assert links == ["https://github.com/org/repo/pull/123", "https://github.com/org/repo/pull/456"]

    def test_empty_field_returns_empty_list(self):
        issue = {"git_pr": ""}
        links = dashboard.get_issue_links(issue)
        assert links == []

    def test_missing_field_returns_empty_list(self):
        issue = {}
        links = dashboard.get_issue_links(issue)
        assert links == []


class TestCheckDependencies:
    """Test dependency checking."""

    def test_returns_dict(self):
        """Should return a dict with acli and gh keys."""
        deps = dashboard.check_dependencies()
        assert isinstance(deps, dict)
        assert "acli" in deps
        assert "gh" in deps

    def test_values_are_bool(self):
        """Dependency status should be boolean."""
        deps = dashboard.check_dependencies()
        assert isinstance(deps["acli"], bool)
        assert isinstance(deps["gh"], bool)


class TestGenerateDemoData:
    """Test demo data generation."""

    def test_generates_features(self):
        """Should generate demo features."""
        data = dashboard.generate_demo_data("2.1")
        assert len(data["features"]) > 0

    def test_generates_stats(self):
        """Should generate statistics."""
        data = dashboard.generate_demo_data("2.1")
        stats = data["stats"]
        assert stats["features"] > 0
        assert stats["epics"] > 0
        assert stats["issues"] > 0
        assert stats["prs"] >= 0

    def test_features_have_hierarchy(self):
        """Features should have epics with issues."""
        data = dashboard.generate_demo_data("2.1")
        feature = data["features"][0]
        assert "epics" in feature
        assert len(feature["epics"]) > 0
        assert "issues" in feature["epics"][0]

    def test_completion_percentages(self):
        """Should include completion percentages."""
        data = dashboard.generate_demo_data("2.1")
        feature = data["features"][0]
        assert "completion" in feature
        assert 0 <= feature["completion"] <= 100


class TestGenerateHtml:
    """Test HTML generation."""

    def test_empty_features(self):
        """Should handle empty feature list."""
        data = {
            "features": [],
            "stats": {"features": 0, "epics": 0, "issues": 0, "prs": 0},
            "team_name": ""
        }
        html = dashboard.generate_html(data, "rhdh-2.1-candidate", "2.1")

        assert "<!DOCTYPE html>" in html
        assert "RHDH 2.1 Release Dashboard" in html
        assert "No features found" in html

    def test_with_features(self):
        """Should generate HTML with feature data."""
        data = {
            "features": [
                {
                    "key": "RHDHPLAN-1722",
                    "summary": "Test Feature",
                    "status": "In Progress",
                    "type": "Feature",
                    "completion": 50,
                    "epics": [],
                    "prs": []
                }
            ],
            "stats": {"features": 1, "epics": 0, "issues": 0, "prs": 0},
            "team_name": ""
        }
        html = dashboard.generate_html(data, "rhdh-2.1-candidate", "2.1")

        assert "<!DOCTYPE html>" in html
        assert "RHDHPLAN-1722" in html
        assert "Test Feature" in html
        assert "50%" in html

    def test_includes_stats(self):
        """Should include statistics in the output."""
        data = {
            "features": [],
            "stats": {"features": 5, "epics": 12, "issues": 45, "prs": 23},
            "team_name": ""
        }
        html = dashboard.generate_html(data, "rhdh-2.1-candidate", "2.1")

        assert ">5<" in html  # Feature count
        assert ">12<" in html  # Epic count
        assert ">45<" in html  # Issue count
        assert ">23<" in html  # PR count

    def test_includes_javascript(self):
        """Should include interactive JavaScript."""
        data = {
            "features": [],
            "stats": {"features": 0, "epics": 0, "issues": 0, "prs": 0},
            "team_name": ""
        }
        html = dashboard.generate_html(data, "rhdh-2.1-candidate", "2.1")

        assert "<script>" in html
        assert "toggleNode" in html

    def test_includes_css(self):
        """Should include embedded CSS."""
        data = {
            "features": [],
            "stats": {"features": 0, "epics": 0, "issues": 0, "prs": 0},
            "team_name": ""
        }
        html = dashboard.generate_html(data, "rhdh-2.1-candidate", "2.1")

        assert "<style>" in html
        assert "tree-node" in html
        assert "progress-bar" in html
