# -*- coding: utf-8 -*-
"""
triage/rules_engine.py
~~~~~~~~~~~~~~~~~~~~~~

This module implements the core RulesEngine orchestrator that loads configs,
sequentially executes rules, resolves contradictions, and batches updates.

:copyright: (c) 2026 Universal Commerce Protocol.
:license: Apache-2.0, see LICENSE for more details.
"""
import os
import yaml
from typing import List
from .models import PRContext, RuleResult
from .github_api import GitHubAPIClient
from .rules import BaseRule

class RulesEngine:
    def __init__(self, client: GitHubAPIClient, dry_run: bool = False):
        self.client = client
        self.dry_run = dry_run
        self.rules: List[BaseRule] = []

    def add_rule(self, rule: BaseRule):
        """Registers a triage rule."""
        self.rules.append(rule)

    def load_routing_config(self) -> list:
        """Helper to load and parse the UCP_PR_REVIEW_ROUTING.yml configuration."""
        config_path = os.path.join(os.path.dirname(__file__), "UCP_PR_REVIEW_ROUTING.yml")
        if not os.path.exists(config_path):
            print(f"[WARNING] Config not found at standard module path. Attempting root paths.")
            # Fallback to sibling folder path configuration
            config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "UCP_PR_REVIEW_ROUTING.yml")
            
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)
                self.allowed_repos = config.get("allowed_repositories", [])
                return config.get("routing_rules", [])
        except Exception as e:
            print(f"[ERROR] Failed to load YAML routing rules config: {e}")
            self.allowed_repos = []
            return []

    def is_repository_allowed(self, current_repo: str) -> bool:
        """Checks if the active executing repository matches the allowed scope."""
        if not hasattr(self, "allowed_repos") or not self.allowed_repos:
            # If no restrictions are defined, allow by default
            return True
        
        # Ignore casing for verification robust checks
        allowed_lower = {repo.lower() for repo in self.allowed_repos}
        return current_repo.lower() in allowed_lower

    def run(self, context: PRContext) -> str:
        """Sequentially evaluates all registered rules, aggregates operations, and batches label and comment updates."""
        print(f"[ENGINE] Starting Rules Engine run for PR #{context.pr_number} in '{context.repo_name}'")
        
        # 0. Verify target repository scope permissions
        if not self.is_repository_allowed(context.repo_name):
            print(f"[ENGINE] [SKIP] Repository '{context.repo_name}' is not in the permitted execution scope.")
            return "Skipped: Repository not allowed"

        labels_to_add = set()
        labels_to_remove = set()
        comments_to_create = []
        actions_summaries = []

        for rule in self.rules:
            try:
                result: RuleResult = rule.evaluate(context, self.client)
                
                # Aggregate changes
                labels_to_add.update(result.labels_to_add)
                labels_to_remove.update(result.labels_to_remove)
                comments_to_create.extend(result.comments_to_create)
                
                # Calculate active additions and removals for this specific rule to display cleanly
                active_adds = result.labels_to_add - context.labels
                active_removes = result.labels_to_remove.intersection(context.labels)
                
                status_str = "SKIPPED" if "Skipped" in result.action_taken else "EVALUATED"
                changes_str = ""
                if active_adds:
                    changes_str += f" | Added: {list(active_adds)}"
                if active_removes:
                    changes_str += f" | Removed: {list(active_removes)}"
                if not active_adds and not active_removes:
                    changes_str += " | No changes"

                quoted_rule_name = f"'{rule.name}'"
                print(f"  - [RULE] STATUS: {status_str:<9} | Rule: {quoted_rule_name:<42} | Action: ({result.action_taken}){changes_str}")
                actions_summaries.append(f"{rule.name}: {result.action_taken}")
            except Exception as e:
                quoted_rule_name = f"'{rule.name}'"
                print(f"  - [RULE] STATUS: FAILED    | Rule: {quoted_rule_name:<42} | Error: ({e})")

        # Resolve contradictions (adding and removing the same label)
        contradictions = labels_to_add.intersection(labels_to_remove)
        if contradictions:
            print(f"[ENGINE] Warning: Contradicting labels operations resolved (favoring addition): {contradictions}")
            # Favor addition: remove them from removal set
            labels_to_remove = labels_to_remove - contradictions

        # Clean up current context labels from the sets
        labels_to_add = labels_to_add - context.labels
        labels_to_remove = labels_to_remove.intersection(context.labels)

        # Batched dry-run vs actual API updates
        if self.dry_run:
            print("\n======================================================================")
            print("                       DRY-RUN EXECUTION DETAILS")
            print("======================================================================")
            print("[DRY-RUN] Staging evaluated PR mutations (Live API updates skipped):")
            if labels_to_add:
                print(f"  - [DRY-RUN] STATUS: ADD_LABELS | Applied: {list(labels_to_add)}")
            if labels_to_remove:
                print(f"  - [DRY-RUN] STATUS: REM_LABELS | Revoked: {list(labels_to_remove)}")
            for comment in comments_to_create:
                # Quote and truncate comments to prevent messy console wrapping
                truncated_comment = comment[:65] + "..." if len(comment) > 65 else comment
                print(f"  - [DRY-RUN] STATUS: ADD_COMM   | Comment: '{truncated_comment}'")
            if not labels_to_add and not labels_to_remove and not comments_to_create:
                print("  - [DRY-RUN] STATUS: NO_CHANGES | No mutations required")
            print("======================================================================\n")
        else:
            # Batch actual GitHub API modifications
            if labels_to_add:
                self.client.add_labels_to_pr(context.pr_number, labels_to_add)
            if labels_to_remove:
                self.client.remove_labels_from_pr(context.pr_number, labels_to_remove)
            
            # Create comments sequentially
            for comment in comments_to_create:
                self.client.create_comment(context.pr_number, comment)

        summary_action = "; ".join(actions_summaries)
        print(f"[ENGINE] Rules Engine execution completed for PR #{context.pr_number}. Summary: {summary_action}")
        return summary_action
