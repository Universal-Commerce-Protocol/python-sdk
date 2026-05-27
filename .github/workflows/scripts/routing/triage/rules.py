# -*- coding: utf-8 -*-
"""
triage/rules.py
~~~~~~~~~~~~~~~

This module contains concrete rule check implementations inheriting from BaseRule.
Handles pattern routing, dynamic approvals, blocked/resume lifecycles, and inactivity detection.

:copyright: (c) 2026 Universal Commerce Protocol.
:license: Apache-2.0, see LICENSE for more details.
"""
import fnmatch
from datetime import datetime, timedelta, timezone
from typing import Set, Tuple
from .models import PRContext, RuleResult, Label, ReviewInfo
from .github_api import GitHubAPIClient

# ==============================================================================
# Base Rule Class
# ==============================================================================
class BaseRule:
    def __init__(self, name: str):
        self.name = name

    def evaluate(self, context: PRContext, client: GitHubAPIClient) -> RuleResult:
        raise NotImplementedError("Rules must implement evaluate()")

# ==============================================================================
# Rule 1: Dynamic File Pattern Routing Rule
# ==============================================================================
class FileRoutingRule(BaseRule):
    """Matches modified files against UCP_PR_REVIEW_ROUTING.yml configurations."""
    def __init__(self, rules_config: list):
        super().__init__("File Pattern Routing Rule")
        self.config = rules_config

    def evaluate(self, context: PRContext, client: GitHubAPIClient) -> RuleResult:
        labels_to_add = set()
        labels_to_remove = set()
        
        # Skip for draft PRs or if core CI Checks are failing/pending
        if context.is_draft:
            return RuleResult(self.name, satisfied=True, action_taken="Skipped: Draft PR")
            
        if not context.ci_passed:
            print(f"[SKIP] PR #{context.pr_number} has pending or failed CI status checks. Ingestion suspended.")
            return RuleResult(self.name, satisfied=True, action_taken="Skipped: Core CI Checks failing/pending")

        # Determine which routing files matched
        matched_any = False
        for rule in self.config:
            rule_name = rule.get("name", f"Rule")
            patterns = rule.get("patterns", [])
            review_reqs = rule.get("review_requirements", {})
            allowed_repos = rule.get("repositories", [])

            # Check if this specific rule is allowed to execute on the current repository
            if allowed_repos:
                allowed_lower = {repo.lower() for repo in allowed_repos}
                if context.repo_name.lower() not in allowed_lower:
                    print(f"[RULE] [SKIP] Rule '{rule_name}' is restricted and cannot execute on '{context.repo_name}'.")
                    continue

            rule_matches = False
            for filepath in context.modified_files:
                for pattern in patterns:
                    if fnmatch.fnmatch(filepath, pattern) or filepath.startswith(pattern.replace("**/", "")):
                        rule_matches = True
                        break
                if rule_matches:
                    break

            if rule_matches:
                matched_any = True
                # Inspect if each required group's reviews are met
                for team_handle, req_details in review_reqs.items():
                    threshold = req_details.get("threshold", 1)
                    needs_label = req_details.get("needs_review_label")
                    approved_label = req_details.get("approved_label")

                    satisfied, _ = verify_team_approvals(context, team_handle, threshold, client)
                    
                    if not satisfied:
                        if needs_label:
                            labels_to_add.add(needs_label)
                        if approved_label:
                            labels_to_remove.add(approved_label)
                    else:
                        if needs_label:
                            labels_to_remove.add(needs_label)
                        if approved_label:
                            labels_to_add.add(approved_label)

        # Ingest: if no specific protocol/rules match, or standard triage applies, flag needs-triage
        if not matched_any and Label.LABEL_UNDER_REVIEW not in context.labels:
            labels_to_add.add(Label.LABEL_NEEDS_TRIAGE)

        return RuleResult(
            self.name,
            satisfied=True,
            labels_to_add=labels_to_add,
            labels_to_remove=labels_to_remove,
            action_taken="Evaluated folder file mappings and applied review requirements."
        )

