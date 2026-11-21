#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Conservative, auditable feature-conditioning policy.

AI assessments are evidence, not authority.  Only deterministic, narrowly
scoped optional exclusions may be written automatically.  Existing engineer
choices, Fixes obligations, readiness issues and unresolved consumers are
never silently overridden.  All inputs from model/commit messages are data.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time

from feature_assessor import assessment_cache_key, feature_metadata, _digest

SCHEMA = 1
POLICY = "optional-exclude-guarded-v1"
SAFETY_WORDS = re.compile(
    r"\b(?:regression|security|cve|vulnerability|panic|"
    r"crash|deadlock|corruption|race|use.after.free|errata|workaround|"
    r"overflow|underflow|fault|incorrect|broken|bug)\b", re.I)


def _single_line(text: object, limit: int = 650) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()[:limit]


def history_path(directory: Path) -> Path:
    return directory / ".kernel-backport" / "conditioning-history.json"


def read_conditioning_history(directory: Path, plan_digest: str) -> dict:
    path = history_path(directory)
    if path.is_symlink():
        raise ValueError("refusing symlink conditioning history")
    if not path.exists():
        return {"schema": SCHEMA, "policy": POLICY,
                "source_plan_sha256": plan_digest, "events": []}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise ValueError(f"invalid conditioning history: {exc}") from exc
    if (not isinstance(doc, dict) or doc.get("schema") != SCHEMA or
            doc.get("source_plan_sha256") != plan_digest or
            not isinstance(doc.get("events"), list)):
        raise ValueError("conditioning history not bound to frozen planner source")
    return doc


def _atomic_private(path: Path, doc: dict) -> None:
    if path.is_symlink():
        raise ValueError(f"refusing symlink {path}")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".condition-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(doc, stream, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


class RequestCounter:
    """Hard *request count* ceiling; report real API token usage if provided.

    Pricing is deliberately not guessed.  A successful call can exceed its
    eventual token count, so this is not represented as a dollar cost ceiling.
    """
    def __init__(self, limit: int = 0):
        if limit < 0:
            raise ValueError("--max-api-requests must be >= 0")
        self.limit = limit
        self.attempts = 0
        self.usage: list[dict] = []

    def before_request(self, model: str) -> None:
        if self.limit and self.attempts >= self.limit:
            raise ValueError(f"API request limit {self.limit} reached; "
                             "cached assessments preserved, rerun with a higher limit")
        self.attempts += 1

    def on_usage(self, model: str, usage: dict) -> None:
        if isinstance(usage, dict):
            self.usage.append({"model": model,
                               "input_tokens": usage.get("input_tokens", 0),
                               "output_tokens": usage.get("output_tokens", 0),
                               "total_tokens": usage.get("total_tokens", 0)})

    def summary(self) -> dict:
        return {"attempted_api_requests": self.attempts,
                "requests_with_reported_usage": len(self.usage),
                "input_tokens_reported": sum(int(x.get("input_tokens") or 0) for x in self.usage),
                "output_tokens_reported": sum(int(x.get("output_tokens") or 0) for x in self.usage),
                "usage_by_model": self.usage}


def _later_fixes(plan) -> dict[str, set[str]]:
    fixes: dict[str, set[str]] = {}
    for member, rec in {**plan.base_commits, **plan.commits}.items():
        for original in rec.get("fixes") or []:
            fixes.setdefault(original, set()).add(member)
    return fixes


def _consumers(plan) -> dict[str, set[str]]:
    consumers: dict[str, set[str]] = {}
    for consumer in plan.order:
        if plan.effective(consumer) not in ("TAKE", "REVIEW"):
            continue
        record = plan.commits[consumer]
        for edge in record.get("advisor_prerequisites") or []:
            sha = edge.get("sha") if isinstance(edge, dict) else None
            if isinstance(sha, str):
                consumers.setdefault(sha, set()).add(consumer)
        # The planner's required_by is provenance rather than proof, but it
        # still marks an unresolved possible consumer: fail closed before
        # excluding the provider, particularly for REVIEW disagreements.
        for user in record.get("required_by") or []:
            if isinstance(user, str) and user in plan.commits and user != consumer:
                if plan.effective(user) in ("TAKE", "REVIEW"):
                    consumers.setdefault(consumer, set()).add(user)
    return consumers


