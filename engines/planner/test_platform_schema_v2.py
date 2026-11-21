"""Strict platform scope v2 and false-exclusion regression tests."""
import json
import tempfile
from pathlib import Path
import unittest
import types

from test_kernel_backport_planner import kbp


def entry(name, pattern, product="rhel10", basis="certified_rhel10",
          scope="exact_board"):
    return {"name": name, "architecture": "arm64",
            "product_scope": product, "support_basis": basis,
            "match_scope": scope, "paths": [pattern]}


def profile_data():
    return {
        "version": 2, "name": "v2-test", "unmatched_board": "REVIEW",
        "active_product_scopes": ["rhel10", "user_selected"],
        "platforms": [
            entry("RHEL board", "arch/arm64/boot/dts/qcom/selected.dts"),
            entry("RHEL9 reference", "arch/arm64/boot/dts/nvidia/refboard.dts",
                  basis="certified_rhel9_proxy", scope="reference_board_proxy"),
            entry("Separate product", "arch/arm64/boot/dts/renesas/rhivos.dts",
                  product="rhivos", basis="product_documented"),
        ],
        "excluded_platforms": [
            entry("Unsupported board", "arch/arm64/boot/dts/rockchip/unsupported.dts",
                  basis="engineering_exclusion"),
            entry("Auxiliary DT Makefile", "arch/arm64/boot/dts/rockchip/Makefile",
                  basis="engineering_exclusion"),
        ],
    }


class PlatformSchemaV2Tests(unittest.TestCase):
    def load(self, obj):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "profile.json"
            path.write_text(json.dumps(obj))
            return kbp.load_platform_profile(path)

    def planner(self, files, obj=None):
        p = object.__new__(kbp.Planner)
        p.platform_profile = self.load(obj or profile_data())
        p.nodes = {"a" * 40: kbp.CommitNode("a" * 40, "example", files=list(files))}
        p._platform_match_cache = {}
        p._platform_excluded_match_cache = {}
        p._platform_patch_line_cache = {}
        p.changed_files = lambda sha: p.nodes[sha].files
        p.git = types.SimpleNamespace(run=lambda args: "")
        p._tree_file_text = lambda sha, path: None
        return p

    def test_schema_v2_metadata_and_active_scopes(self):
        profile = self.load(profile_data())
        self.assertEqual(profile.version, 2)
        self.assertEqual(profile.active_product_scopes, ("rhel10", "user_selected"))
        self.assertEqual(profile.platforms[1].support_basis, "certified_rhel9_proxy")
        self.assertEqual(profile.platforms[1].match_scope, "reference_board_proxy")
        self.assertEqual(profile.platforms[2].product_scope, "rhivos")

    def test_v2_requires_explicit_semantics(self):
        bad = []
        for field in ("product_scope", "support_basis", "match_scope"):
            obj = profile_data()
            del obj["platforms"][0][field]
            bad.append(obj)
        for field, value in (("product_scope", "not-a-product"),
                             ("support_basis", "guess"),
                             ("match_scope", "unknown")):
            obj = profile_data()
            obj["platforms"][0][field] = value
            bad.append(obj)
        obj = profile_data()
        obj["excluded_platforms"][0]["support_basis"] = "certified_rhel10"
        bad.append(obj)
        obj = profile_data()
        obj["active_product_scopes"].append("rhel10")
        bad.append(obj)
        for item in bad:
            with self.subTest(item=item), self.assertRaises(ValueError):
                self.load(item)

    def test_rhivos_is_inactive_without_opt_in(self):
        path = "arch/arm64/boot/dts/renesas/rhivos.dts"
        p = self.planner([path])
        self.assertEqual(p.platform_relevance("a" * 40)["status"], "UNMATCHED_BOARD")
        obj = profile_data()
        obj["active_product_scopes"].append("rhivos")
        p = self.planner([path], obj)
        rel = p.platform_relevance("a" * 40)
        self.assertEqual(rel["status"], "DIRECT")
        self.assertEqual(rel["matches"][0]["product_scope"], "rhivos")

    def test_rhel9_reference_match_is_qualified_proxy(self):
        p = self.planner(["arch/arm64/boot/dts/nvidia/refboard.dts"])
        rel = p.platform_relevance("a" * 40)
        self.assertEqual(rel["status"], "DIRECT")
        self.assertEqual(rel["support_strength"], "proxy")
        self.assertEqual(rel["matches"][0]["match_scope"], "reference_board_proxy")

    def test_makefile_only_is_not_excluded(self):
        p = self.planner(["arch/arm64/boot/dts/rockchip/Makefile"])
        rel = p.platform_relevance("a" * 40)
        self.assertEqual(rel["status"], "SHARED_OR_UNKNOWN")
        self.assertTrue(rel["excluded_matches"])

    def test_makefile_plus_excluded_board_is_excluded(self):
        p = self.planner(["arch/arm64/boot/dts/rockchip/Makefile",
                          "arch/arm64/boot/dts/rockchip/unsupported.dts"])
        self.assertEqual(p.platform_relevance("a" * 40)["status"], "EXCLUDED_BOARD")

    def test_makefile_plus_supported_board_is_not_excluded(self):
        p = self.planner(["arch/arm64/boot/dts/rockchip/Makefile",
                          "arch/arm64/boot/dts/rockchip/new-supported-board.dts"])
        self.assertEqual(p.platform_relevance("a" * 40)["status"], "UNMATCHED_BOARD")

    def test_kaanapali_scope_exclusion_from_shipped_profile(self):
        import yaml
        shipped = Path(__file__).resolve().parents[2] / "profiles/rhel-10-platforms.yaml"
        obj = yaml.safe_load(shipped.read_text())
        self.assertEqual(set(obj["active_product_scopes"]),
                         {"rhel10", "user_selected", "rhivos", "rhel_for_nvidia"})
        kaanapali = "arch/arm64/boot/dts/qcom/kaanapali.dtsi"
        makefile = "arch/arm64/boot/dts/qcom/Makefile"
        self.assertEqual(self.planner([kaanapali, makefile], obj)
                         .platform_relevance("a" * 40)["status"], "EXCLUDED_PLATFORM_ONLY")
        self.assertEqual(self.planner([makefile], obj)
                         .platform_relevance("a" * 40)["status"], "SHARED_OR_UNKNOWN")
        mixed = self.planner([kaanapali,
                              "arch/arm64/boot/dts/qcom/lemans.dtsi"], obj)
        self.assertNotIn(mixed.platform_relevance("a" * 40)["status"],
                         {"EXCLUDED_BOARD", "EXCLUDED_PLATFORM_ONLY"})

    def test_profile_ships_and_is_valid(self):
        import yaml
        release = Path(__file__).resolve().parents[2] / "profiles/rhel-10-platforms.yaml"
        self.assertTrue(release.is_file())
        parsed = self.load(yaml.safe_load(release.read_text()))
        self.assertEqual(len(parsed.platforms), 24)
        self.assertEqual(len(parsed.excluded_platforms), 30)
        self.assertEqual(parsed.version, 2)
        for name in ("AWS EC2 Graviton", "Microsoft Azure Cobalt"):
            guest = next(p for p in parsed.platforms if p.name.startswith(name))
            self.assertEqual(guest.support_basis, "supported_guest_rhel9_proxy")
        self.assertTrue(all(p.support_basis != "legacy_unspecified"
                            for p in parsed.platforms + parsed.excluded_platforms))
        azure = next(p for p in parsed.platforms if p.name.startswith("Microsoft Azure Cobalt"))
        self.assertNotIn("drivers/hv/*", azure.paths)

if __name__ == "__main__":
    unittest.main()
