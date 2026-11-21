#!/usr/bin/env python3
"""Priority queues never change engineering TAKE/REVIEW/SKIP dispositions."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from review_queues import (build_queues, is_dt_only, write_reports, _fresh_icc,
                           hardware_only_identities, soc_identities)

CLI = Path(__file__).with_name("kernel-backport")


def rec(title, files, *, relevance="UNMATCHED_BOARD", decision="REVIEW",
        prereqs=None, fixes=None, series=None, present=False, includes=None):
    return {"subject": title, "files": files, "advisor_decision": decision,
            "advisor_prerequisites": [{"sha": x} for x in (prereqs or [])],
            "fixes": fixes or [], "series_ids": series or [],
            "patch_context_dt_includes": includes or [],
            "match": {"status": "present" if present else "missing"},
            "platform_relevance": {"status": relevance,
                                   "matches": [{"name": "RHEL"}] if relevance == "DIRECT" else []}}


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.board = "a"*40
        self.soc = "b"*40
        self.mixed = "c"*40
        self.common = "d"*40
        self.consumer = "e"*40
        self.fix = "f"*40
        self.commits = {
            self.board: rec("ASUS Vivobook IRIS", ["arch/arm64/boot/dts/qcom/x1e80100-asus-vivobook-s15.dts"],
                            series=["S001"], includes=[{"path": "hamoa.dtsi"}]),
            self.soc: rec("Kaanapali SoC", ["arch/arm64/boot/dts/qcom/kaanapali.dtsi"],
                          relevance="UNMATCHED_SOC", series=["S001"]),
            self.mixed: rec("board plus shared code",
                            ["arch/arm64/boot/dts/qcom/qcom-board.dts", "drivers/clk/qcom/gcc.c"],
                            series=["S001"]),
            self.common: rec("common arm64", ["arch/arm64/kernel/fpsimd.c"],
                             relevance="SHARED_OR_UNKNOWN"),
            self.consumer: rec("needed ARM64 consumer", ["arch/arm64/kernel/process.c"],
                               decision="TAKE", relevance="SHARED_OR_UNKNOWN"),
            self.fix: rec("later fix", ["lib/vdso/gettimeofday.c"],
                          relevance="SHARED_OR_UNKNOWN", fixes=[self.consumer]),
        }
        self.order = list(self.commits)
        self.payload = {"commits": self.commits, "order": self.order}

    def build(self, overrides=None):
        return build_queues(self.payload, overrides=overrides)

    def test_unmatched_board_only_and_unchanged_include_deferred(self):
        q = self.build()["queues"]
        self.assertIn(self.board, [r["sha"] for r in q["p3"]])
        self.assertIn(self.soc, [r["sha"] for r in q["p3"]])
        self.assertEqual(len((q["p1"] + q["p2"])), 2)
        self.assertFalse(is_dt_only(["arch/arm64/boot/dts/qcom/Makefile"]))
        self.assertTrue(is_dt_only(["arch/arm64/boot/dts/qcom/Makefile",
                                    "arch/arm64/boot/dts/qcom/qcom-board.dts"]))

    def test_mixed_board_shared_driver_goes_engineering(self):
        q = self.build()["queues"]
        self.assertIn(self.mixed, [r["sha"] for r in q["p1"] + q["p2"]])
        self.assertIn(self.common, [r["sha"] for r in q["p1"] + q["p2"]])

    def test_take_consumer_prerequisite_promotes_board(self):
        self.commits[self.consumer]["advisor_prerequisites"] = [{"sha": self.board}]
        q = self.build()["queues"]
        self.assertIn(self.board, [r["sha"] for r in q["p0"]])
        self.assertNotIn(self.board, [r["sha"] for r in q["p3"]])

    def test_skip_dependency_still_reports_action_required(self):
        self.commits[self.consumer]["advisor_prerequisites"] = [{"sha": self.board}]
        q = self.build({self.board: {"decision": "SKIP", "rationale": "not RHEL"}})["queues"]
        rows = [r for r in q["p0"] if r["sha"] == self.board]
        self.assertEqual(rows[0]["decision"], "SKIP")

    def test_known_later_fix_requires_disposition(self):
        q = self.build()["queues"]
        self.assertIn(self.fix, [r["sha"] for r in q["p0"]])
        q2 = self.build({self.fix: {"decision": "SKIP", "rationale": "Already implemented differently"}})["queues"]
        self.assertNotIn(self.fix, [r["sha"] for r in q2["p0"]])
        q3 = self.build({self.fix: {"decision": "SKIP", "rationale": ""}})["queues"]
        self.assertIn(self.fix, [r["sha"] for r in q3["p0"]])
        self.commits[self.fix]["match"]["status"] = "present"
        self.assertNotIn(self.fix, [r["sha"] for r in self.build()["queues"]["p0"]])

    def test_nonworksheet_fix_visible(self):
        self.order.remove(self.fix)
        q = self.build()["queues"]
        row = next(r for r in q["p0"] if r["sha"] == self.fix)
        self.assertEqual(row["decision"], "NOT_IN_WORKSHEET")

    def test_series_group_does_not_defer_mixed_member(self):
        data = self.build()
        group = next(g for g in data["deferred_series"] if g["series"] == "S001")
        self.assertEqual(group["count"], 2)
        self.assertNotIn(self.mixed, group["sha"])

    def test_override_reclassifies_without_changing_plan_source(self):
        before = json.dumps(self.payload, sort_keys=True)
        data = self.build({self.board: {"decision": "SKIP"}})
        self.assertEqual(len(data["queues"]["p3"]), 1)
        self.assertEqual(json.dumps(self.payload, sort_keys=True), before)

    def test_soc_binding_header_and_config_deferred(self):
        samples = [
            ("QCS8300 clock definitions",
             ["Documentation/devicetree/bindings/clock/qcom,qcs8300-gcc.yaml",
              "include/dt-bindings/clock/qcom,qcs8300-gcc.h"], "qcs8300"),
            ("Add initial i.MX943", ["arch/arm64/boot/dts/freescale/imx943.dtsi",
                                    "arch/arm64/boot/dts/freescale/imx943-clock.h"], "imx943"),
            ("Enable Qualcomm Kaanapali clocks", ["arch/arm64/configs/defconfig"], "kaanapali"),
            ("Enable PCIe for Renesas RZ/G3S SoC", ["arch/arm64/configs/defconfig"], "rzg3s"),
        ]
        for i, (subject, files, ident) in enumerate(samples):
            s = format(i+1, "040x")
            self.commits[s] = rec(subject, files, relevance="SHARED_OR_UNKNOWN")
            self.order.append(s)
            self.assertIn(ident, hardware_only_identities(self.commits[s]))
        data = self.build()
        deferred = {r["sha"] for r in data["queues"]["p3"]}
        self.assertTrue(all(format(i+1, "040x") in deferred for i in range(len(samples))))

    def test_common_binding_and_shared_driver_are_not_deferred(self):
        cases = [
            ("Renesas RZ/G3E binding",
             ["Documentation/devicetree/bindings/pinctrl/renesas,rzg2l-pinctrl.yaml",
              "include/dt-bindings/pinctrl/renesas,r9a09g047-pinctrl.h"]),
            ("QCS8300 clocks plus generic driver",
             ["include/dt-bindings/clock/qcom,qcs8300-gcc.h",
              "drivers/clk/qcom/clk-rpmh.c"]),
            ("Generic config change", ["arch/arm64/configs/defconfig"]),
        ]
        for i, (subject, files) in enumerate(cases):
            sha = format(i+17, "040x")
            self.commits[sha] = rec(subject, files, relevance="SHARED_OR_UNKNOWN")
            self.order.append(sha)
            self.assertFalse(hardware_only_identities(self.commits[sha]))
        eng = {r["sha"] for r in (self.build()["queues"]["p1"] + self.build()["queues"]["p2"])}
        self.assertTrue(all(format(i+17, "040x") in eng for i in range(len(cases))))

    def test_selected_soc_identity_and_take_prerequisite_protect_defer(self):
        sha = "1" * 40
        name = "QCS8300 binding"
        self.commits[sha] = rec(name, ["include/dt-bindings/clock/qcom,qcs8300-gcc.h"],
                                 relevance="SHARED_OR_UNKNOWN")
        self.order.append(sha)
        self.commits[self.consumer]["advisor_prerequisites"] = [{"sha": sha}]
        data = self.build()
        self.assertIn(sha, {r["sha"] for r in data["queues"]["p0"]})
        self.commits[self.consumer]["advisor_prerequisites"] = []
        # A different commit in the same plan has explicit positive evidence
        # for this SoC; don't infer from mixed changed-file sets.
        self.commits[self.consumer]["platform_relevance"]["matches"] = [
            {"platform": "RHEL QCS8300", "evidence": "source-dt-compatible:qcom,qcs8300"}]
        self.assertIn(sha, {r["sha"] for r in (self.build()["queues"]["p1"] + self.build()["queues"]["p2"])})

    def test_active_selected_profile_protects_unmatched_soc_in_other_commit(self):
        sha = "9"*40
        self.commits[sha] = rec("R-Car S4 clocks", [
            "include/dt-bindings/clock/renesas,r8a779f0.h"],
            relevance="SHARED_OR_UNKNOWN")
        self.order.append(sha)
        self.payload["platform_profile"] = {"selected_platforms": [{
            "name": "Renesas R-Car S4", "paths": [],
            "identifiers": {"dt_compatible": ["renesas,r8a779f0"]}}]}
        self.assertIn(sha, {r["sha"] for r in (self.build()["queues"]["p1"] + self.build()["queues"]["p2"])})

    def test_selected_soc_dts_without_direct_match_not_deferred(self):
        sha = "8"*40
        self.commits[sha] = rec("Lemans board update", [
            "arch/arm64/boot/dts/qcom/lemans-evk.dts"],
            relevance="UNMATCHED_BOARD")
        self.order.append(sha)
        self.payload["platform_profile"] = {"selected_platforms": [{
            "name": "Qualcomm SA8775P Snapdragon Ride", "paths": [
                "arch/arm64/boot/dts/qcom/lemans.dtsi"],
            "identifiers": {"dt_compatible": ["qcom,sa8775p"]}}]}
        self.assertIn(sha, {r["sha"] for r in (self.build()["queues"]["p1"] + self.build()["queues"]["p2"])})

    def test_mixed_platform_match_cannot_invent_supported_soc(self):
        # Lemans support must not cause Kaanapali in the same patch's file
        # list to count as a supported SoC for a separate defconfig change.
        sha = "2" * 40
        self.commits[sha] = rec("Enable Qualcomm Kaanapali in defconfig",
                                 ["arch/arm64/configs/defconfig"],
                                 relevance="SHARED_OR_UNKNOWN")
        self.order.append(sha)
        self.commits[self.consumer]["files"] = [
            "arch/arm64/boot/dts/qcom/lemans.dtsi",
            "arch/arm64/boot/dts/qcom/kaanapali.dtsi"]
        self.commits[self.consumer]["platform_relevance"]["matches"] = [
            {"platform": "Qualcomm SA8775P Snapdragon Ride", "evidence":
             "path:arch/arm64/boot/dts/qcom/lemans.dtsi"}]
        self.assertIn(sha, {r["sha"] for r in self.build()["queues"]["p3"]})

    def test_no_accidental_substring_soc_match(self):
        self.assertEqual(soc_identities("qcs8300"), {"qcs8300"})
        self.assertNotIn("qcs615", soc_identities("qcs8300"))
        self.assertFalse(soc_identities("Generic Hyper-V guest"))

    def test_icc_freshness(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / ".kernel-backport").mkdir()
            p = d / ".kernel-backport/icc.json"
            p.write_text(json.dumps({"plan_sha256": "aaa", "decision_revision": "r1",
                                     "logical_blockers": [{"kind": "MISSING", "sha": self.board}]}))
            self.assertIsNone(_fresh_icc(d, "aaa", "r2"))
            self.assertIsNotNone(_fresh_icc(d, "aaa", "r1"))
            self.assertIsNone(_fresh_icc(d, "bbb", "r1"))
            queues = self.build()
            self.assertIn("icc_current", queues)
            self.assertNotIn("ics_current", queues)

    def test_terra_high_and_normal_priorities_are_independent_of_decisions(self):
        high_sha = "4" * 40
        normal_sha = "5" * 40
        self.commits[high_sha] = rec("arm64 shared code candidate",
            ["arch/arm64/mm/fault.c"], relevance="SHARED_OR_UNKNOWN")
        self.commits[high_sha].update({
            "advisor_review_priority": "HIGH",
            "advisor_review_priority_reason": "Fixes a verified ARM64 fault handling regression",
            "advisor_decision_model": "gpt-6.1-terra",
        })
        self.commits[normal_sha] = rec("cleanup shared interfaces",
            ["include/linux/example.h"], relevance="SHARED_OR_UNKNOWN")
        self.commits[normal_sha].update({
            "advisor_review_priority": "NORMAL",
            "advisor_review_priority_reason": "Optional API cleanup without a known regression",
            "advisor_decision_model": "gpt-6.1-terra",
        })
        self.order.extend([high_sha, normal_sha])
        q = self.build()
        high = next(x for x in q["queues"]["p1"] if x["sha"] == high_sha)
        normal = next(x for x in q["queues"]["p2"] if x["sha"] == normal_sha)
        self.assertIn("Terra:", high["priority_reason"])
        self.assertIn("gpt-6.1-terra", normal["priority_basis"])
        self.assertEqual(self.commits[high_sha]["advisor_decision"], "REVIEW")

    def test_missing_prerequisite_not_in_graph_is_still_p0(self):
        missing = "7" * 40
        self.commits[self.consumer]["advisor_prerequisites"] = [{"sha": missing}]
        report = self.build()
        row = next(r for r in report["queues"]["p0"] if r["sha"] == missing)
        self.assertEqual(row["decision"], "NOT_IN_PLAN")
        self.assertEqual(report["issues"][0]["kind"], "TAKE_PREREQUISITE")

    def test_model_high_never_overrides_hardware_defer_or_creates_p0(self):
        self.commits[self.board].update({
            "advisor_review_priority": "HIGH",
            "advisor_review_priority_reason": "Claimed important by advisor",
        })
        q = self.build()
        self.assertIn(self.board, {r["sha"] for r in q["queues"]["p3"]})
        self.assertNotIn(self.board, {r["sha"] for r in q["queues"]["p0"]})

    def test_p0_and_p1_do_not_change_icc_semantics(self):
        self.commits[self.consumer]["advisor_prerequisites"] = [{"sha": self.board}]
        q = self.build()
        self.assertTrue(any(r["sha"] == self.board for r in q["queues"]["p0"]))
        self.assertEqual(q["schema"], 4)
        self.assertEqual(q["issues"][0]["kind"], "TAKE_PREREQUISITE")
        # A plausible shared-code REVIEW remains nonblocking P1 or P2.
        self.assertFalse(any(r["sha"] == self.common for r in q["queues"]["p0"]))

    def test_old_generated_reports_removed_without_touching_worksheet(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "backport_worksheet.txt").write_text("engineer decisions preserved\n")
            (d / "review-engineering.txt").write_text("legacy report")
            write_reports(d, self.build())
            self.assertFalse((d / "review-engineering.txt").exists())
            for p in ("p0", "p1", "p2", "p3"):
                self.assertTrue((d / f"review-{p}.txt").exists())
            self.assertIn("preserved", (d / "backport_worksheet.txt").read_text())

    def test_report_writes_only_derived_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "backport_worksheet.txt").write_text("engineer SKIP\n")
            before = hashlib.sha256((d / "backport_worksheet.txt").read_bytes()).hexdigest()
            write_reports(d, self.build())
            self.assertEqual(hashlib.sha256((d / "backport_worksheet.txt").read_bytes()).hexdigest(), before)
            self.assertIn("ASUS Vivobook", (d / "review-p3.txt").read_text())
            self.assertTrue((d / "review-queues.json").exists())


class ReviewCliTest(unittest.TestCase):
    def test_cli_without_git_repository_and_engineer_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "backport_plan_01"
            d.mkdir()
            a, b = "a"*40, "b"*40
            payload = {"downstream": "c"*40, "replay_start": "c"*40,
                       "target": "d"*40, "order": [a,b],
                       "commits": {a: rec("board-only", ["arch/arm64/boot/dts/qcom/qcom-mtp.dts"]),
                                   b: rec("shared", ["arch/arm64/kernel/smp.c"],
                                          relevance="SHARED_OR_UNKNOWN")}}
            (d / "backport_plan.json").write_text(json.dumps(payload))
            lines = ['BACKPORT WORKSHEET (oldest first)', '=' * 80, f'{a[:12]} [DIRECT] ("board-only")', '      advisor: REVIEW',
                     f'{b[:12]} [DIRECT] ("shared")', '      advisor: REVIEW', '',
                     'SAME PATCH SERIES', '=' * 80, 'appendix']
            original = "\n".join(lines) + "\n"
            (d / "backport_worksheet.txt").write_text(original)
            command = [sys.executable, str(CLI), "review", "--results-dir", str(d),
                       "--priority", "high"]
            cp = subprocess.run(command, cwd=tmp, capture_output=True, text=True)
            self.assertEqual(cp.returncode, 0, cp.stderr)
            self.assertIn("P1 high=0, P2 normal=1, P3 deferred=1", cp.stdout)
            self.assertEqual((d / "backport_worksheet.txt").read_text(), original)
            # Engineer SKIP changes view, not source plan.
            lines.insert(4, '      engineer-decision: SKIP')
            lines.insert(5, '      engineer-rationale: Out of scope')
            (d / "backport_worksheet.txt").write_text("\n".join(lines) + "\n")
            cp = subprocess.run(command, cwd=tmp, capture_output=True, text=True)
            self.assertEqual(cp.returncode, 0, cp.stderr)
            self.assertIn("P1 high=0, P2 normal=1, P3 deferred=0", cp.stdout)
            self.assertEqual(json.loads((d / "backport_plan.json").read_text()), payload)
            default = subprocess.run([sys.executable, str(CLI), "review",
                "--results-dir", str(d)], cwd=tmp, capture_output=True, text=True)
            self.assertEqual(default.returncode, 0, default.stderr)
            self.assertIn("P0 CRITICAL", default.stdout)
            self.assertIn("P1 HIGH", default.stdout)
            self.assertNotIn("P3 DEFERRED (", default.stdout)
            everything = subprocess.run([sys.executable, str(CLI), "review",
                "--results-dir", str(d), "--all", "--format", "json"],
                cwd=tmp, capture_output=True, text=True)
            self.assertEqual(everything.returncode, 0, everything.stderr)
            self.assertEqual(list(json.loads(everything.stdout)["queues"]),
                             ["p0", "p1", "p2", "p3"])
            invalid = subprocess.run([sys.executable, str(CLI), "review",
                "--results-dir", str(d), "--all", "--priority", "high"],
                cwd=tmp, capture_output=True, text=True)
            self.assertNotEqual(invalid.returncode, 0)


if __name__ == "__main__":
    unittest.main()
