# -*- coding: utf-8 -*-
"""
triage/models.py
~~~~~~~~~~~~~~~~

This module defines standard dataclasses and strict string constants representing
PR lifecycle states, reviews, and the label taxonomy.

:copyright: (c) 2026 Universal Commerce Protocol.
:license: Apache-2.0, see LICENSE for more details.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Set, Dict, Any, Optional

# ==============================================================================
# Centralized Label Constants
# ==============================================================================
class Label:
    # Standard PR status labels
    LABEL_NEEDS_TRIAGE = "status:needs-triage"
    LABEL_UNDER_REVIEW = "status:under-review"
    LABEL_STALE_REVIEW = "status:stale-review"
    LABEL_BLOCKED = "blocked"
    LABEL_READY_TO_MERGE = "status:ready-to-merge"
    LABEL_STALE = "status:stale"
    LABEL_MERGED = "status:merged"
    LABEL_ABANDON_CANDIDATE = "status:abandon-candidate"

    # Governance status labels
    LABEL_NEEDS_TC_REVIEW = "gov:needs-tc-review"
    LABEL_TC_APPROVED = "gov:tc-approved"
    LABEL_NEEDS_GC_REVIEW = "gov:needs-gc-review"
    LABEL_GC_APPROVED = "gov:gc-approved"
    LABEL_APPROVED = "gov:approved"

    # Specialized trigger labels
    LABEL_TC_MAJORITY_APPROVED = "status:tc-majority-approved"

# ==============================================================================
# PR Core Context Dataclasses
# ==============================================================================
@dataclass
class ReviewInfo:
    user: str
    state: str  # APPROVED, CHANGES_REQUESTED, COMMENTED, etc.
    submitted_at: Optional[datetime] = None

@dataclass
class PRContext:
    pr_number: int
    repo_name: str
    title: str
    author: str
    is_draft: bool
    labels: Set[str] = field(default_factory=set)
    modified_files: List[str] = field(default_factory=list)
    reviews: List[ReviewInfo] = field(default_factory=list)
    ci_passed: bool = False # Dynamic CI checks status (Lint, Unit tests, CLA) - Defaulting strictly to False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    event_name: str = ""
    event_payload: Dict[str, Any] = field(default_factory=dict)

@dataclass
class RuleResult:
    rule_name: str
    satisfied: bool
    labels_to_add: Set[str] = field(default_factory=set)
    labels_to_remove: Set[str] = field(default_factory=set)
    comments_to_create: List[str] = field(default_factory=list)
    action_taken: str = "No action required"
