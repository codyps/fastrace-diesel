"""Dispatch CI once per release PR head; default to a read-only preview."""

import argparse
import os

from auto_release import api, pages, release_pr, same_tree


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dispatch", action="store_true", help="Dispatch missing CI runs")
    args = parser.parse_args()
    repository = os.environ.get("GITHUB_REPOSITORY", "codyps/fastrace-diesel")
    if repository != "codyps/fastrace-diesel":
        raise RuntimeError("Release CI is only configured for codyps/fastrace-diesel")
    root = f"repos/{repository}"
    branch = api(root)["default_branch"]
    for candidate in pages(f"{root}/pulls?state=open&base={branch}"):
        if not release_pr(candidate, repository, branch):
            continue
        pr = api(f"{root}/pulls/{candidate['number']}")
        if (pr["state"] != "open" or pr["draft"]
                or pr["user"]["login"] != "github-actions[bot]"
                or not release_pr(pr, repository, branch)):
            continue
        if same_tree(root, pr):
            print(f"Skipping stale release PR #{pr['number']}: its tree matches the base")
            continue
        sha = pr["head"]["sha"]
        runs = pages(f"{root}/actions/workflows/ci.yml/runs?event=workflow_dispatch&head_sha={sha}",
                     "workflow_runs")
        if runs:
            print(f"CI already dispatched for release PR #{pr['number']} at {sha}")
            continue
        current = api(f"{root}/pulls/{pr['number']}")
        if (current["state"] != "open" or current["draft"]
                or current["user"]["login"] != "github-actions[bot]"
                or not release_pr(current, repository, branch)
                or current["head"]["sha"] != sha
                or current["head"]["ref"] != pr["head"]["ref"]
                or current["base"]["sha"] != pr["base"]["sha"]):
            print(f"Release PR #{pr['number']} changed; waiting for the next check")
            continue
        if args.dispatch:
            api(f"{root}/actions/workflows/ci.yml/dispatches",
                {"ref": current["head"]["ref"]}, method="POST")
            print(f"Dispatched CI for release PR #{pr['number']} at {sha}")
        else:
            print(f"Preview: CI needed for release PR #{pr['number']} at {sha}")


if __name__ == "__main__":
    main()