def recommend_conditioning(plan, groups: list[dict], report: dict, icc: dict,
                           *, model: str, summary_model: str,
                           auto_disagreements: bool = False) -> dict:
    """Produce safe AUTO-SKIP candidates and explicit exceptions, never mutate.

    Unmodified planner TAKEs and explicitly opted-in disputed REVIEWs can be
    excluded. REVIEWs must independently meet a stricter, high-confidence
    evidence gate. Retained consumers and all Fixes obligations fail closed.
    """
    later = _later_fixes(plan)
    consumers = _consumers(plan)
    candidates: dict[str, dict] = {}
    exceptions: dict[str, dict] = {}
    assessments = 0
    feature_decisions = []
    for group in groups:
        result = group.get("assessment")
        if not isinstance(result, dict) or group.get("assessment_status") != "assessed":
            invalid = group.get("assessment_status") == "invalid"
            label = "INVALID" if invalid else "UNASSESSED"
            reason = (("Invalid model assessment: " + str(group.get("assessment_error") or
                       "failed semantic validation") + "; use --retry-invalid to reassess")
                      if invalid else "No current validated feature assessment")
            feature_decisions.append({"id": group["id"], "title": group["title"],
                                      "assessment_status": label,
                                      "validation_error": group.get("assessment_error") if invalid else None,
                                      "members": group["members"]})
            for member in group["members"]:
                exceptions[member] = {"sha": member, "feature_id": group["id"],
                                      "status": label, "why": reason}
            continue
        assessments += 1
        feature_decisions.append({
            "id": group["id"], "title": group["title"],
            "assessment_status": "ASSESSED", "members": group["members"],
            "capability": result.get("capability"),
            "selection_decision": result.get("selection_decision"),
            "feature_value": result.get("backport_value"),
            "confidence": result.get("confidence"),
            "rationale": result.get("feature_rationale"),
            "inclusion_basis": result.get("inclusion_basis"),
            "exclusion_impact": result.get("exclusion_impact"),
            "next_action": result.get("next_action"),
            "member_recommendations": result.get("member_recommendations")})
        recs = {m["sha"]: m for m in result["member_recommendations"]}
        roles = {m["sha"]: m["role"] for m in result["member_roles"]}
        metadata = feature_metadata(plan, group, report, icc)
        evidence_digest = _digest(metadata)
        for member in group["members"]:
            rec = plan.commits[member]
            action = recs[member]["recommendation"]
            role = roles[member]
            reason = recs[member]["reason"]
            hazard = bool(SAFETY_WORDS.search(rec.get("subject") or ""))
            # The patch can carry a Fixes tag regardless of reviewer role.
            fixes = bool(rec.get("fixes") or later.get(member))
            explicit = member in plan.human_overrides or member in plan.auto_overrides
            escalation = rec.get("advisor_escalation_status")
            escalated = escalation in ("disagree", "error", "budget-deferred")
            # This is a deliberately *narrow* exception to v0.1.32's
            # mandatory-human disagreement gate. It requires an independently
            # cached feature-level Sol EXCLUDE for a manifestly optional
            # member of a LOW-value feature. Error/budget cases, full CORE
            # features and correctness fixes still need the engineer.
            auto_adjudicate = bool(
                auto_disagreements and escalation == "disagree"
                and model == "gpt-6.1-sol"
                and result.get("selection_decision") == "EXCLUDE"
                and result.get("backport_value") == "LOW"
                and role in ("OPTIONAL", "CLEANUP"))
            # A REVIEW was never approved as a TAKE. It is eligible for
            # guarded automatic disposition only when a dedicated dispute
            # dossier validates a high-confidence, no-further-verification
            # low-value exclusion. Older TAKE policy is unchanged.
            is_review_disagreement = plan.effective(member) == "REVIEW"
            if is_review_disagreement:
                auto_adjudicate = bool(
                    auto_adjudicate and group.get("assessment_kind") == "advisor-disagreement"
                    and result.get("confidence") == "high"
                    and result.get("needs_verification") is False)
            optional_role = role in ("OPTIONAL", "CLEANUP")
            optional_core = (role in ("CORE", "PREREQUISITE") and
                             result.get("selection_decision") == "EXCLUDE" and
                             result.get("backport_value") == "LOW")
            decision = plan.effective(member)
            decision_allowed = (decision == "TAKE" or
                                (decision == "REVIEW" and auto_adjudicate))
            allowed = (decision_allowed and not explicit
                       and action == "EXCLUDE" and not fixes
                       and (not escalated or auto_adjudicate)
                       and not hazard and (optional_role or optional_core))
            if allowed:
                candidates[member] = {
                    "sha": member, "feature_id": group["id"],
                    "feature_title": group["title"], "model": model,
                    "summary_model": summary_model,
                    "feature_selection": result.get("selection_decision"),
                    "feature_value": result.get("backport_value"),
                    "member_role": role,
                    "reason": _single_line(reason),
                    "evidence_sha256": evidence_digest,
                    "cache_key": assessment_cache_key(group["id"], model, summary_model),
                    "status": "AUTO-SKIP",
                    "prior_decision": decision,
                    "advisor_disagreement_resolution": auto_adjudicate}
            else:
                labels = []
                if explicit: labels.append("existing engineer/automatic decision")
                if fixes: labels.append("Fixes relationship requires independent applicability review")
                if hazard: labels.append("possible correctness/security/erratum")
                if escalated: labels.append("unresolved advisor escalation")
                if is_review_disagreement and not auto_adjudicate:
                    labels.append("REVIEW disagreement needs high-confidence dedicated assessment and no outstanding verification")
                if action != "EXCLUDE": labels.append("reviewer recommends " + action)
                if not (optional_role or optional_core): labels.append("not proven optional")
                exceptions[member] = {"sha": member, "feature_id": group["id"],
                                      "status": "KEEP/ESCALATE", "role": role,
                                      "why": "; ".join(labels) or _single_line(reason)}
    # Fixed point: SKIP a provider only if all selected users are also excluded.
    while True:
        blocked = []
        for member in candidates:
            retained = sorted(c for c in consumers.get(member, set())
                              if c != member and c not in candidates)
            if retained:
                blocked.append((member, retained))
        if not blocked:
            break
        for member, retained in blocked:
            record = candidates.pop(member)
            exceptions[member] = {"sha": member, "feature_id": record["feature_id"],
                                  "status": "KEEP/ESCALATE",
                                  "why": "Selected claimed prerequisite consumer(s): " +
                                         ", ".join(s[:12] for s in retained)}
    return {"schema": SCHEMA, "policy": POLICY,
            "decision_revision": plan.revision(),
            "plan_sha256": plan.plan_digest,
            "frozen_downstream_baseline": plan.baseline,
            "model": model, "summary_model": summary_model,
            "assessed_groups": assessments, "groups_considered": len(groups),
            "disagreement_groups_considered": sum(
                g.get("assessment_kind") == "advisor-disagreement" for g in groups),
            "unresolved_obligations": {
                "logical_blockers": icc.get("logical_blockers", []),
                "advisor_readiness_blockers": icc.get("advisor_readiness_blockers", []),
            },
            "feature_decisions": feature_decisions,
            "proposed_skips": [candidates[s] for s in plan.order if s in candidates],
            "exceptions": [exceptions[s] for s in plan.order if s in exceptions]}


