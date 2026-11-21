#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""0.1.26 regression coverage: feature-chain decisions and credential loading."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from openai_credentials import api_key_from_config
from feature_assessor import (build_features, attach_feature_cache,
                              assess_features, feature_metadata,
                              validate_feature_result, feature_text,
                              write_feature_reports, read_feature_cache)
from review_queues import build_queues


def record(subject, files, **more):
    return {"subject": subject, "files": files, "advisor_decision": "TAKE",
            "advisor_rationale": "Relevant if feature is enabled", "advisor_prerequisites": [],
            "fixes": [], "required_by": [], "triggered_by": [], "series_ids": [],
            "match": {"status": "missing"},
            "platform_relevance": {"status": "SHARED_OR_UNKNOWN", "matches": [],
                                   "excluded_matches": []}, **more}


class StubFeatureModel:
    def __init__(self, group, *, recommendation="RETAIN", member_actions=None):
        self.group = group
        self.calls = []
        self.recommendation = recommendation
        self.actions = member_actions or {}

    def assess_feature(self, packet, prompt, schema):
        self.calls.append(packet)
        return {
            "recommendation": self.recommendation,
            "selection_decision": ("EXCLUDE" if self.recommendation == "EXCLUDE"
                                   else "INCLUDE"),
            "inclusion_basis": "Demonstrated supported consumer if included",
            "exclusion_impact": "Would lose tested functionality if required",
            "capability": "Sample CFI feature",
            "minimum_candidate_shas": list(self.group["members"]),
            "minimum_set_conditions": "Retain the selected consumer and its provider if enabled",
            "member_roles": [{"sha": s, "role": "CORE"} for s in self.group["members"]],
            "backport_value": "HIGH" if self.recommendation == "RETAIN" else "LOW",
            "confidence": "medium",
            "feature_rationale": "Feature supports a declared RHEL architecture",
            "member_recommendations": [
                {"sha": sha, "recommendation": self.actions.get(sha, "RETAIN"),
                 "reason": "Used by selected supported feature"}
                for sha in self.group["members"]],
            "evidence": [{"sha": self.group["members"][0],
                          "source": "patch", "finding": "Patch adds a macro"}],
            "next_action": "Verify builds and downstream integration",
            "risks": "Requires full supported-architecture validation",
            "needs_verification": True,
        }


class FeatureTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.repo = self.dir / "repo"
        self.repo.mkdir()

        def git(*args):
            return subprocess.check_output(["git", *args], cwd=self.repo, text=True).strip()

        git("init", "-q")
        git("config", "user.name", "Tests")
        git("config", "user.email", "test@example.net")
        (self.repo / "module.c").write_text("#define OLD 1\n")
        git("add", "module.c")
        git("commit", "-qm", "base")
        (self.repo / "module.c").write_text("#define TYPE 1\n")
        git("add", "module.c")
        git("commit", "-qm", "provider")
        self.provider = git("rev-parse", "HEAD")
        (self.repo / "consumer.c").write_text("int use = TYPE;\n")
        git("add", "consumer.c")
        git("commit", "-qm", "consumer")
        self.consumer = git("rev-parse", "HEAD")
        (self.repo / "other.c").write_text("int other = 0;\n")
        git("add", "other.c")
        git("commit", "-qm", "unrelated")
        self.unrelated = git("rev-parse", "HEAD")
        self.records = {
            self.provider: record("cfi: provide TYPE", ["module.c"],
                                  required_by=[self.consumer]),
            self.consumer: record("arm64: consume TYPE", ["consumer.c"],
                                  advisor_prerequisites=[{"sha": self.provider,
                                                          "evidence_kind": "macro"}]),
            self.unrelated: record("arm64: independent change", ["other.c"]),
        }
        self.order = [self.provider, self.consumer, self.unrelated]
        self.plan = SimpleNamespace(
            order=self.order, commits=self.records, overrides={},
            payload={"commits": self.records, "order": self.order,
                     "advisor": {"product": {"product": "RHEL 10"}},
                     "platform_profile": {"sha256": "f" * 64}},
            directory=self.dir, plan_digest="a" * 64,
            effective=lambda sha: self.records[sha]["advisor_decision"],
        )
        self.icc = {"replay": {"status": "blocked", "commits": [
            {"sha": self.provider, "status": "conflict", "conditional": False},
            {"sha": self.consumer, "status": "clean", "conditional": True},
            {"sha": self.unrelated, "status": "clean", "conditional": False},
        ]}, "logical_blockers": []}
        self.report = build_queues(self.plan.payload, icc=self.icc,
                                   plan_sha256=self.plan.plan_digest)

    def groups(self):
        return build_features(self.plan, self.report, priorities=["p0"])

    def test_group_explicit_consumer_and_not_independent_file(self):
        groups = self.groups()
        self.assertEqual(len(groups), 1)
        group = groups[0]
        self.assertEqual(set(group["members"]), {self.provider, self.consumer})
        self.assertNotIn(self.unrelated, group["members"])
        self.assertEqual(group["member_count"], 2)
        self.assertIn("recorded-prerequisite-claim", {l["kind"] for l in group["links"]})
        self.assertTrue(all(not l["hard_prerequisite_proven"] for l in group["links"]))

    def test_feature_assess_stores_cache_without_writing_git_or_worksheet(self):
        groups = self.groups()
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.repo).strip()
        initial = copy.deepcopy(self.records)
        fake = StubFeatureModel(groups[0])
        stats = assess_features(self.plan, groups, self.report, self.icc, self.repo,
                                model="stub", client=fake, print_fn=lambda *x: None)
        self.assertEqual(stats, {"new_assessments": 1, "available": 1})
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual({x["sha"] for x in fake.calls[0]["patches"]},
                         {self.provider, self.consumer})
        self.assertEqual(groups[0]["assessment_status"], "assessed")
        self.assertEqual(self.records, initial)
        self.assertEqual(subprocess.check_output(["git", "rev-parse", "HEAD"],
                                                 cwd=self.repo).strip(), head)
        self.assertEqual(subprocess.check_output(["git", "status", "--porcelain"],
                                                 cwd=self.repo).strip(), b"")
        new = self.groups()
        fake2 = StubFeatureModel(new[0])
        stats2 = assess_features(self.plan, new, self.report, self.icc, self.repo,
                                 model="stub", client=fake2, print_fn=lambda *x: None)
        self.assertEqual(stats2, {"new_assessments": 0, "available": 1})
        self.assertFalse(fake2.calls)
        write_feature_reports(self.dir, new)
        rendered = (self.dir / "review-features.txt").read_text()
        self.assertIn("feature value: HIGH", rendered)
        self.assertIn("recommendation: RETAIN", rendered)
        self.assertTrue(all(len(line) <= 80 for line in rendered.splitlines()))
        self.assertEqual(len(json.loads((self.dir / "review-features.json").read_text())
                             ["features"]), 1)
        self.assertEqual(read_feature_cache(self.dir)["schema"], 3)

    def test_relevant_change_invalidates_cache_and_unrelated_does_not(self):
        groups = self.groups()
        fake = StubFeatureModel(groups[0])
        assess_features(self.plan, groups, self.report, self.icc, self.repo,
                        model="stub", client=fake, print_fn=lambda *x: None)
        self.records[self.unrelated]["advisor_rationale"] += " modified"
        new = self.groups()
        attach_feature_cache(self.plan, new, self.report, self.icc, model="stub")
        self.assertEqual(new[0]["assessment_status"], "assessed")
        self.records[self.consumer]["advisor_rationale"] += " modified"
        new2 = self.groups()
        attach_feature_cache(self.plan, new2, self.report, self.icc, model="stub")
        self.assertEqual(new2[0]["assessment_status"], "stale")

    def test_exclusion_guard_for_required_provider(self):
        group = self.groups()[0]
        meta = feature_metadata(self.plan, group, self.report, self.icc)
        fake = StubFeatureModel(group, recommendation="EXCLUDE",
                                member_actions={self.provider: "EXCLUDE",
                                                self.consumer: "RETAIN"})
        result = validate_feature_result(fake.assess_feature({}, "", {}), group, meta, False)
        self.assertEqual(result["recommendation"], "INVESTIGATE")
        self.assertEqual({x["sha"]: x["recommendation"] for x in
                          result["member_recommendations"]}[self.provider], "INVESTIGATE")

    def test_fix_guard_and_incomplete_member_validation(self):
        self.records[self.consumer]["fixes"] = [self.provider]
        group = self.groups()[0]
        meta = feature_metadata(self.plan, group, self.report, self.icc)
        fake = StubFeatureModel(group, recommendation="EXCLUDE",
                                member_actions={sha: "EXCLUDE" for sha in group["members"]})
        result = validate_feature_result(fake.assess_feature({}, "", {}), group, meta, False)
        self.assertEqual(result["recommendation"], "INVESTIGATE")
        self.assertTrue(result["needs_verification"])
        raw = fake.assess_feature({}, "", {})
        with self.assertRaises(ValueError):
            validate_feature_result({**raw, "member_recommendations": []}, group, meta, False)

    def test_no_api_calls_for_cache_display_and_symlink_rejection(self):
        groups = self.groups()
        attach_feature_cache(self.plan, groups, self.report, self.icc)
        self.assertEqual(groups[0]["assessment_status"], "not-assessed")
        self.assertIn("UNASSESSED", feature_text(groups))
        (self.dir / ".kernel-backport").mkdir(exist_ok=True)
        (self.dir / ".kernel-backport" / "feature-assessments.json").symlink_to(
            self.dir / "arbitrary")
        with self.assertRaises(ValueError):
            read_feature_cache(self.dir)


class KeyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "openai.env"

    def test_fallback_unquoted_export_quoted_and_comments(self):
        self.path.write_text("# ignored\nexport OPENAI_API_KEY='sk-from-file' # comment\n")
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            self.assertEqual(api_key_from_config(self.path), "sk-from-file")
        self.path.write_text('OPENAI_API_KEY="sk-quoted"\n')
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            self.assertEqual(api_key_from_config(self.path), "sk-quoted")

    def test_environment_wins_even_when_file_is_malformed(self):
        self.path.write_text("OPENAI_API_KEY=$(touch /tmp/should-not-run)\n")
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-explicit"}):
            self.assertEqual(api_key_from_config(self.path), "sk-explicit")
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            with self.assertRaises(ValueError):
                api_key_from_config(self.path)

    def test_missing_returns_empty(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            self.assertEqual(api_key_from_config(self.path), "")


if __name__ == "__main__":
    unittest.main()
