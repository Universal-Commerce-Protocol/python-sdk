#!/usr/bin/env python3
# /// script
# dependencies = [
#   "pygithub",
#   "pyyaml",
# ]
# ///
# -*- coding: utf-8 -*-
"""
test_routing.py
~~~~~~~~~~~~~~~

Comprehensive unit test suite verifying dynamic triage matching, reviewer
approvals, stale inactivity limits, and superpower overrides.

:copyright: (c) 2026 Universal Commerce Protocol.
:license: Apache-2.0, see LICENSE for more details.
"""
import sys
import os
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

# Dynamically insert scripts/routing to sys.path to resolve imports cleanly
script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

from triage.models import PRContext, ReviewInfo, Label
from triage.rules import FileRoutingRule, ReviewerApprovalRule, LabelLifecycleRule, StalePRRule
from triage.github_api import GitHubAPIClient

class TestTriageRules(unittest.TestCase):
    def setUp(self):
        # Standard test configuration
        self.mock_config = [
            {
                "name": "Core Protocol & Spec",
                "patterns": ["schemas/**/*.json"],
                "review_requirements": {
                    "@Universal-Commerce-Protocol/tech-council": {
                        "threshold": "majority",
                        "needs_review_label": "gov:needs-tc-review",
                        "approved_label": "gov:tc-approved"
                    }
                }
            },
            {
                "name": "Payments Custom rules",
                "patterns": ["src/components/payments/**"],
                "review_requirements": {
                    "@Universal-Commerce-Protocol/payments-maintainers": {
                        "threshold": 1,
                        "needs_review_label": "status:review-needed-payments-maintainers",
                        "approved_label": "status:payments-approved"
                    }
                }
            }
        ]
        # Mock api client
        self.mock_client = MagicMock(spec=GitHubAPIClient)

    def test_file_routing_rule_matches_correctly(self):
        """Verifies FileRoutingRule flags needs_review label when file patterns match."""
        # Scenario: Changed schemas file, missing tech-council approval
        self.mock_client.check_team_membership.return_value = False
        
        context = PRContext(
            pr_number=100,
            repo_name="Universal-Commerce-Protocol/ucp",
            title="feat: update core transaction schema",
            author="developer1",
            is_draft=False,
            labels=set(),
            modified_files=["schemas/v1/transaction.json"],
            reviews=[],
            ci_passed=True # Enforce CI is passing
        )

        rule = FileRoutingRule(self.mock_config)
        result = rule.evaluate(context, self.mock_client)

        self.assertTrue("gov:needs-tc-review" in result.labels_to_add)
        self.assertTrue("gov:tc-approved" in result.labels_to_remove)

    def test_file_routing_rule_suspends_on_ci_failure(self):
        """Verifies FileRoutingRule suspends triage ingestion if core CI status checks are failing."""
        context = PRContext(
            pr_number=101,
            repo_name="Universal-Commerce-Protocol/ucp",
            title="feat: update core spec on failing CI",
            author="developer1",
            is_draft=False,
            labels=set(),
            modified_files=["schemas/v1/transaction.json"],
            reviews=[],
            ci_passed=False # CI is failing or pending
        )

        rule = FileRoutingRule(self.mock_config)
        result = rule.evaluate(context, self.mock_client)

        # Verify that no labeling updates are triggered when CI is failing
        self.assertEqual(len(result.labels_to_add), 0)
        self.assertEqual(len(result.labels_to_remove), 0)
        self.assertTrue("Skipped: Core CI Checks failing/pending" in result.action_taken)

    def test_file_routing_rule_falls_back_to_needs_triage_if_no_match(self):
        """Verifies FileRoutingRule defaults standard non-matching files to status:needs-triage."""
        context = PRContext(
            pr_number=102,
            repo_name="Universal-Commerce-Protocol/ucp",
            title="docs: modify non-spec general text file",
            author="writer",
            is_draft=False,
            labels=set(),
            modified_files=["docs/general_guide.txt"], # No rule maps to docs/*
            reviews=[],
            ci_passed=True
        )

        rule = FileRoutingRule(self.mock_config)
        result = rule.evaluate(context, self.mock_client)

        self.assertTrue(Label.LABEL_NEEDS_TRIAGE in result.labels_to_add)
        self.assertEqual(len(result.labels_to_remove), 0)

    def test_file_routing_rule_clears_stale_when_approved(self):
        """Verifies FileRoutingRule maps approved labels once requirements are satisfied."""
        # Scenario: Changed schemas file, has tech-council majority label manually applied
        context = PRContext(
            pr_number=100,
            repo_name="Universal-Commerce-Protocol/ucp",
            title="feat: update core transaction schema",
            author="developer1",
            is_draft=False,
            labels={Label.LABEL_TC_MAJORITY_APPROVED},
            modified_files=["schemas/v1/transaction.json"],
            reviews=[],
            ci_passed=True # Enforce CI is passing
        )

        rule = FileRoutingRule(self.mock_config)
        result = rule.evaluate(context, self.mock_client)

        self.assertTrue("gov:tc-approved" in result.labels_to_add)
        self.assertTrue("gov:needs-tc-review" in result.labels_to_remove)

    def test_payments_reviewer_approval_multi_group_rules(self):
        """Verifies ReviewerApprovalRule maps custom domain-specific groups approvals."""
        # Scenario: Changed payments components, has 1 approval from payments-maintainers team member
        self.mock_client.check_team_membership.side_effect = lambda org, team, user: (
            team == "payments-maintainers" and user == "payments_expert"
        )

        context = PRContext(
            pr_number=105,
            repo_name="Universal-Commerce-Protocol/ucp",
            title="feat: add new stripe payment connector",
            author="payments_dev",
            is_draft=False,
            labels=set(),
            modified_files=["src/components/payments/stripe.py"],
            reviews=[
                ReviewInfo(user="payments_expert", state="APPROVED")
            ]
        )

        rule = ReviewerApprovalRule(self.mock_config)
        result = rule.evaluate(context, self.mock_client)

        self.assertTrue("status:payments-approved" in result.labels_to_add)
        self.assertTrue("status:review-needed-payments-maintainers" in result.labels_to_remove)
        self.assertTrue(result.satisfied)

    def test_payments_reviewer_approval_multiple_person_threshold(self):
        """Verifies ReviewerApprovalRule handles thresholds greater than 1 (e.g., 2 approvals)."""
        # Scenario: Changed payments components, requires 2 approvals from payments-maintainers team
        config_with_two_threshold = [
            {
                "name": "Payments Custom rules",
                "patterns": ["src/components/payments/**"],
                "review_requirements": {
                    "@Universal-Commerce-Protocol/payments-maintainers": {
                        "threshold": 2,
                        "needs_review_label": "status:review-needed-payments-maintainers",
                        "approved_label": "status:payments-approved"
                    }
                }
            }
        ]

        # Define mock team membership checking:
        # Let's make "payments_expert1" and "payments_expert2" be members, "someone_else" is not.
        self.mock_client.check_team_membership.side_effect = lambda org, team, user: (
            team == "payments-maintainers" and user in ["payments_expert1", "payments_expert2"]
        )

        # 1. Case A: Only 1 approval from a team member (unresolved)
        context_one_approval = PRContext(
            pr_number=106,
            repo_name="Universal-Commerce-Protocol/ucp",
            title="feat: Stripe updates",
            author="payments_dev",
            is_draft=False,
            labels=set(),
            modified_files=["src/components/payments/stripe.py"],
            reviews=[
                ReviewInfo(user="payments_expert1", state="APPROVED"),
                ReviewInfo(user="someone_else", state="APPROVED")
            ]
        )

        rule = ReviewerApprovalRule(config_with_two_threshold)
        result_one = rule.evaluate(context_one_approval, self.mock_client)

        self.assertFalse(result_one.satisfied)
        self.assertTrue("status:review-needed-payments-maintainers" in result_one.labels_to_add)
        self.assertTrue("status:payments-approved" in result_one.labels_to_remove)

        # 2. Case B: 2 approvals from team members (fully satisfied)
        context_two_approvals = PRContext(
            pr_number=106,
            repo_name="Universal-Commerce-Protocol/ucp",
            title="feat: Stripe updates",
            author="payments_dev",
            is_draft=False,
            labels=set(),
            modified_files=["src/components/payments/stripe.py"],
            reviews=[
                ReviewInfo(user="payments_expert1", state="APPROVED"),
                ReviewInfo(user="payments_expert2", state="APPROVED")
            ]
        )

        result_two = rule.evaluate(context_two_approvals, self.mock_client)

        self.assertTrue(result_two.satisfied)
        self.assertTrue("status:payments-approved" in result_two.labels_to_add)
        self.assertTrue("status:review-needed-payments-maintainers" in result_two.labels_to_remove)


    def test_amit_superpower_override_approval(self):
        """Verifies Amit superpower approval satisfies all approval thresholds instantly."""
        context = PRContext(
            pr_number=110,
            repo_name="Universal-Commerce-Protocol/ucp",
            title="feat: complete spec modifications",
            author="developer2",
            is_draft=False,
            labels={"gov:needs-tc-review"},
            modified_files=["schemas/v1/transaction.json"],
            reviews=[
                ReviewInfo(user="amithanda", state="APPROVED")
            ]
        )

        rule = ReviewerApprovalRule(self.mock_config)
        result = rule.evaluate(context, self.mock_client)

        # Ensure superpower cleared pending and applied approvals
        self.assertTrue("gov:needs-tc-review" in result.labels_to_remove)
        self.assertTrue("gov:tc-approved" in result.labels_to_add)
        self.assertTrue(Label.LABEL_READY_TO_MERGE in result.labels_to_add)
        self.assertTrue(result.satisfied)

    def test_unauthorized_needs_review_label_removal_guardrail(self):
        """Verifies ReviewerApprovalRule blocks unauthorized removal of active needs-review labels."""
        # Scenario: Changed schemas file (Core Spec), user 'unauthorized_tester' manually removed needs_review label
        # Target org/team handles: @Universal-Commerce-Protocol/tech-council
        self.mock_client.check_team_membership.return_value = False # User is not a member of TC or DevOps

        context_unlabel = PRContext(
            pr_number=111,
            repo_name="Universal-Commerce-Protocol/ucp",
            title="feat: corespec modifications",
            author="developer2",
            is_draft=False,
            labels=set(), # Set is empty because the user removed the label
            modified_files=["schemas/v1/transaction.json"],
            reviews=[],
            event_name="pull_request",
            event_payload={
                "action": "unlabeled",
                "sender": {"login": "unauthorized_tester"},
                "label": {"name": "gov:needs-tc-review"} # Removed label name
            }
        )

        rule = ReviewerApprovalRule(self.mock_config)
        result = rule.evaluate(context_unlabel, self.mock_client)

        # Assert that the needs_review label is dynamically re-applied
        self.assertTrue("gov:needs-tc-review" in result.labels_to_add)
        # Assert warning comment is prepared
        self.assertEqual(len(result.comments_to_create), 1)
        self.assertTrue("Warning: @unauthorized_tester, you do not have permission to remove `gov:needs-tc-review`." in result.comments_to_create[0])

    def test_label_lifecycle_blocked_resume(self):
        """Verifies LabelLifecycleRule handles blocked and resumed triggers."""
        rule = LabelLifecycleRule()

        # Scenario A: User manually added blocked label
        context_block = PRContext(
            pr_number=120,
            repo_name="Universal-Commerce-Protocol/ucp",
            title="feat: core updates",
            author="developer3",
            is_draft=False,
            labels={Label.LABEL_UNDER_REVIEW},
            event_name="pull_request",
            event_payload={"action": "labeled", "label": {"name": "blocked"}}
        )
        result_block = rule.evaluate(context_block, self.mock_client)
        self.assertTrue(Label.LABEL_UNDER_REVIEW in result_block.labels_to_remove)

        # Scenario B: Author comments on blocked PR (resolves block)
        context_comment = PRContext(
            pr_number=120,
            repo_name="Universal-Commerce-Protocol/ucp",
            title="feat: core updates",
            author="developer3",
            is_draft=False,
            labels={Label.LABEL_BLOCKED},
            event_name="issue_comment",
            event_payload={"action": "created", "comment": {"user": {"login": "developer3"}}}
        )
        result_comment = rule.evaluate(context_comment, self.mock_client)
        self.assertTrue(Label.LABEL_UNDER_REVIEW in result_comment.labels_to_add)
        self.assertTrue(Label.LABEL_BLOCKED in result_comment.labels_to_remove)

    def test_stale_pr_cron_inactivity_limits(self):
        """Verifies StalePRRule tracks review and blocked stale PRs correctly."""
        rule = StalePRRule(stale_threshold_days=30, abandon_threshold_days=37)
        
        # Scenario: Active under-review PR with no updates for 32 days (should become stale)
        utc_now = datetime.now(timezone.utc)
        inactive_updated_time = utc_now - timedelta(days=32)

        context_stale = PRContext(
            pr_number=130,
            repo_name="Universal-Commerce-Protocol/ucp",
            title="feat: stale review example",
            author="developer4",
            is_draft=False,
            labels={Label.LABEL_UNDER_REVIEW},
            updated_at=inactive_updated_time
        )
        result_stale = rule.evaluate(context_stale, self.mock_client)
        self.assertTrue(Label.LABEL_STALE_REVIEW in result_stale.labels_to_add)
        self.assertTrue(Label.LABEL_NEEDS_TRIAGE in result_stale.labels_to_add)

        # Scenario: Blocked PR with no updates for 39 days (should become abandon candidate)
        inactive_abandon_time = utc_now - timedelta(days=39)
        context_abandon = PRContext(
            pr_number=135,
            repo_name="Universal-Commerce-Protocol/ucp",
            title="feat: abandoned blocked example",
            author="developer4",
            is_draft=False,
            labels={Label.LABEL_BLOCKED},
            updated_at=inactive_abandon_time
        )
        result_abandon = rule.evaluate(context_abandon, self.mock_client)
        self.assertTrue(Label.LABEL_ABANDON_CANDIDATE in result_abandon.labels_to_add)

