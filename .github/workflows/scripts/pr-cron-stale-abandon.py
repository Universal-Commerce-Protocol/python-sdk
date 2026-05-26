#!/usr/bin/env python3
# PEP 723 - Inline script metadata (allows runners like 'uv' or 'pipx' to auto-run with dependencies)
# /// script
# dependencies = [
#   "pygithub",
# ]
# ///
import os
import sys
from datetime import datetime, timedelta, timezone
from github import Github, Auth

def main():
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("[ERROR] GH_TOKEN or GITHUB_TOKEN is not set.")
        sys.exit(1)

    auth = Auth.Token(token)
    g = Github(auth=auth)
    repo_name = os.environ.get("GITHUB_REPOSITORY")
    repo = g.get_repo(repo_name)
    
    # Threshold Config (abandon is an additional X days on top of stale)
    STALE_THRESHOLD_DAYS = 30
    ADDITIONAL_ABANDON_DAYS = 7
    ABANDON_THRESHOLD_DAYS = STALE_THRESHOLD_DAYS + ADDITIONAL_ABANDON_DAYS

    # Label Constants
    LABEL_UNDER_REVIEW = "status:under-review"
    LABEL_BLOCKED = "blocked"
    LABEL_STALE_REVIEW = "status:stale-review"
    LABEL_NEEDS_TRIAGE = "status:needs-triage"
    LABEL_ABANDON_CANDIDATE = "status:abandon-candidate"

    now = datetime.now(timezone.utc)
    stale_limit = now - timedelta(days=STALE_THRESHOLD_DAYS)
    abandon_limit = now - timedelta(days=ABANDON_THRESHOLD_DAYS)

    print(f"[CONFIG] Stale limit: >{STALE_THRESHOLD_DAYS} days ({stale_limit.isoformat()})")
    print(f"[CONFIG] Abandon limit: >{ABANDON_THRESHOLD_DAYS} days ({abandon_limit.isoformat()})")

    # Fetch all open pull requests
    pulls = repo.get_pulls(state="open")
    
    stale_found = 0
    stale_labeled = 0
    abandon_found = 0
    abandon_labeled = 0
    already_labeled_count = 0
    total_scanned = 0

    for pr in pulls:
        total_scanned += 1
        try:
            if pr.draft:
                continue

            # pr.updated_at is already timezone-aware in PyGithub (utc)
            updated_at = pr.updated_at
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
                
            labels = [l.name for l in pr.get_labels()]

            is_stale = updated_at < stale_limit
            is_abandon_candidate = updated_at < abandon_limit

            # Determine matching category
            is_stale_review = is_stale and (LABEL_UNDER_REVIEW in labels)
            is_blocked_abandon = is_abandon_candidate and (LABEL_BLOCKED in labels)

            if not is_stale_review and not is_blocked_abandon:
                continue

            print(f'[INACTIVE] PR #{pr.number} "{pr.title}" is inactive since {pr.updated_at.isoformat()}')

            # Select target labels based on matching category
            if is_blocked_abandon:
                target_labels = [LABEL_STALE_REVIEW, LABEL_ABANDON_CANDIDATE, LABEL_NEEDS_TRIAGE]
                abandon_found += 1
            else:
                target_labels = [LABEL_STALE_REVIEW, LABEL_NEEDS_TRIAGE]
                stale_found += 1

            # Keep only labels that are missing from the PR
            missing_labels = [l for l in target_labels if l not in labels]

            if missing_labels:
                print(f'[ACTION] PR #{pr.number} "{pr.title}" is inactive. Adding missing labels: {missing_labels}')
                # PyGithub add_to_labels accepts iterable of strings or label objects
                for label in missing_labels:
                    pr.add_to_labels(label)

                comment_body = ""
                # Abandon candidate notification comment
                if is_blocked_abandon and (LABEL_ABANDON_CANDIDATE in missing_labels):
                    comment_body = (
                        f"This pull request has been blocked and inactive for "
                        f"{ABANDON_THRESHOLD_DAYS} days. "
                        f"It has been marked as an abandon candidate. "
                        f"Please resolve the blockers to resume review."
                    )
                # Stale review notification comment
                elif is_stale_review and (LABEL_STALE_REVIEW in missing_labels):
                    comment_body = (
                        f"This pull request has been inactive for "
                        f"{STALE_THRESHOLD_DAYS} days. "
                        f"Could you please provide an update or follow up on reviews?"
                    )

                if comment_body:
                    print(f"[ACTION] Posting stale/abandon comment on PR #{pr.number}")
                    pr.create_issue_comment(comment_body)

                if is_stale_review:
                    stale_labeled += 1
                if is_blocked_abandon:
                    abandon_labeled += 1
            else:
                already_labeled_count += 1
        except Exception as e:
            print(f"[ERROR] Failed to process PR #{pr.number} due to error: {e}", file=sys.stderr)

    print("\n========================================")
    print("     STALE TRACKER RUN SUMMARY")
    print("========================================")
    print(f"Total Open PRs Scanned:          {total_scanned}")
    print(f"PRs Already Correctly Labeled:   {already_labeled_count}")
    print("----------------------------------------")
    print(f"Stale Reviews (>{STALE_THRESHOLD_DAYS}d) Found:      {stale_found}")
    print(f"Stale Reviews Newly Labeled:     {stale_labeled}")
    print("----------------------------------------")
    print(f"Abandon Candidates (>{ABANDON_THRESHOLD_DAYS}d) Found:  {abandon_found}")
    print(f"Abandon Candidates Newly Labeled: {abandon_labeled}")
    print("========================================\n")

if __name__ == "__main__":
    main()
