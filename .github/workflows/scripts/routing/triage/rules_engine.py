# rules_engine.py
import os
import yaml
from typing import List
from .models import PRContext, RuleResult
from .github_api import GitHubAPIClient
from .rules import BaseRule

class RulesEngine:
    def __init__(self, client: GitHubAPIClient):
        self.client = client
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
                return config.get("routing_rules", [])
        except Exception as e:
            print(f"[ERROR] Failed to load YAML routing rules config: {e}")
            return []

    def run(self, context: PRContext) -> str:
        """Sequentially evaluates all registered rules, aggregates operations, and batches label and comment updates."""
        print(f"[ENGINE] Starting Rules Engine run for PR #{context.pr_number} in '{context.repo_name}'")
        
        labels_to_add = set()
        labels_to_remove = set()
        comments_to_create = []
        actions_summaries = []

        for rule in self.rules:
            print(f"  - [EVALUATING] Rule: {rule.name}")
            try:
                result: RuleResult = rule.evaluate(context, self.client)
                
                # Aggregate changes
                labels_to_add.update(result.labels_to_add)
                labels_to_remove.update(result.labels_to_remove)
                comments_to_create.extend(result.comments_to_create)
                
                print(f"    [RESULT] Rule '{rule.name}' evaluated. Action: '{result.action_taken}'")
                actions_summaries.append(f"{rule.name}: {result.action_taken}")
            except Exception as e:
                print(f"    [ERROR] Rule '{rule.name}' raised an exception during execution: {e}")

        # Resolve contradictions (adding and removing the same label)
        contradictions = labels_to_add.intersection(labels_to_remove)
        if contradictions:
            print(f"[ENGINE] Warning: Contradicting labels operations resolved (favoring addition): {contradictions}")
            # Favor addition: remove them from removal set
            labels_to_remove = labels_to_remove - contradictions

        # Clean up current context labels from the sets
        labels_to_add = labels_to_add - context.labels
        labels_to_remove = labels_to_remove.intersection(context.labels)

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
