import contextlib
from copy import deepcopy
import io
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

import auto_release
import dispatch_release_ci as dispatch


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.root = "repos/codyps/fastrace-diesel"
        self.pr = {
            "number": 3, "state": "open", "draft": False,
            "user": {"login": "github-actions[bot]"},
            "head": {"ref": "release-plz-next", "sha": "head-sha",
                     "repo": {"full_name": "codyps/fastrace-diesel"}},
            "base": {"ref": "main", "sha": "base-sha"},
        }
        self.current = deepcopy(self.pr)

    def run_main(self, runs=None, stale=False, execute=True, candidates=None):
        mutations = []
        pr_reads = 0

        def api(endpoint, payload=None, method="PUT"):
            nonlocal pr_reads
            if payload is not None:
                mutations.append((endpoint, payload, method))
                return None  # GitHub workflow dispatch returns HTTP 204.
            if endpoint == self.root:
                return {"default_branch": "main"}
            if endpoint == f"{self.root}/pulls/3":
                pr_reads += 1
                return self.pr if pr_reads == 1 else self.current
            if endpoint == f"{self.root}/git/commits/base-sha":
                return {"tree": {"sha": "base-tree"}}
            if endpoint == f"{self.root}/git/commits/head-sha":
                return {"tree": {"sha": "base-tree" if stale else "head-tree"}}
            self.fail(f"Unexpected API endpoint: {endpoint}")

        def pages(endpoint, key=None):
            if endpoint == f"{self.root}/pulls?state=open&base=main":
                return [self.pr] if candidates is None else candidates
            self.assertEqual(endpoint,
                             f"{self.root}/actions/workflows/ci.yml/runs?event=workflow_dispatch&head_sha=head-sha")
            self.assertEqual(key, "workflow_runs")
            return [] if runs is None else runs

        with (patch.object(dispatch, "api", side_effect=api),
              patch.object(auto_release, "api", side_effect=api),
              patch.object(dispatch, "pages", side_effect=pages),
              patch.dict("os.environ", {"GITHUB_REPOSITORY": "codyps/fastrace-diesel"}),
              patch("sys.argv", ["dispatch_release_ci.py"] + (["--dispatch"] if execute else [])),
              contextlib.redirect_stdout(io.StringIO())):
            dispatch.main()
        return mutations

    def test_new_head_dispatches_once(self):
        self.assertEqual(self.run_main(), [(f"{self.root}/actions/workflows/ci.yml/dispatches",
                                          {"ref": "release-plz-next"}, "POST")])

    def test_existing_dispatch_is_never_repeated(self):
        for status, conclusion in [("queued", None), ("in_progress", None),
                                   ("completed", "success"), ("completed", "failure"),
                                   ("completed", "cancelled"), ("completed", "timed_out")]:
            with self.subTest(status=status, conclusion=conclusion):
                self.assertEqual(self.run_main(runs=[{"status": status, "conclusion": conclusion}]), [])

    def test_no_candidate_stale_tree_and_preview_do_not_dispatch(self):
        for options in [{"candidates": []}, {"stale": True}, {"execute": False}]:
            with self.subTest(options=options):
                self.assertEqual(self.run_main(**options), [])

    def test_non_release_prs_are_ignored(self):
        for changes in [{"draft": True}, {"state": "closed"},
                        {"user": {"login": "someone"}},
                        {"head": {"ref": "release-plz-next", "repo": {"full_name": "someone/fork"}}},
                        {"head": {"ref": "release-plz-next", "repo": None}},
                        {"base": {"ref": "other"}}]:
            with self.subTest(changes=changes), patch.dict(self.pr, changes):
                self.assertEqual(self.run_main(), [])

    def test_pr_change_during_check_prevents_dispatch(self):
        for changes in [{"state": "closed"}, {"draft": True},
                        {"user": {"login": "someone"}},
                        {"head": {**self.pr["head"], "sha": "new-head"}},
                        {"head": {**self.pr["head"], "ref": "release-plz-other"}},
                        {"base": {**self.pr["base"], "sha": "new-base"}}]:
            with self.subTest(changes=changes), patch.dict(self.current, changes):
                self.assertEqual(self.run_main(), [])

    def test_api_supports_empty_dispatch_response(self):
        with patch.object(auto_release.subprocess, "run",
                          return_value=subprocess.CompletedProcess([], 0, stdout="")) as run:
            self.assertIsNone(auto_release.api("endpoint", {"ref": "branch"}, method="POST"))
            self.assertEqual(run.call_args.args[0],
                             ["gh", "api", "endpoint", "--method", "POST", "--input", "-"])

    def test_api_errors_propagate(self):
        with patch.object(auto_release.subprocess, "run",
                          side_effect=subprocess.CalledProcessError(1, "gh")):
            with self.assertRaises(subprocess.CalledProcessError):
                auto_release.api("endpoint", {"ref": "branch"}, method="POST")

    def test_workflows_wire_merge_output_and_dispatch_guard(self):
        workflows = Path(__file__).resolve().parents[1] / "workflows"
        automatic = (workflows / "auto-release.yml").read_text()
        self.assertIn("id: merge", automatic)
        self.assertIn("if: steps.merge.outputs.merged == 'true'", automatic)
        self.assertEqual(automatic.count("gh workflow run release-plz.yml"), 1)
        release = (workflows / "release-plz.yml").read_text()
        self.assertIn("python3 .github/scripts/dispatch_release_ci.py --dispatch", release)
        self.assertNotIn("gh workflow run ci.yml", release)


if __name__ == "__main__":
    unittest.main()
