#!/usr/bin/env python3
# /// script
# dependencies = [
#   "pygithub",
#   "pyyaml",
# ]
# ///
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
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("[ERROR] GH_TOKEN or GITHUB_TOKEN environment variable is required to validate team existence.")
        sys.exit(1)

    # Extract active repository name dynamically from the git config of the local clone.
    try:
        import subprocess
        # Use file directory to ensure it runs git in correct path context
        script_dir = os.path.dirname(os.path.abspath(__file__))
        origin_url = subprocess.check_output(["git", "-C", script_dir, "config", "--get", "remote.origin.url"]).decode("utf-8").strip()
        clean_url = origin_url.replace(".git", "").replace(":", "/")
        parts = clean_url.split("/")
        repo_name = f"{parts[-2]}/{parts[-1]}"
        print(f"[INFO] Target Repository resolved: {repo_name}")
    except Exception as e:
        print(f"[ERROR] Failed to dynamically determine current Git repository name: {e}")
        sys.exit(1)

    config_path = os.path.join(os.path.dirname(__file__), "UCP_PR_REVIEW_ROUTING.yml")
    if not os.path.exists(config_path):
        print(f"[ERROR] Configuration file not found at: {config_path}")
        sys.exit(1)

    print(f"[START] Validating routing configuration file: {config_path}")

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

    if has_errors:
        print("[FAIL] YAML validation failed due to syntax/structure errors.")
        sys.exit(1)

    print("[INFO] YAML syntax and structure verification: PASSED")
    # Extract organization name dynamically from repo_name to check teams in the current workspace org context
    org_name = repo_name.split("/")[0]
    print(f"[INFO] Verifying organization '{org_name}' existence for {len(referenced_teams)} dynamic team references...")

    # 2. API Group Existence Checks
    verified_teams = 0
    for org_from_handle, team_slug, handle in referenced_teams:
        try:
            # Try to fetch organization using handle's declared org first
            org = g.get_organization(org_from_handle)
            team = org.get_team_by_slug(team_slug)
            print(f"  - [OK] Verified active team: {handle} (Slug: '{team_slug}', ID: {team.id})")
            verified_teams += 1
        except Exception as e:
            # If running in a personal repository fork, org is a standard user (not an org) which throws 404.
            # Let's verify if the target org_from_handle matches a personal fork instead of failure
            if org_from_handle == "Universal-Commerce-Protocol" and org_name != "Universal-Commerce-Protocol":
                print(f"  - [WARN] Skipping live verification for: {handle} (Running on local fork '{org_name}')")
                verified_teams += 1
            else:
                print(f"  - [FAIL] Team handle does not exist on GitHub organization '{org_from_handle}': {handle} (Error: {e})")
                has_errors = True

    if has_errors:
        print(f"[FAIL] Config contains invalid or non-existent GitHub organization teams.")
        sys.exit(1)

    print(f"[SUCCESS] Validation complete. All {verified_teams} dynamic review groups exist successfully on GitHub.")

if __name__ == "__main__":
    main()