# ==============================================================================
# Rules Engine & Configurations Parser Tests
# ==============================================================================
class TestRulesEngineOrchestrator(unittest.TestCase):
    def setUp(self):
        self.mock_client = MagicMock(spec=GitHubAPIClient)
        self.engine = None

    @patch("triage.rules_engine.yaml.safe_load")
    @patch("triage.rules_engine.open", create=True)
    @patch("triage.rules_engine.os.path.exists")
    def test_rules_engine_loads_config_correctly(self, mock_exists, mock_open, mock_yaml_load):
        """Verifies rules engine successfully parses dynamic yaml configuration mappings."""
        from triage.rules_engine import RulesEngine
        mock_exists.return_value = True
        mock_yaml_load.return_value = {
            "routing_rules": [
                {"name": "Test Config Pattern", "patterns": ["*.txt"], "review_requirements": {}}
            ]
        }
        
        engine = RulesEngine(self.mock_client)
        config = engine.load_routing_config()
        
        self.assertEqual(len(config), 1)
        self.assertEqual(config[0]["name"], "Test Config Pattern")

    def test_rules_engine_orchestrates_rules_sequential(self):
        """Verifies RulesEngine runs registered rules sequentially and batches actions."""
        from triage.rules_engine import RulesEngine
        from triage.rules import BaseRule
        from triage.models import RuleResult

        # Mock a simple custom rule class
        mock_rule = MagicMock(spec=BaseRule)
        mock_rule.name = "Mock Custom Rule"
        mock_rule.evaluate.return_value = RuleResult(
            rule_name="Mock Custom Rule",
            satisfied=True,
            labels_to_add={"status:custom-add"},
            labels_to_remove={"status:custom-remove"}
        )

        context = PRContext(
            pr_number=200,
            repo_name="Universal-Commerce-Protocol/ucp",
            title="feat: test execution orchestrator",
            author="tester",
            is_draft=False,
            labels={"status:custom-remove"},
            modified_files=[],
            reviews=[]
        )

        engine = RulesEngine(self.mock_client)
        engine.add_rule(mock_rule)
        engine.run(context)

        # Assert rule evaluation was called
        mock_rule.evaluate.assert_called_once_with(context, self.mock_client)
        # Assert batched API updates triggered correctly
        self.mock_client.add_labels_to_pr.assert_called_once_with(200, {"status:custom-add"})
        self.mock_client.remove_labels_from_pr.assert_called_once_with(200, {"status:custom-remove"})


# Custom text runner to output pretty test execution logs summaries
class PrettyTextTestRunner(unittest.TextTestRunner):
    def run(self, test):
        print("\n======================================================================")
        print("                 PR TRIAGE RULES ENGINE UNIT TESTS RUN")
        print("======================================================================\n")
        result = super().run(test)
        print("\n======================================================================")
        print("                       TEST RUN SUMMARY RESULTS")
        print("======================================================================")
        print(f"Tests Executed:   {result.testsRun}")
        print(f"Passed:           {result.testsRun - len(result.failures) - len(result.errors)}")
        print(f"Failures:         {len(result.failures)}")
        print(f"Errors:           {len(result.errors)}")
        print("======================================================================\n")
        return result

if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestTriageRules)
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestRulesEngineOrchestrator))
    PrettyTextTestRunner(verbosity=2).run(suite)

