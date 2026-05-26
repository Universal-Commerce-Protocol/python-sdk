# models.py
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Set, Dict, Any, Optional

# ==============================================================================
# Centralized Label Constants
# ==============================================================================
class Label:
    # Standard PR status labels
    NEEDS_TRIAGE = "status:needs-triage"
    UNDER_REVIEW = "status:under-review"
    STALE_REVIEW = "status:stale-review"
    BLOCKED = "blocked"
    READY_TO_MERGE = "status:ready-to-merge"
    STALE = "status:stale"
    MERGED = "status:merged"
    ABANDON_CANDIDATE = "status:abandon-candidate"

    # Governance status labels
    NEEDS_TC_REVIEW = "gov:needs-tc-review"
    TC_APPROVED = "gov:tc-approved"
    NEEDS_GC_REVIEW = "gov:needs-gc-review"
    GC_APPROVED = "gov:gc-approved"
    APPROVED = "gov:approved"

    # Specialized trigger labels
    TC_MAJORITY_APPROVED = "status:tc-majority-approved"

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