# ==============================================================================
# Rule 2: Dynamic Reviewer approvals & Security Check Rule
# ==============================================================================
class ReviewerApprovalRule(BaseRule):
    """Verifies custom team approval criteria and enforces permission guardrails."""
    def __init__(self, rules_config: list):
        super().__init__("Reviewer Approval Rule")
        self.config = rules_config

    def evaluate(self, context: PRContext, client: GitHubAPIClient) -> RuleResult:
        labels_to_add = set()
        labels_to_remove = set()
        comments = []

        # ==============================================================================
        # 0. Enforce Guardrail Logic FIRST (Always executes, prevents early return bypass)
        # ==============================================================================
        event_user = context.event_payload.get("sender", {}).get("login")
        event_action = context.event_payload.get("action")
        org_name = context.repo_name.split("/")[0]

        # A. Guard against unauthorized addition of TC Majority Label
        if Label.LABEL_TC_MAJORITY_APPROVED in context.labels:
            if event_user and context.event_name == "pull_request" and event_action == "labeled":
                label_added = context.event_payload.get("label", {}).get("name")
                if label_added == Label.LABEL_TC_MAJORITY_APPROVED:
                    is_tc = client.check_team_membership(org_name, "tech-council", event_user)
                    is_devops = client.check_team_membership(org_name, "devops-maintainers", event_user)
                    if not is_tc and not is_devops:
                        print(f"[GUARDRAIL] Unauthorized user {event_user} applied majority label. Revoking.")
                        labels_to_remove.add(Label.LABEL_TC_MAJORITY_APPROVED)
                        comments.append(
                            f"Warning: @{event_user}, you do not have permission to apply "
                            f"`{Label.LABEL_TC_MAJORITY_APPROVED}`. This action has been automatically reverted."
                        )

        # B. Guard against unauthorized removal of active needs-review or approved labels
        if context.event_name == "pull_request" and event_action == "unlabeled":
            label_removed = context.event_payload.get("label", {}).get("name")
            
            for rule in self.config:
                for team_handle, req_details in rule.get("review_requirements", {}).items():
                    needs_label = req_details.get("needs_review_label")
                    approved_label = req_details.get("approved_label")
                    
                    # Check if the removed label is either the needs-review or the approved label of this team
                    is_needs = needs_label and label_removed == needs_label
                    is_approved = approved_label and label_removed == approved_label
                    
                    if is_needs or is_approved:
                        # Verify if the user who removed it is a member of the team or DevOps
                        clean_handle = team_handle.lstrip("@")
                        team_org, team_slug = clean_handle.split("/", 1)
                        
                        is_team_member = client.check_team_membership(team_org, team_slug, event_user)
                        is_devops = client.check_team_membership(org_name, "devops-maintainers", event_user)
                        
                        if not is_team_member and not is_devops:
                            satisfied, _ = verify_team_approvals(context, team_handle, req_details.get("threshold", 1), client)
                            
                            # Scenario A: Removed needs-review label while reviews are still pending
                            if is_needs and not satisfied:
                                print(f"[GUARDRAIL] Unauthorized user {event_user} removed required label {needs_label}. Re-applying.")
                                labels_to_add.add(needs_label)
                                comments.append(
                                    f"Warning: @{event_user}, you do not have permission to remove "
                                    f"`{needs_label}`. Reviews from `{team_handle}` are still pending. "
                                    f"This action has been automatically reverted."
                                )
                            
                            # Scenario B: Removed approved label while reviews are fully satisfied
                            elif is_approved and satisfied:
                                print(f"[GUARDRAIL] Unauthorized user {event_user} removed approved label {approved_label}. Re-applying.")
                                labels_to_add.add(approved_label)
                                comments.append(
                                    f"Warning: @{event_user}, you do not have permission to remove "
                                    f"`{approved_label}`. Reviews from `{team_handle}` are satisfied and approved. "
                                    f"This action has been automatically reverted."
                                )

            # If guardrails re-applied labels, we exit early with results to prevent regular evaluation overrides
            if labels_to_add or labels_to_remove:
                return RuleResult(
                    self.name,
                    satisfied=False,
                    labels_to_add=labels_to_add,
                    labels_to_remove=labels_to_remove,
                    comments_to_create=comments,
                    action_taken="Guardrails override triggered to revert unauthorized label modifications."
                )

        # ==============================================================================
        # 1. Superpower Override (e.g., Amit's approval satisfies all rules)
        # ==============================================================================
        SUPERPOWER_USERS = {"amithanda"}
        for review in context.reviews:
            if review.user in SUPERPOWER_USERS and review.state == "APPROVED":
                print(f"[SUPERPOWER] Override triggered by approval from {review.user}")
                # Clear all pending review tags, apply approvals
                for rule in self.config:
                    for team, details in rule.get("review_requirements", {}).items():
                        if details.get("needs_review_label"):
                            labels_to_remove.add(details.get("needs_review_label"))
                        if details.get("approved_label"):
                            labels_to_add.add(details.get("approved_label"))
                
                labels_to_add.add(Label.LABEL_APPROVED)
                labels_to_add.add(Label.LABEL_READY_TO_MERGE)
                return RuleResult(
                    self.name,
                    satisfied=True,
                    labels_to_add=labels_to_add,
                    labels_to_remove=labels_to_remove,
                    action_taken="Superpower approval override triggered."
                )

        # ==============================================================================
        # 2. Review requirements matching core spec rules or relaxed SDK settings
        # ==============================================================================
        is_sdk = any(sdk_repo in context.repo_name.lower() for sdk_repo in ["sdk", "meeting-minutes"])
        
        all_rules_satisfied = True
        rules_evaluated = 0

        for rule in self.config:
            rule_name = rule.get("name", f"Rule")
            patterns = rule.get("patterns", [])
            review_reqs = rule.get("review_requirements", {})
            allowed_repos = rule.get("repositories", [])

            # Check if this specific rule is allowed to execute on the current repository
            if allowed_repos:
                allowed_lower = {repo.lower() for repo in allowed_repos}
                if context.repo_name.lower() not in allowed_lower:
                    print(f"[RULE] [SKIP] Rule '{rule_name}' is restricted and cannot execute approvals verification on '{context.repo_name}'.")
                    continue

            # Check if this rule matches PR's files
            matches = False
            for filepath in context.modified_files:
                for pattern in patterns:
                    if fnmatch.fnmatch(filepath, pattern) or filepath.startswith(pattern.replace("**/", "")):
                        matches = True
                        break
                if matches:
                    break

            if not matches:
                continue

            rules_evaluated += 1
            
            # Verify approvals for each required team
            for team_handle, req_details in review_reqs.items():
                threshold = req_details.get("threshold", 1)
                needs_label = req_details.get("needs_review_label")
                approved_label = req_details.get("approved_label")

                # SDK relaxed rules override: 1 team approval is always sufficient
                if is_sdk:
                    threshold = 1

                satisfied, current_approvals = verify_team_approvals(context, team_handle, threshold, client)

                # Guard against manual label addition by non-TC members
                if needs_label == Label.LABEL_NEEDS_TC_REVIEW and Label.LABEL_TC_MAJORITY_APPROVED in context.labels:
                    # If majority label is present, it acts as meeting standard override
                    satisfied = True

                if not satisfied:
                    all_rules_satisfied = False
                    if needs_label:
                        labels_to_add.add(needs_label)
                    if approved_label:
                        labels_to_remove.add(approved_label)
                else:
                    if needs_label:
                        labels_to_remove.add(needs_label)
                    if approved_label:
                        labels_to_add.add(approved_label)

        # If all rules passed, transition to ready-to-merge
        if all_rules_satisfied and rules_evaluated > 0:
            labels_to_add.add(Label.LABEL_APPROVED)
            labels_to_add.add(Label.LABEL_READY_TO_MERGE)
            # Cleanup pending needs labels
            for rule in self.config:
                for team, details in rule.get("review_requirements", {}).items():
                    if details.get("needs_review_label"):
                        labels_to_remove.add(details.get("needs_review_label"))
        else:
            labels_to_remove.add(Label.LABEL_APPROVED)
            labels_to_remove.add(Label.LABEL_READY_TO_MERGE)

        return RuleResult(
            self.name,
            satisfied=all_rules_satisfied,
            labels_to_add=labels_to_add,
            labels_to_remove=labels_to_remove,
            comments_to_create=comments,
            action_taken="Verified team approval thresholds."
        )

