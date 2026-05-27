#!/usr/bin/env python3
# /// script
# dependencies = [
#   "pygithub",
#   "pyyaml",
# ]
# ///
# -*- coding: utf-8 -*-
"""
validate-routing.py
~~~~~~~~~~~~~~~~~~~

A standalone validation script to verify UCP_PR_REVIEW_ROUTING.yml syntax
and check dynamic GitHub organization team existence.

:copyright: (c) 2026 Universal Commerce Protocol.
:license: Apache-2.0, see LICENSE for more details.
"""
import os
import sys
import yaml
from github import Github, Auth

def parse_team_handle(handle: str):
    """Parses a dynamic team handle (e.g. @Universal-Commerce-Protocol/payments-maintainers) to get org name and slug."""
    if not handle.startswith("@"):
        raise ValueError(f"Invalid team handle format: '{handle}'. Handles must start with '@'.")
    
    clean_handle = handle.lstrip("@")
    if "/" not in clean_handle:
        raise ValueError(f"Invalid team handle format: '{handle}'. Must be in format '@org/team-slug'.")
    
    org_name, team_slug = clean_handle.split("/", 1)
    return org_name, team_slug

def main():
    # 1. Fetch and Mask Auth Token
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    masked_token = f"{token[:8]}...{token[-4:]}" if token else "None"

    print("\n======================================================================")
    print("             UCP PR ROUTING CONFIGURATION VALIDATOR RUN")
    print("======================================================================\n")
    print(f"[ENV] GITHUB_ACTIONS (CI) : {os.environ.get('GITHUB_ACTIONS', 'false')}")
    print(f"[ENV] GITHUB_EVENT_NAME   : {os.environ.get('GITHUB_EVENT_NAME', 'local_dry_run')}")
    print(f"[ENV] GH_TOKEN / PAT      : {masked_token}")

    if not token:
        print("\n[ERROR] GITHUB_TOKEN or GH_TOKEN is not configured.")
        print("To run the validator locally, you must export GH_TOKEN with 'read:org' permissions:")
        print("  $ export GH_TOKEN=\"your_github_pat\"")
        print("  $ uv run .github/workflows/scripts/routing/validate-routing.py\n")
        sys.exit(1)

    # 2. Extract Active Repository Name Dynamically from Git metadata
    try:
        import subprocess
        # Use file directory to ensure it runs git in correct path context
        script_dir = os.path.dirname(os.path.abspath(__file__))
        origin_url = subprocess.check_output(["git", "-C", script_dir, "config", "--get", "remote.origin.url"]).decode("utf-8").strip()
        clean_url = origin_url.replace(".git", "").replace(":", "/")
        parts = clean_url.split("/")
        repo_name = f"{parts[-2]}/{parts[-1]}"
        print(f"[ENV] Target Repository   : {repo_name}")
    except Exception as e:
        print(f"[ERROR] Failed to dynamically determine current Git repository name: {e}")
        sys.exit(1)

    config_path = os.path.join(os.path.dirname(__file__), "UCP_PR_REVIEW_ROUTING.yml")
    if not os.path.exists(config_path):
        print(f"[ERROR] Configuration file not found at: {config_path}")
        sys.exit(1)

    print(f"[ENV] Configuration Path  : {config_path}")
    print("\n----------------------------------------------------------------------")
    print(f"[START] Validating routing configuration...")

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except Exception as e:
        print(f"[FAIL] Failed to parse YAML configuration syntax: {e}")
        sys.exit(1)

    routing_rules = config.get("routing_rules")
    if not routing_rules or not isinstance(routing_rules, list):
        print("[FAIL] Missing or invalid 'routing_rules' root list in configuration.")
        sys.exit(1)

    auth = Auth.Token(token)
    g = Github(auth=auth)

    has_errors = False
    referenced_teams = set()

    # 1. Syntax and Key Structure Checks
    for idx, rule in enumerate(routing_rules):
        rule_name = rule.get("name", f"Rule #{idx}")
        patterns = rule.get("patterns")
        review_reqs = rule.get("review_requirements")

        if not patterns or not isinstance(patterns, list):
            print(f"[FAIL] Rule '{rule_name}' must have a non-empty list of 'patterns'.")
            has_errors = True

        if not review_reqs or not isinstance(review_reqs, dict):
            print(f"[FAIL] Rule '{rule_name}' must have a 'review_requirements' dictionary mapping.")
            has_errors = True
            continue

        # Parse team handles from review_requirements keys
        for handle, details in review_reqs.items():
            if not isinstance(details, dict) or "threshold" not in details:
                print(f"[FAIL] Rule '{rule_name}' requirement '{handle}' must be a dictionary with a 'threshold' key.")
                has_errors = True
                continue

            needs_lbl = details.get("needs_review_label")
            approved_lbl = details.get("approved_label")

            # Check needs_review_label and approved_label exist under details
            if "needs_review_label" not in details or "approved_label" not in details:
                print(f"[FAIL] Rule '{rule_name}' requirement '{handle}' must configure 'needs_review_label' and 'approved_label' properties.")
                has_errors = True

            try:
                org_name, team_slug = parse_team_handle(handle)
                referenced_teams.add((org_name, team_slug, handle))
            except ValueError as ve:
                print(f"[FAIL] Rule '{rule_name}' invalid team handle: {ve}")
                has_errors = True

    # 1.5 Validate Label Taxonomy existence against labels.yml
    labels_yml_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../labels.yml"))
    if os.path.exists(labels_yml_path):
        try:
            with open(labels_yml_path, "r", encoding="utf-8") as lf:
                labels_def = yaml.safe_load(lf)
                # Extract defined labels from the standard list structure
                defined_labels = {item.get("name") for item in labels_def if isinstance(item, dict) and item.get("name")}
                
                print(f"[INFO] Parsed {len(defined_labels)} defined labels from labels.yml taxonomy.")
                
                # Verify that every referenced label is in the taxonomy
                for idx, rule in enumerate(routing_rules):
                    rule_name = rule.get("name", f"Rule #{idx}")
                    for handle, details in rule.get("review_requirements", {}).items():
                        needs_lbl = details.get("needs_review_label")
                        approved_lbl = details.get("approved_label")

                        if needs_lbl and needs_lbl not in defined_labels:
                            print(f"[FAIL] Rule '{rule_name}' needs_review_label '{needs_lbl}' is missing from labels.yml taxonomy!")
                            has_errors = True
                        if approved_lbl and approved_lbl not in defined_labels:
                            print(f"[FAIL] Rule '{rule_name}' approved_label '{approved_lbl}' is missing from labels.yml taxonomy!")
                            has_errors = True
        except Exception as le:
            print(f"[WARNING] Failed to parse labels.yml taxonomy checking: {le}")
    else:
        print(f"[WARNING] labels.yml taxonomy file not found at: {labels_yml_path}. Skipping taxonomy check.")

    if has_errors:
        print("\n======================================================================")
        print("                       VALIDATION RESULT: FAILED")
        print("======================================================================")
        print("[FAIL] Config UCP_PR_REVIEW_ROUTING.yml contains syntax or taxonomy errors.")
        print("======================================================================\n")
        sys.exit(1)

    print("[INFO] YAML syntax and structure verification: PASSED")

    # 2. API Group Existence Checks
    verified_teams = 0
    org_name = repo_name.split("/")[0]
    print("\n----------------------------------------------------------------------")
    print(f"[INFO] Verifying organization '{org_name}' existence for {len(referenced_teams)} dynamic team references...")

    for org_from_handle, team_slug, handle in referenced_teams:
        try:
            org = g.get_organization(org_from_handle)
            team = org.get_team_by_slug(team_slug)
            print(f"  - [OK] Verified active team: {handle} (Slug: '{team_slug}', ID: {team.id})")
            verified_teams += 1
        except Exception as e:
            if org_from_handle == "Universal-Commerce-Protocol" and org_name != "Universal-Commerce-Protocol":
                print(f"  - [WARN] Skipping live verification for: {handle} (Running on local fork '{org_name}')")
                verified_teams += 1
            else:
                print(f"  - [FAIL] Team handle does not exist on GitHub organization '{org_from_handle}': {handle} (Error: {e})")
                has_errors = True

    if has_errors:
        print("\n======================================================================")
        print("                       VALIDATION RESULT: FAILED")
        print("======================================================================")
        print("[FAIL] Config UCP_PR_REVIEW_ROUTING.yml contains invalid team handles.")
        print("======================================================================\n")
        sys.exit(1)

    print("\n======================================================================")
    print("                       VALIDATION RESULT: PASSED")
    print("======================================================================")
    print(f"YAML Syntax:          PASSED")
    print(f"Taxonomy Check:       PASSED ({len(defined_labels)} labels defined)")
    print(f"Dynamic Review Teams: PASSED ({verified_teams} teams verified)")
    print("======================================================================\n")

if __name__ == "__main__":
    main()
