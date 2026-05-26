#!/usr/bin/env python3
# /// script
# dependencies = [
#   "pygithub",
#   "pyyaml",
# ]
# ///
import os
import sys
from triage.github_api import GitHubAPIClient
from triage.rules_engine import RulesEngine
from triage.rules import StalePRRule

def main():
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("[ERROR] GH_TOKEN or GITHUB_TOKEN is not set.")
        sys.exit(1)

    # Extract active repository name dynamically from the git config of the local clone.
    try:
        import subprocess
        script_dir = os.path.dirname(os.path.abspath(__file__))
        origin_url = subprocess.check_output(["git", "-C", script_dir, "config", "--get", "remote.origin.url"]).decode("utf-8").strip()
        clean_url = origin_url.replace(".git", "").replace(":", "/")
        parts = clean_url.split("/")
        repo_name = f"{parts[-2]}/{parts[-1]}"
        print(f"[INFO] Target Repository resolved: {repo_name}")
    except Exception as e:
        print(f"[ERROR] Failed to dynamically determine current Git repository name: {e}")
        sys.exit(1)




    # Configure thresholds (stale after 30 days, abandon candidate after 37 days)
    STALE_THRESHOLD_DAYS = 30
    ABANDON_THRESHOLD_DAYS = 37

    print(f"[START] Inactivity Scan: Scanning open PRs in '{repo_name}'...")

    try:
        # Initialize client and engine
        client = GitHubAPIClient(token, repo_name)
        engine = RulesEngine(client)

        # Register only stale/abandon inactivity rules
        engine.add_rule(StalePRRule(
            stale_threshold_days=STALE_THRESHOLD_DAYS,
            abandon_threshold_days=ABANDON_THRESHOLD_DAYS
        ))

        # Fetch all currently open pull requests
        pulls = client.repo.get_pulls(state="open")
        total_scanned = 0

        for pygithub_pr in pulls:
            if pygithub_pr.draft:
                continue
            
            total_scanned += 1
            print(f"  - [SCANNING] PR #{pygithub_pr.number}: '{pygithub_pr.title}' (Updated at: {pygithub_pr.updated_at})")
            
            try:
                # Wrap the PR inside our shared context model and run engine
                context = client.get_pr_context(pygithub_pr.number, event_name="schedule")
                engine.run(context)
            except Exception as pe:
                print(f"    [ERROR] Failed to run stale evaluation on PR #{pygithub_pr.number}: {pe}", file=sys.stderr)

        print(f"[SUCCESS] Inactivity Scan complete. Scanned {total_scanned} open non-draft pull requests.")
    except Exception as e:
        print(f"[ERROR] Inactivity scan runner failed: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