# ==============================================================================
# Rule 3: Block/Resume Lifecycle state-machine Rule
# ==============================================================================
class LabelLifecycleRule(BaseRule):
    """Automates blocked/resumed PR transitions based on labeling and comments."""
    def __init__(self):
        super().__init__("Label Lifecycle Rule")

    def evaluate(self, context: PRContext, client: GitHubAPIClient) -> RuleResult:
        labels_to_add = set()
        labels_to_remove = set()
        action = "Evaluated lifecycle rules."

        event_action = context.event_payload.get("action")

        # 1. Manual block label added
        if context.event_name == "pull_request" and event_action == "labeled":
            label_added = context.event_payload.get("label", {}).get("name")
            if label_added == Label.LABEL_BLOCKED:
                labels_to_remove.add(Label.LABEL_UNDER_REVIEW)
                action = "Blocked: suspended under-review state"

        # 2. Author commits/pushes a new synchronized update
        if context.event_name == "pull_request" and event_action == "synchronize":
            if Label.LABEL_BLOCKED in context.labels:
                labels_to_add.add(Label.LABEL_UNDER_REVIEW)
                labels_to_remove.add(Label.LABEL_BLOCKED)
                action = "Resumed: synch push removed blocker label"

        # 3. Author comments on a blocked PR
        if context.event_name == "issue_comment" and event_action == "created":
            comment_author = context.event_payload.get("comment", {}).get("user", {}).get("login")
            if comment_author == context.author and Label.LABEL_BLOCKED in context.labels:
                labels_to_add.add(Label.LABEL_UNDER_REVIEW)
                labels_to_remove.add(Label.LABEL_BLOCKED)
                action = "Resumed: comment by author resolved blocker label"

        return RuleResult(
            self.name,
            satisfied=True,
            labels_to_add=labels_to_add,
            labels_to_remove=labels_to_remove,
            action_taken=action
        )

