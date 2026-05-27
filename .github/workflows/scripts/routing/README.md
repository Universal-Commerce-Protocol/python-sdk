# UCP PR Triage & Review Routing Automation

This directory houses the modular, configuration-driven Python rules engine designed to automate pull request ingestion triage, blocked/resume lifecycles, dynamically scoped organizational reviews verification, and inactivity stale-PR scans.

---

## Directory Directory Tree Layout

```
scripts/routing/
├── UCP_PR_REVIEW_ROUTING.yml    # Centralized rules configuration mapping
├── pr-triage-automation.py      # Real-time webhook trigger runner (run by GHA workflow)
├── pr-cron-stale-abandon.py     # Daily stale/abandon inactivity scan runner (run by cron GHA workflow)
├── validate-routing.py          # Pretty CLI validation tool checking YAML syntax and org group existence
├── test_routing.py              # Pretty unit test suite executing mocked verifications locally
└── triage/
    ├── github_api.py            # Encapsulates dynamic PyGithub API calls and organization caching
    ├── models.py                # Standard shared dataclasses and strict LABEL_ Constants
    └── rules.py                 # Core abstract BaseRule class and concrete check implementations
```

---

## 1. Centralized Review Routing Configuration

All folder-to-reviewer-mappings and label states are decoupled completely from the execution codebase and stored in [**`UCP_PR_REVIEW_ROUTING.yml`**](./UCP_PR_REVIEW_ROUTING.yml). This allows developers to flexibly configure or update rules without editing Python modules.

### Configuration Rule Structure:
```yaml
routing_rules:
  - name: "Core Protocol & Spec"
    patterns:
      - "schemas/**/*.json"
      - "spec/**/*.md"
    review_requirements:
      # Maps fully qualified GitHub team handles to approval thresholds and status labels:
      "@Universal-Commerce-Protocol/tech-council":
        threshold: "majority" # Require TC majority approval or status:tc-majority-approved
        needs_review_label: "gov:needs-tc-review"
        approved_label: "gov:tc-approved"
      "@Universal-Commerce-Protocol/maintainers":
        threshold: 1
        needs_review_label: "status:review-needed-maintainers"
        approved_label: "gov:maintainer-approved"
```

* **`patterns`**: List of path glob filters determining if modifications in the PR match this rule.
* **`review_requirements`**: Maps dynamic, fully qualified organization team handles to:
  * `threshold`: Integer count (e.g. `1`, `2`) or `"majority"`.
  * `needs_review_label`: Staging label applied when approvals are below the threshold.
  * `approved_label`: Calming tint approval label applied dynamically once approvals are satisfied.

---

## 2. Core Rules Scaffolding (`triage/rules.py`)

Triage logic is organized into specialized rule subclasses inheriting from `BaseRule`:

1. **`FileRoutingRule`**: Compiles modified files against the configuration and dynamically applies the corresponding `needs_review_label` and removes the `approved_label`.
2. **`ReviewerApprovalRule`**: Tracks active `pygithub` approvals against the resolved organization team members list:
   * **Superpower Override**: If a designated superpower user (like Amit `amithanda`) approves, all TC and GC rules are satisfied instantly, transitioning the PR to `gov:approved` / `status:ready-to-merge`.
   * **Label Security Guardrail**: Restricts `gov:tc-approved` and `status:tc-majority-approved` application. If applied by an unauthorized user outside Tech Council or DevOps, the script revokes the label with an automated warning comment.
   * **SDK Relaxed Mode**: Repositories matching `sdk` or `meeting-minutes` automatically default team thresholds to `1` to expedite SDK review cycles.
3. **`LabelLifecycleRule`**: Resolves blocked feedback loops. Clears `Label.LABEL_BLOCKED` and restores `Label.LABEL_UNDER_REVIEW` when the author pushes a new commit or comments on the PR.
4. **`StalePRRule`**: Scans active timestamps:
   * Under-review PRs inactive for 30 days are labeled `status:stale-review` and `status:needs-triage`.
   * Blocked PRs inactive for 37 days are labeled `status:abandon-candidate`.

---

## 3. Local Dry-Run & Validation Utilities

### YAML and Taxonomy Validation:
Developers making changes to `UCP_PR_REVIEW_ROUTING.yml` can test and validate their configurations locally using:
```bash
export GH_TOKEN="your_github_personal_access_token"
uv run .github/workflows/scripts/routing/validate-routing.py
```
This utility checks:
1. **YAML Syntax**: Verifies structure correctness.
2. **Taxonomy Matcher**: Cross-references labels with `.github/labels.yml` to prevent styling typos.
3. **Dynamic Org Team Check**: Dynamically calls the API to verify that all configured dynamic handles actually exist in the active organization (gracefully skipped with a warning on local forks).

### Triage dry-runs:
You can evaluate the rules engine output on any PR locally without committing live updates to GitHub by appending the `--dry-run` option flag:
```bash
export GH_TOKEN="your_token"
uv run .github/workflows/scripts/routing/pr-triage-automation.py --dry-run
```

---

## 4. Local Unit Testing

The rules engine is backed by a mock-based unit test suite verifying all edge conditions. Execute tests locally using:
```bash
export GH_TOKEN="your_token"
uv run .github/workflows/scripts/routing/test_routing.py
```
This outputs high-visibility boxed **Test Run Summary Results** with counts of executed, passed, failed, and errored test cases.