def audit_conditioning(plan, proposal: dict, *, applied: bool) -> None:
    """Write an append-only action record, including all exceptions and evidence.

    Record before worksheet mutation.  If the process exits after the record
    but before the worksheet update, the worksheet remains the source of truth
    and the journal event is detectable as an uncommitted intent.
    """
    hist = read_conditioning_history(plan.directory, plan.plan_digest)
    event = {"when_unix": int(time.time()), "action": "PROPOSAL",
             "requested_application": applied,
             "policy": POLICY, "revision_before": plan.revision(),
             "baseline": plan.baseline, "model": proposal["model"],
             "summary_model": proposal["summary_model"],
             "proposed_skips": proposal["proposed_skips"],
             "unresolved_obligations": proposal.get("unresolved_obligations", {}),
             "exceptions": proposal["exceptions"],
             "feature_decisions": proposal.get("feature_decisions", [])}
    serialized = json.dumps(event, sort_keys=True, separators=(",", ":"))
    event["event_sha256"] = hashlib.sha256(serialized.encode()).hexdigest()
    hist["events"].append(event)
    _atomic_private(history_path(plan.directory), hist)


def audit_conditioning_result(directory: Path, plan_digest: str, *,
                              revision_before: str, revision_after: str,
                              skipped: list[str], success: bool) -> None:
    hist = read_conditioning_history(directory, plan_digest)
    event = {"when_unix": int(time.time()),
             "action": "COMMITTED_AUTO_SKIPS" if success else "ROLLED_BACK_AUTO_SKIPS",
             "revision_before": revision_before,
             "revision_after": revision_after,
             "commits": skipped}
    event["event_sha256"] = hashlib.sha256(
        json.dumps(event, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    hist["events"].append(event)
    _atomic_private(history_path(directory), hist)


def audit_manual_decision(directory: Path, plan_digest: str, *,
                          commit: str, before: str, after: str, reason: str,
                          revision_before: str, revision_after: str) -> None:
    """Make reviewer overrides durable without altering earlier audit events."""
    hist = read_conditioning_history(directory, plan_digest)
    event = {"when_unix": int(time.time()), "action": "ENGINEER_DECISION",
             "sha": commit, "before": before, "after": after,
             "reason": reason or "", "revision_before": revision_before,
             "revision_after": revision_after}
    event["event_sha256"] = hashlib.sha256(
        json.dumps(event, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    hist["events"].append(event)
    _atomic_private(history_path(directory), hist)


def write_conditioning_report(directory: Path, proposal: dict, *, applied: int,
                              budget: RequestCounter | None = None) -> None:
    payload = dict(proposal)
    payload["auto_skips_applied_this_run"] = applied
    payload["api_usage"] = budget.summary() if budget is not None else RequestCounter().summary()
    _atomic_private(directory / "conditioning-decisions.json", payload)
    from text_output import wrap_text
    text = wrap_text(conditioning_text(payload)) + "\n"
    path = directory / "conditioning-decisions.txt"
    if path.is_symlink():
        raise ValueError("refusing symlink conditioning text report")
    fd, temp = tempfile.mkstemp(dir=directory, prefix=".condition-text-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            os.fchmod(f.fileno(), 0o600)
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def conditioning_text(proposal: dict) -> str:
    lines = ["FEATURE CONDITIONING", "=" * 78,
             "Policy: " + proposal.get("policy", POLICY),
             "Frozen baseline: " + proposal.get("frozen_downstream_baseline", "unknown"),
             "Decision revision: " + proposal.get("decision_revision", "unknown")[:12],
             "Reviewer: " + proposal.get("model", "unknown") +
             "; patch summaries: " + proposal.get("summary_model", "unknown"),
             "Feature groups: " + str(proposal.get("groups_considered", 0)),
             "Assessed groups: " + str(proposal.get("assessed_groups", 0)),
             "Disputed REVIEW groups: " + str(proposal.get("disagreement_groups_considered", 0)),
             "Proposed automatic SKIPs: " + str(len(proposal.get("proposed_skips", []))),
             "Applied SKIPs in this run: " + str(proposal.get("auto_skips_applied_this_run", 0)),
             "Disagreements proposed for guarded automatic SKIP: " + str(
                 sum(bool(x.get("advisor_disagreement_resolution")) for x in
                     proposal.get("proposed_skips", []))),
             "Engineer exceptions: " + str(len(proposal.get("exceptions", []))), ""]
    usage = proposal.get("api_usage", {})
    lines.append("API attempts: " + str(usage.get("attempted_api_requests", 0)) +
                 "; reported input tokens: " + str(usage.get("input_tokens_reported", 0)) +
                 "; reported output tokens: " + str(usage.get("output_tokens_reported", 0)))
    unresolved = proposal.get("unresolved_obligations", {})
    logical = unresolved.get("logical_blockers", [])
    advisor = unresolved.get("advisor_readiness_blockers", [])
    if logical or advisor:
        lines += ["", "UNRESOLVED EXECUTION OBLIGATIONS (NOT WAIVED)", "-" * 78,
                  "Logical blockers: " + str(len(logical)),
                  "Advisor readiness blockers: " + str(len(advisor))]
        for item in advisor:
            lines.append("  " + _single_line(item.get("kind", "UNKNOWN") + " " +
                                            str(item.get("sha", ""))[:12] + " " +
                                            str(item.get("reason", ""))))
        for item in logical:
            lines.append("  " + _single_line(json.dumps(item, sort_keys=True)))
    lines += ["", "FEATURE-LEVEL DECISIONS", "-" * 78]
    for entry in proposal.get("feature_decisions", []):
        lines.append(entry["id"] + " " + entry["title"] + " [" +
                     entry.get("assessment_status", "unknown") + "]")
        if entry.get("assessment_status") == "ASSESSED":
            lines.append("  selection: " + str(entry.get("selection_decision")) +
                         "; value: " + str(entry.get("feature_value")) +
                         "; confidence: " + str(entry.get("confidence")))
            lines.append("  rationale: " + str(entry.get("rationale")))
            lines.append("  inclusion basis: " + str(entry.get("inclusion_basis")))
            lines.append("  exclusion impact: " + str(entry.get("exclusion_impact")))
            lines.append("  next action: " + str(entry.get("next_action")))
    lines += ["", "AUTOMATIC SKIP PROPOSALS", "-" * 78]
    for row in proposal.get("proposed_skips", []):
        lines.append(row["sha"][:12] + " " + row["feature_id"] +
                     " [" + row.get("member_role", "?") + "]")
        lines.append("  rationale: " + row["reason"])
        if row.get("advisor_disagreement_resolution"):
            lines.append("  disposition: AUTO-ADJUDICATED advisor disagreement "
                         "(prior " + row.get("prior_decision", "UNKNOWN") +
                         "; not engineer approval)")
        lines.append("  evidence: " + row["evidence_sha256"])
    lines += ["", "RETAINED / ENGINEER REVIEW", "-" * 78]
    for row in proposal.get("exceptions", []):
        lines.append(row["sha"][:12] + " " + row["feature_id"] +
                     " " + row["status"])
        lines.append("  why: " + row["why"])
    return "\n".join(lines) + "\n"