# ==============================================================================
# Rule 4 & 5: Integrated Cron Stale PR & Abandon Candidates Rules
# ==============================================================================
class StalePRRule(BaseRule):
    """Detects and labels inactive PRs waiting on review or blocked for too long."""
    def __init__(self, stale_threshold_days: int = 30, abandon_threshold_days: int = 37):
        super().__init__("Stale PR Inactivity Rule")
        self.stale_days = stale_threshold_days
        self.abandon_days = abandon_threshold_days

    def evaluate(self, context: PRContext, client: GitHubAPIClient) -> RuleResult:
        labels_to_add = set()
        comments = []

        if context.is_draft:
            return RuleResult(self.name, satisfied=True, action_taken="Skipped: Draft PR")

        now = datetime.now(timezone.utc)
        updated_at = context.updated_at
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)

        stale_limit = now - timedelta(days=self.stale_days)
        abandon_limit = now - timedelta(days=self.abandon_days)

        is_inactive_stale = updated_at < stale_limit
        is_inactive_abandon = updated_at < abandon_limit

        # Scenario A: abandoned (no reviews for long time, abandon candidate)
        if is_inactive_abandon and Label.LABEL_BLOCKED in context.labels:
            if Label.LABEL_ABANDON_CANDIDATE not in context.labels:
                labels_to_add.add(Label.LABEL_ABANDON_CANDIDATE)
                labels_to_add.add(Label.LABEL_NEEDS_TRIAGE)
                labels_to_add.add(Label.LABEL_STALE_REVIEW)
                comments.append(
                    f"This pull request has been blocked and inactive for "
                    f"{self.abandon_days} days. "
                    f"It has been marked as an `{Label.LABEL_ABANDON_CANDIDATE}`. "
                    f"Please resolve the blockers to resume review."
                )

        # Scenario B: PR waiting on reviews
        elif is_inactive_stale and Label.LABEL_UNDER_REVIEW in context.labels:
            if Label.LABEL_STALE_REVIEW not in context.labels:
                labels_to_add.add(Label.LABEL_STALE_REVIEW)
                labels_to_add.add(Label.LABEL_NEEDS_TRIAGE)
                comments.append(
                    f"This pull request has been inactive for "
                    f"{self.stale_days} days. "
                    f"Could you please provide an update or follow up on reviews?"
                )

        return RuleResult(
            self.name,
            satisfied=True,
            labels_to_add=labels_to_add,
            comments_to_create=comments,
            action_taken="Evaluated inactivity limits."
        )

# ==============================================================================
# Private Logic Verification Helper
# ==============================================================================
def verify_team_approvals(context: PRContext, team_handle: str, threshold: any, client: GitHubAPIClient) -> Tuple[bool, int]:
    """Parses dynamic handles and checks dynamic pygithub approvals count."""
    # Handle parsing
    if not team_handle.startswith("@") or "/" not in team_handle:
        return False, 0

    clean_handle = team_handle.lstrip("@")
    org_name, team_slug = clean_handle.split("/", 1)

    approvals = 0
    approved_users = set()

    # Select active approvals (including superpower overrides check)
    SUPERPOWER_USERS = {"amithanda"}
    has_superpower_approval = False
    
    for review in context.reviews:
        if review.state == "APPROVED":
            if review.user in SUPERPOWER_USERS:
                has_superpower_approval = True
            is_member = client.check_team_membership(org_name, team_slug, review.user)
            if is_member:
                approvals += 1
                approved_users.add(review.user)

    if has_superpower_approval:
        return True, approvals

    if threshold == "majority":
        # Programmatic majority overrides: TC triggers majority via manual majority label or meetings
        return Label.LABEL_TC_MAJORITY_APPROVED in context.labels, approvals

    try:
        required_count = int(threshold)
    except ValueError:
        required_count = 1

    return approvals >= required_count, approvals
