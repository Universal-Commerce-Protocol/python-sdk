#!/usr/bin/env python3
# /// script
# dependencies = [
#   "pygithub",
#   "pyyaml",
# ]
# ///
import json
import os
import sys
import argparse

# Dynamically add parent directory to sys.path to resolve namespace package imports
script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

from triage.github_api import GitHubAPIClient
from triage.rules_engine import RulesEngine
from triage.rules import FileRoutingRule, ReviewerApprovalRule, LabelLifecycleRule

def main():
    parser = argparse.ArgumentParser(description="Real-time UCP PR Triage webhook runner.")
    parser.add_argument("--dry-run", action="store_true", help="Evaluate rules without committing updates to API.")
    args = parser.parse_args()

    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("[ERROR] GH_TOKEN or GITHUB_TOKEN is not set.")
        sys.exit(1)

    event_path = os.environ.get("GITHUB_EVENT_PATH")
    event_name = os.environ.get("GITHUB_EVENT_NAME")
    
    # Extract active repository name dynamically from the local git config
    try:
        import subprocess
        script_dir = os.path.dirname(os.path.abspath(__file__))
        origin_url = subprocess.check_output(["git", "-C", script_dir, "config", "--get", "remote.origin.url"]).decode("utf-8").strip()
        clean_url = origin_url.replace(".git", "").replace(":", "/")
        parts = clean_url.split("/")
        repo_name = f"{parts[-2]}/{parts[-1]}"
        print(f"[INFO] Target Repository resolved: {repo_name}")
    except Exception as e:
        # Fallback to CI environment variable, else fail
        repo_name = os.environ.get("GITHUB_REPOSITORY")
        if not repo_name:
            print(f"[ERROR] Failed to dynamically determine current Git repository name: {e}")
            sys.exit(1)

    if not event_path or not os.path.exists(event_path):
        print("[ERROR] GITHUB_EVENT_PATH is not set or invalid.")
        sys.exit(1)

    with open(event_path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    # Determine pull request number from payload
    pr_number = (
        payload.get("pull_request", {}).get("number") or
        payload.get("issue", {}).get("number") or
        (payload.get("check_suite", {}).get("pull_requests") or [{}])[0].get("number")
    )

    if not pr_number:
        print("[SKIP] Event not associated with an open Pull Request.")
        return

    print(f"[START] Real-time Triage webhook trigger: '{event_name}' for PR #{pr_number}")

    try:
        # Initialize GitHub client and Rules Engine
        client = GitHubAPIClient(token, repo_name)
        engine = RulesEngine(client, dry_run=args.dry_run)
        
        # Load YML configuration
        routing_config = engine.load_routing_config()

        # Register real-time rules
        engine.add_rule(FileRoutingRule(routing_config))
        engine.add_rule(LabelLifecycleRule())
        engine.add_rule(ReviewerApprovalRule(routing_config))

        # Fetch live PR details and run engine
        context = client.get_pr_context(pr_number, event_name=event_name, event_payload=payload)
        
        # Skip evaluations for draft PRs
        if context.is_draft:
            print(f"[SKIP] Pull request #{pr_number} is a draft.")
            return

        engine.run(context)
        print("[SUCCESS] Real-time triage automated evaluations completed.")
    except Exception as e:
        import traceback
        print("[ERROR] Real-time triage webhook processing failed.", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
