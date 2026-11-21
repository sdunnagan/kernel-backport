#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Derived P0-P3 engineer review priorities; decisions and worksheets are immutable.

The planner retains REVIEW for unmatched hardware. The engineer's *attention*
queue is a separate, recomputable view of the immutable plan plus overrides.
"""
from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
import re

from text_output import wrap_text

PRIORITY_NAMES = ("p0", "p1", "p2", "p3")
TRIAGE_NAMES = ("REQUIRED", "INTEGRATION", "OPTIONAL", "UNCLASSIFIED")
# Narrow signals only. A generic "cleanup" or "fix" title never suffices to
# declare an upstream change expendable, particularly in shared kernel code.
_STYLE_ONLY = re.compile(r"(?i)\b(?:lowercase hex|whitespace only|"
                         r"comment[- ]only|spelling typo|fix typo)\b")
PRIORITY_LABELS = {
    "p0": "P0 CRITICAL", "p1": "P1 HIGH",
    "p2": "P2 NORMAL", "p3": "P3 DEFERRED",
}
# Backward-compatible programmatic symbol is intentionally not kept: the
# published schema is now priority-based, not three unrelated queues.
QUEUE_NAMES = PRIORITY_NAMES
_DT_SOURCE = re.compile(r"^arch/[^/]+/boot/dts/.+\.(?:dts|dtsi|dtso)$")
_DT_MAKEFILE = re.compile(r"^arch/[^/]+/boot/dts/.+/Makefile$")

# Strict hardware identity recognition for generated DT bindings, per-SoC
# constants and configuration-only changes. A directory such as include/ or
# Documentation/ is not automatically common *functionality*. Conversely,
# modifying a generic binding schema or shared driver is NOT hardware-only.
# These are intentionally conservative positive patterns, not all SoC names.
_SOC_ID = re.compile(
    r"(?i)(?<![a-z0-9])(?:"
    r"qcs[0-9]{3,5}|qcm[0-9]{3,5}|sm[0-9]{4}|sdm[0-9]{3,4}|"
    r"msm[0-9]{4}|mt[0-9]{4,5}|imx[0-9]{2,4}[a-z]?|"
    r"r[89]a[a-z0-9]{5,7}|rk[0-9]{4}|tegra[0-9]{3}|"
    r"bcm[0-9]{4}|gs[0-9]{3}|exynos[0-9]{4}|"
    r"sa[0-9]{4,5}p?|x1e[0-9]{5}|sky[0-9]|"
    r"kaanapali|milos|glymur|hamoa|lemans|"
    r"rz/g[0-9][a-z0-9]*"
    r")(?![a-z0-9])"
)
_SOC_HEADER = re.compile(r"^(?:include/dt-bindings/[^/]+/|"
                         r"arch/[^/]+/boot/dts/[^/]+/)[^/]+\.h$")
_SOC_SCHEMA = re.compile(r"^Documentation/devicetree/bindings/.+\.yaml$")
_SOC_DTS = re.compile(r"^arch/[^/]+/boot/dts/.+\.(?:dts|dtsi|dtso)$")
_SOC_CONFIG = re.compile(r"^arch/[^/]+/configs/[^/]*defconfig$")
_DT_ROOT_MAKEFILE = re.compile(r"^arch/[^/]+/boot/dts/Makefile$")


def soc_identities(text: str) -> set[str]:
    """Identifiers, not substring guesses (QCS615 != QCS8300)."""
    return {m.group(0).lower().replace("/", "") for m in _SOC_ID.finditer(text)}


def hardware_only_identities(record: dict) -> set[str]:
    """Recognize edits limited to identifiable SoC hardware definitions.

    All substantive changed paths must contain a *specific* SoC identity;
    generic binding schemas, common clock drivers, Kconfig infrastructure,
    and shared source are deliberately left for engineering review. A
    defconfig-only edit is eligible only when its subject names a SoC.
    """
    files = record.get("files") or []
    substantive = [f for f in files if not (_DT_MAKEFILE.fullmatch(f) or
                                            _DT_ROOT_MAKEFILE.fullmatch(f))]
    if not substantive:
        return set()
    if all(_SOC_CONFIG.fullmatch(f) for f in substantive):
        return soc_identities(record.get("subject") or "")
    identities = set()
    for f in substantive:
        if not (_SOC_HEADER.fullmatch(f) or _SOC_SCHEMA.fullmatch(f) or
                _SOC_DTS.fullmatch(f)):
            return set()
        tokens = soc_identities(f.rsplit("/", 1)[-1])
        if not tokens:
            return set()
        identities.update(tokens)
    return identities


def _selected_hardware_identities(records: dict, profile: dict | None = None) -> set[str]:
    """Conservative protection for IDs explicitly matched to active scope.

    The plan stores evidence on individual commits, not the full platform
    YAML. A positive match anywhere in the plan means its precise SoC identity
    should not be automatically deferred just because another record lacks a
    direct match. Never infer a match from a generic CPU/vendor family name.
    """
    active = set()
    # New plans embed only the active positive platform definitions. This is
    # better evidence than inferring support from an unrelated mixed commit.
    for entry in (profile or {}).get("selected_platforms") or []:
        if not isinstance(entry, dict):
            continue
        active.update(soc_identities(str(entry.get("name") or "")))
        for path in entry.get("paths") or []:
            active.update(soc_identities(str(path).rsplit("/", 1)[-1]))
        for compatible in (entry.get("identifiers") or {}).get("dt_compatible") or []:
            active.update(soc_identities(str(compatible)))
    # Old plans have no selected-platform snapshot; the direct evidence is
    # still useful, but only matched evidence, never all changed file paths.
    for record in records.values():
        rel = record.get("platform_relevance") or {}
        for match in rel.get("matches") or []:
            if not isinstance(match, dict):
                continue
            for key in ("platform", "evidence"):
                active.update(soc_identities(str(match.get(key) or "")))
    return active


def is_dt_only(files: list[str]) -> bool:
    """Only changed DT content counts. An unchanged include is not a change.

    The shared DTS Makefile is *auxiliary*, not sufficient alone to establish
    board scope or architectural relevance. Files outside DTS are not ignored.
    """
    substantive = [p for p in files if not _DT_MAKEFILE.fullmatch(p)]
    return bool(substantive) and all(_DT_SOURCE.fullmatch(p) for p in substantive)


def is_deferable_hardware(record: dict, *, active_soc_ids: set[str] | None = None) -> bool:
    rel = record.get("platform_relevance") or {}
    files = record.get("files") or []
    if rel.get("matches") or rel.get("scope_conflict"):
        return False
    identities = hardware_only_identities(record)
    if identities.intersection(active_soc_ids or set()):
        # A selected SoC must not be deferred just because this particular
        # board/header has no direct path match in the old plan's metadata.
        return False
    if rel.get("status") in ("UNMATCHED_BOARD", "UNMATCHED_SOC") and is_dt_only(files):
        return True
    return bool(identities)


def _missing(record: dict) -> bool:
    match = record.get("match") or {}
    return (match.get("status") != "present"
            or match.get("revert_status") in ("reverted", "ambiguous"))


def _fixes_sources(commits: dict) -> dict[str, set[str]]:
    children = defaultdict(set)
    for fix, rec in commits.items():
        for original in rec.get("fixes") or []:
            if original in commits:
                children[original].add(fix)
    return children


def _fresh_icc(directory: Path, plan_sha: str, decision_revision: str) -> dict | None:
    path = directory / ".kernel-backport" / "icc.json"
    if not path.is_file() or path.is_symlink():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (doc.get("plan_sha256") != plan_sha
            or doc.get("decision_revision") != decision_revision):
        return None
    return doc


_HIGH_IMPACT = re.compile(
    r"(?i)\b(?:fix(?:es|ed)?|regression|security|cve-\d+|"
    r"use.after.free|out.of.bounds|overflow|corruption|uninitialized|"
    r"crash|deadlock|data.race|kexec|hibernation)\b"
)


def _engineering_priority(record: dict) -> tuple[str, str, str]:
    """Evidence-backed prioritization, not an additional TAKE/SKIP decision.

    Terra can supply HIGH/NORMAL when it has actually inspected the patch.
    Historical plans without that field use intentionally conservative signals;
    no title matching is a substitute for inspection of the actual patch.
    """
    rel = record.get("platform_relevance") or {}
    if record.get("advisor_error_kind"):
        return ("p1", "Advisor evaluation failed; engineer needs to inspect the patch",
                "deterministic:advisor-error")
    if rel.get("scope_conflict"):
        return ("p1", "Conflicting positive and negative platform scope evidence",
                "deterministic:scope-conflict")
    if rel.get("matches"):
        return ("p1", "Positive selected-platform match; verify relevance before backporting",
                "deterministic:platform-match")
    if record.get("dependency_kind") == "dependency-candidate":
        return ("p1", "Potential prerequisite; verify actual TAKE-consumer requirements",
                "deterministic:dependency-candidate")
    if record.get("fixes"):
        return ("p1", "Upstream Fixes: metadata; verify affected code is present",
                "deterministic:historical-fix")
    model = record.get("advisor_review_priority")
    reason = str(record.get("advisor_review_priority_reason") or "").strip()
    if model in ("HIGH", "NORMAL") and reason:
        label = "p1" if model == "HIGH" else "p2"
        return (label, "Terra: " + reason, "advisor:" + str(record.get("advisor_decision_model") or "model"))
    if _HIGH_IMPACT.search(str(record.get("subject") or "")):
        return ("p1", "Possible correctness/security impact inferred from commit subject; verify patch",
                "fallback:subject-signal")
    return ("p2", "Shared or mixed code without verified high-priority evidence",
            "fallback:source-scope")


def build_queues(payload: dict, overrides: dict | None = None,
                 extra_commits: dict | None = None, order: list[str] | None = None,
                 *, icc: dict | None = None,
                 plan_sha256: str = "", decision_revision: str = "") -> dict:
    """Compute derived review priorities; never alter TAKE/REVIEW/SKIP.

    P0 is reserved for known consistency obligations, P3 for safely deferred
    unmatched hardware. P1/P2 are review *importance*, not ICC outcomes.
    """
    overrides = overrides or {}
    all_records = dict(payload.get("commits") or {})
    if extra_commits:
        all_records.update(extra_commits)
    order = list(order if order is not None else payload.get("order") or [])
    order_set = set(order)

    def effective(sha: str) -> str:
        return overrides.get(sha, {}).get("decision", all_records[sha].get("advisor_decision"))

    selected = {sha for sha in order if effective(sha) == "TAKE"}
    active_soc_ids = _selected_hardware_identities(all_records, payload.get("platform_profile"))
    attention = defaultdict(set)
    issue_kinds = defaultdict(set)
    issues = []

    def issue(target: str, kind: str, detail: str, *, source: str = "") -> None:
        attention[target].add(detail)
        issue_kinds[target].add(kind)
        issues.append({"kind": kind, "sha": target, "source": source,
                       "reason": detail})

    for consumer in sorted(selected):
        rec = all_records[consumer]
        for edge in rec.get("advisor_prerequisites") or []:
            dep = edge.get("sha") if isinstance(edge, dict) else None
            if not dep or dep in selected:
                continue
            if dep in all_records and not _missing(all_records[dep]):
                continue
            issue(dep, "TAKE_PREREQUISITE",
                  f"Selected TAKE {consumer[:12]} has an unresolved explicit prerequisite",
                  source=consumer)

    for original, children in _fixes_sources(all_records).items():
        if original not in selected:
            continue
        for fix in sorted(children):
            rec = all_records[fix]
            if fix in selected or not _missing(rec):
                continue
            override = overrides.get(fix, {})
            if (fix in order_set and effective(fix) == "SKIP"
                    and override.get("rationale", "").strip()):
                continue
            issue(fix, "UNRESOLVED_LATER_FIX",
                  f"Known later fix for TAKE {original[:12]} needs TAKE, downstream evidence, "
                  "or justified engineer SKIP", source=original)

    if icc:
        for blocker in icc.get("logical_blockers") or []:
            sha = blocker.get("prerequisite") or blocker.get("fix_commit") or blocker.get("sha")
            if isinstance(sha, str) and sha:
                issue(sha, blocker.get("kind", "ICC_BLOCKER"),
                      "Current ICC blocker: " + str(blocker.get("kind", "unknown")),
                      source=blocker.get("sha", ""))
        # A failed exploratory cherry-pick is an *integration* P0, not a
        # logical dependency proof. Do not convert a suspect series peer to
        # a hard prerequisite or change any TAKE/REVIEW/SKIP decision.
        replay = icc.get("replay") or {}
        if replay.get("status") == "blocked":
            for commit in replay.get("commits") or []:
                if commit.get("status") not in ("conflict", "empty", "error"):
                    continue
                sha = commit.get("sha")
                if not isinstance(sha, str) or not sha:
                    continue
                paths = ", ".join((commit.get("files") or [])[:3])
                issue(sha, "ICC_REPLAY_BLOCKER",
                      "ICC exploratory replay " + str(commit["status"])
                      + (" in " + paths if paths else "")
                      + "; inspect conflict diagnosis; this is not a proven prerequisite",
                      source=sha)

    replay_records = {
        item.get("sha"): item
        for item in (icc or {}).get("replay", {}).get("commits", [])
        if item.get("sha")
    }
    conflict_details = {
        item.get("sha"): item.get("diagnosis")
        for item in (icc or {}).get("replay", {}).get("commits", [])
        if item.get("diagnosis") and item.get("sha")
    }
    queues = {p: [] for p in PRIORITY_NAMES}
    index = {sha: i for i, sha in enumerate(order)}

    def row(sha: str, priority: str, reason: str, basis: str,
            *, disposition: str | None = None) -> dict:
        rec = all_records[sha]
        rel = rec.get("platform_relevance") or {}
        return {
            "sha": sha, "subject": rec.get("subject") or "(untitled)",
            "priority": priority, "priority_label": PRIORITY_LABELS[priority],
            "priority_reason": reason, "priority_basis": basis,
            "decision": disposition if disposition is not None else effective(sha),
            "advisor_decision": rec.get("advisor_decision"),
            "files": rec.get("files") or [],
            "platform_status": rel.get("status") or "UNKNOWN",
            "soc_identities": sorted(hardware_only_identities(rec)),
            "series_ids": rec.get("series_ids") or [],
            "conflict_diagnosis": conflict_details.get(sha),
            # Triage is derived below, independently of priority and decision.
            "triage": "UNCLASSIFIED", "triage_confidence": "none",
            "triage_reason": "Insufficient evidence for an engineering disposition",
            "triage_action": "Inspect upstream and downstream changes before deciding",
            "related": [], "related_basis": "",
        }

    for sha in order:
        if effective(sha) != "REVIEW":
            continue
        rec = all_records[sha]
        if attention.get(sha):
            priority, reason, basis = ("p0", "; ".join(sorted(attention[sha])),
                                       "deterministic:icc-obligation")
        elif is_deferable_hardware(rec, active_soc_ids=active_soc_ids):
            ids = hardware_only_identities(rec)
            reason = ("SoC-only bindings, headers or configuration for "
                      + ", ".join(sorted(ids)) + "; no supported-platform match or TAKE requirement"
                      if ids and not is_dt_only(rec.get("files") or [])
                      else "Unmatched board/SoC-only change; no identified TAKE requirement")
            priority, basis = "p3", "deterministic:hardware-only"
        else:
            priority, reason, basis = _engineering_priority(rec)
        queues[priority].append(row(sha, priority, reason, basis))

    # A selected prerequisite/fix can be SKIP or absent from the worksheet;
    # nevertheless it must stay visible as a critical item.
    for sha in sorted(attention, key=lambda s: (index.get(s, len(order)), s)):
        if sha in order_set and effective(sha) == "REVIEW":
            continue
        if sha not in all_records:
            queues["p0"].append({
                "sha": sha, "subject": "Unknown required commit (not in plan)",
                "priority": "p0", "priority_label": PRIORITY_LABELS["p0"],
                "priority_reason": "; ".join(sorted(attention[sha])),
                "priority_basis": "deterministic:icc-obligation",
                "decision": "NOT_IN_PLAN", "advisor_decision": None, "files": [],
                "platform_status": "UNKNOWN", "soc_identities": [], "series_ids": [],
                "conflict_diagnosis": conflict_details.get(sha),
            # Triage is derived below, independently of priority and decision.
            "triage": "UNCLASSIFIED", "triage_confidence": "none",
            "triage_reason": "Insufficient evidence for an engineering disposition",
            "triage_action": "Inspect upstream and downstream changes before deciding",
            "related": [], "related_basis": "",
            })
            continue
        queues["p0"].append(row(
            sha, "p0", "; ".join(sorted(attention[sha])),
            "deterministic:icc-obligation",
            disposition=effective(sha) if sha in order_set else "NOT_IN_WORKSHEET"))

    # An engineering *obligation* is not a command to TAKE a particular SHA.
    # In particular, Fixes: provenance never proves a hard prerequisite.
    obligations = {"TAKE_PREREQUISITE", "UNRESOLVED_LATER_FIX"}
    all_rows = [entry for priority in PRIORITY_NAMES for entry in queues[priority]]
    selected_consumers = defaultdict(set)
    for consumer in selected:
        for edge in all_records[consumer].get("advisor_prerequisites") or []:
            dep = edge.get("sha") if isinstance(edge, dict) else None
            if dep:
                selected_consumers[dep].add(consumer)

    for item in all_rows:
        commit = item["sha"]
        kinds = issue_kinds[commit]
        rec = all_records.get(commit, {})
        paths = rec.get("files") or []
        replay_item = replay_records.get(commit, {})
        if kinds.intersection(obligations) or any(
                kind not in ("ICC_REPLAY_BLOCKER",) for kind in kinds):
            item.update(
                triage="REQUIRED", triage_confidence="high",
                triage_reason=(
                    "Unresolved selection, prerequisite, or later-fix obligation; "
                    "this does not by itself prove the commit must be cherry-picked"),
                triage_action=(
                    "Resolve using verified downstream evidence, a justified "
                    "engineer decision, or the minimum required patch chain"))
        elif (is_deferable_hardware(rec, active_soc_ids=active_soc_ids)
              and item["decision"] == "REVIEW"):
            item.update(
                triage="OPTIONAL", triage_confidence="medium",
                triage_reason="Unmatched hardware-only change; no positive active-platform match",
                triage_action="Verify platform exclusions before engineer SKIP")
        elif (item["decision"] == "TAKE"
              and replay_item.get("status") in ("conflict", "empty", "error")
              and _STYLE_ONLY.search(str(rec.get("subject") or ""))
              and paths and all(_SOC_DTS.fullmatch(path) or
                                path.startswith("Documentation/")
                                for path in paths)
              and not selected_consumers.get(commit)
              and not rec.get("fixes")):
            item.update(
                triage="OPTIONAL", triage_confidence="medium",
                triage_reason=(
                    "Style-only DTS/documentation change with no selected "
                    "TAKE prerequisite consumers or Fixes: relationship"),
                triage_action="Verify the diff is cosmetic, then consider engineer SKIP")
        elif replay_item.get("status") in ("conflict", "empty", "error"):
            item.update(
                triage="INTEGRATION", triage_confidence="high",
                triage_reason=(
                    "Selected patch has an observed exploratory replay difficulty"
                    + (" (conditional)" if replay_item.get("conditional") else "")
                    + "; a conflict does not establish a missing prerequisite"),
                triage_action=(
                    "Compare upstream and downstream changes, then adapt, "
                    "resolve a genuine prerequisite, or reconsider TAKE"))

    # Report related work as a hint, never as a hard dependency edge. Exact
    # changed-path overlap or an explicit shared series ID is required.
    critical = queues["p0"]
    for item in critical:
        peers = []
        mine = all_records.get(item["sha"], {})
        my_files = set(mine.get("files") or [])
        my_series = set(mine.get("series_ids") or [])
        for other in critical:
            if other is item:
                continue
            theirs = all_records.get(other["sha"], {})
            if my_series.intersection(theirs.get("series_ids") or []):
                peers.append((other["sha"], "shared upstream series"))
            elif my_files.intersection(theirs.get("files") or []):
                peers.append((other["sha"], "overlapping changed file"))
        item["related"] = [peer[:12] for peer, _ in peers[:8]]
        if peers:
            item["related_basis"] = (
                "Same series or changed file; not proven dependencies")

    triage_summary = {
        p: {kind: sum(row["triage"] == kind for row in queues[p])
            for kind in TRIAGE_NAMES}
        for p in PRIORITY_NAMES
    }

    groups = defaultdict(list)
    for item in queues["p3"]:
        group = (item["series_ids"] or ["ungrouped"])[0]
        groups[group].append(item)
    deferred_groups = []
    for group, members in sorted(groups.items(), key=lambda kv:
                                 (kv[0] == "ungrouped", kv[0])):
        deferred_members = {m["sha"] for m in members}
        active_series = [sha for sha in order if sha not in deferred_members
                         and group != "ungrouped"
                         and group in (all_records[sha].get("series_ids") or [])
                         and effective(sha) == "TAKE"]
        deferred_groups.append({"series": group, "count": len(members),
                                "sha": [m["sha"] for m in members],
                                "take_peers": active_series})
    return {
        "schema": 4,
        "source_plan_sha256": plan_sha256,
        "decision_revision": decision_revision,
        "icc_current": bool(icc),
        "summary": {p: len(queues[p]) for p in PRIORITY_NAMES},
        "selected_take": len(selected),
        "triage_summary": triage_summary,
        "issues": issues,
        "queues": queues,
        "deferred_series": deferred_groups,
        "note": "Priorities, engineering triage, and TAKE/REVIEW/SKIP decisions "
                "are independent. REQUIRED means an obligation must be resolved "
                "rather than automatically cherry-picked. OPTIONAL is only a "
                "candidate for engineer review, never an automatic SKIP. "
                "An observed conflict is not proof of a dependency. "
                "A stale ICC cannot certify readiness; run check --no-replay.",
    }


def render_text(data: dict, priority: str) -> str:
    rows = data["queues"][priority]
    counts = data.get("triage_summary", {}).get(priority, {})
    lines = [f"{PRIORITY_LABELS[priority]} ({len(rows)})", "=" * 76,
             f"TAKE: {data['selected_take']} | Priorities are independent "
             "of TAKE/REVIEW/SKIP",
             "Triage is advisory; worksheet decisions are unchanged.",
             "Triage: " + ", ".join(f"{name}={counts.get(name, 0)}"
                                   for name in TRIAGE_NAMES), ""]
    if not data["icc_current"]:
        lines += ["ICC: not current/available; run kernel-backport check --no-replay", ""]
    assessed = [item for item in rows if item.get("assessment_status") == "assessed"]
    if assessed:
        counts_by_action = {action: sum(item["assessment"]["recommendation"] == action
                                        for item in assessed)
                            for action in ("RETAIN", "ADAPT", "EXCLUDE", "INVESTIGATE")}
        lines.append("Patch-grounded assessments: " + str(len(assessed)) + "/" + str(len(rows)))
        lines.append("Recommendations: " + ", ".join(
            action + "=" + str(count) for action, count in counts_by_action.items()))
        lines.append("Recommendation concerns inclusion value; difficulty is separate.")
        lines.append("")
    if priority == "p3":
        grouped = {item["sha"]: item for item in rows}
        for group in data["deferred_series"]:
            lines.append(f"Series {group['series']} ({group['count']} deferred)"
                         + (f"; {len(group['take_peers'])} TAKE peer(s) in same series"
                            if group["take_peers"] else ""))
            for sha in group["sha"]:
                item = grouped[sha]
                lines.append(f"  {sha[:12]}  {item['subject']}")
                lines.append(f"    triage: {item['triage']} "
                             f"(confidence: {item['triage_confidence']})")
            lines.append("")
    else:
        for kind in TRIAGE_NAMES:
            entries = [r for r in rows if r.get("triage") == kind]
            if not entries:
                continue
            lines.extend([f"{kind} ({len(entries)})", "-" * 76])
            for item in entries:
                lines.extend([
                    f"{item['sha'][:12]}  {item['subject']}",
                    f"  {item['platform_status']} | {item['priority_reason']}",
                    f"  priority basis: {item['priority_basis']}",
                    f"  triage: {kind} (confidence: {item['triage_confidence']})",
                    f"  integration difficulty: {item.get('integration_difficulty', 'unknown')}",
                    f"  backport value: {item.get('backport_value', 'UNASSESSED')}",
                    f"  why: {item['triage_reason']}",
                    f"  action: {item['triage_action']}",
                    f"  files: {', '.join(item['files'][:4]) or '(not recorded)'}"
                    + (" ..." if len(item["files"]) > 4 else "")])
                assessment = item.get("assessment")
                if assessment:
                    lines.extend([
                        "  recommendation: " + assessment["recommendation"]
                        + " (confidence: " + assessment["confidence"] + ")",
                        "  assessment why: " + assessment["rationale"],
                        "  assessment action: " + assessment["next_action"],
                        "  assessment risks: " + assessment["risks"],
                        "  verify: " + str(assessment["needs_verification"]),
                    ])
                    for evidence in assessment["evidence"]:
                        lines.append("  evidence " + evidence["source"] + ": "
                                     + evidence["finding"])
                elif item.get("assessment_status") == "stale":
                    lines.append("  assessment: stale; rerun review --assess")
                if item.get("related"):
                    lines.append("  related: " + ", ".join(item["related"]))
                    lines.append("  relation: " + item["related_basis"])
                if item.get("conflict_diagnosis"):
                    lines.append("  integration diagnosis: " + str(
                        item["conflict_diagnosis"].get("classification", "unknown")))
                    lines.append("  next: " + str(
                        item["conflict_diagnosis"].get("suggested_action", "")))
                lines.append("")
    return wrap_text("\n".join(lines).rstrip() + "\n")


def write_reports(directory: Path, data: dict) -> None:
    """Atomically refresh derived reports; do not touch plan/worksheet/ICC."""
    import os
    import tempfile
    def write(path: Path, contents: bytes):
        if path.is_symlink():
            raise ValueError(f"refusing symlink report: {path}")
        fd, temp = tempfile.mkstemp(prefix=".review-", dir=directory)
        try:
            with os.fdopen(fd, "wb") as f:
                os.fchmod(f.fileno(), 0o600)
                f.write(contents)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp, path)
        finally:
            Path(temp).unlink(missing_ok=True)
    for priority in PRIORITY_NAMES:
        write(directory / f"review-{priority}.txt", render_text(data, priority).encode())
    # Remove stale reports from the old three-queue terminology. These are
    # generated artifacts, never the source plan, worksheet, or ICC attestation.
    for old in ("action-required", "engineering", "deferred-hardware"):
        legacy = directory / f"review-{old}.txt"
        if legacy.is_symlink():
            raise ValueError(f"refusing symlink report: {legacy}")
        legacy.unlink(missing_ok=True)
    write(directory / "review-queues.json",
          (json.dumps(data, sort_keys=True, indent=2) + "\n").encode())
