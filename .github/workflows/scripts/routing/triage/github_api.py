# -*- coding: utf-8 -*-
"""
triage/github_api.py
~~~~~~~~~~~~~~~~~~~~

This module provides the GitHubAPIClient wrapper to interact with pygithub APIs,
query org memberships dynamically, and batch transition PR operations.

:copyright: (c) 2026 Universal Commerce Protocol.
:license: Apache-2.0, see LICENSE for more details.
"""
import os
from typing import List, Set
from github import Github, Auth
from .models import PRContext, ReviewInfo

class GitHubAPIClient:
    def __init__(self, token: str, repo_name: str):
        self.auth = Auth.Token(token)
        self.g = Github(auth=self.auth)
        self.repo = self.g.get_repo(repo_name)
        self._team_members_cache = {}

    def get_pr_context(self, pr_number: int, event_name: str = "", event_payload: dict = None) -> PRContext:
        """Fetches pull request details and wraps them inside PRContext."""
        pr = self.repo.get_pull(pr_number)
        
        labels = {label.name for label in pr.get_labels()}
        modified_files = [f.filename for f in pr.get_files()]
        
        # Convert pygithub review models to ReviewInfo
        reviews = []
        for review in pr.get_reviews():
            reviews.append(
                ReviewInfo(
                    user=review.user.login,
                    state=review.state,
                    submitted_at=review.submitted_at
                )
            )

        # Resolve Dynamic CI checking status dynamically
        ci_passed = True
        try:
            commit = self.repo.get_commit(pr.head.sha)
            status = commit.get_combined_status()
            
            if status.state != "success" and status.total_count > 0:
                print(f"[INFO] Combined CI status state is currently: \"{status.state}\"")
                ci_passed = False
            else:
                # Check check runs (modern Checks API)
                check_runs = commit.get_check_runs()
                for run in check_runs:
                    name = run.name
                    # Skip triage automation checks to avoid deadlocks
                    if "Triage" in name or "validate" in name:
                        continue
                    if run.status != "completed":
                        ci_passed = False
                        break
                    elif run.conclusion not in ["success", "skipped", "neutral"]:
                        ci_passed = False
                        break
        except Exception as ce:
            print(f"[WARNING] Failed to dynamically query commit CI status: {ce}")

        return PRContext(
            pr_number=pr_number,
            repo_name=self.repo.full_name,
            title=pr.title,
            author=pr.user.login,
            is_draft=pr.draft,
            labels=labels,
            modified_files=modified_files,
            reviews=reviews,
            ci_passed=ci_passed,
            created_at=pr.created_at,
            updated_at=pr.updated_at,
            event_name=event_name,
            event_payload=event_payload or {}
        )

    def check_team_membership(self, org_name: str, team_slug: str, username: str) -> bool:
        """Checks if a given user is a member of a specific organization team dynamically."""
        cache_key = f"{org_name}/{team_slug}"
        
        # Dynamic caching to avoid repeated API requests within a run
        if cache_key not in self._team_members_cache:
            try:
                org = self.g.get_organization(org_name)
                team = org.get_team_by_slug(team_slug)
                members = {member.login for member in team.get_members()}
                self._team_members_cache[cache_key] = members
            except Exception as e:
                print(f"[WARNING] Failed to fetch team details for {cache_key}: {e}")
                self._team_members_cache[cache_key] = set()

        return username in self._team_members_cache[cache_key]

    def add_labels_to_pr(self, pr_number: int, labels: Set[str]):
        """Applies a set of labels to a target PR."""
        if not labels:
            return
        pr = self.repo.get_pull(pr_number)
        for label in labels:
            print(f"[API] Adding label: {label}")
            pr.add_to_labels(label)

    def remove_labels_from_pr(self, pr_number: int, labels: Set[str]):
        """Removes a set of labels from a target PR."""
        if not labels:
            return
        pr = self.repo.get_pull(pr_number)
        current_labels = {label.name for label in pr.get_labels()}
        for label in labels:
            if label in current_labels:
                print(f"[API] Removing label: {label}")
                pr.remove_from_labels(label)

    def create_comment(self, pr_number: int, body: str):
        """Posts an issue comment on the pull request."""
        pr = self.repo.get_pull(pr_number)
        print(f"[API] Creating issue comment on PR #{pr_number}")
        pr.create_issue_comment(body)
