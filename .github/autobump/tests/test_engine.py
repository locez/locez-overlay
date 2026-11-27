"""Zero-dependency unit test suite for the overlay-agnostic autobump engine."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

AUTOBUMP_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(AUTOBUMP_DIR) not in sys.path:
    sys.path.insert(0, str(AUTOBUMP_DIR))

from engine.builder import render_container_test_script, resolve_target_atom
from engine.common import get_local_version, version_key
from engine.context import _select_latest_ebuild
from engine.notify import format_telegram_event
from engine.plugins import STRATEGIES
from engine.plugins.kernel import _extract_filesdir_patches
from engine.python_compat import (
    compute_target_compat,
    format_python_compat,
    parse_python_compat,
    parse_python_targets_desc,
)
from engine.repair import validate_allowed_changes
from engine.retention import ebuild_forbids_zfs, ebuild_supports_zfs, must_keep_for_zfs
from engine.schema import ConfigError, _parse_build_spec, _parse_retention_spec, generate_nvchecker_toml, load_config


class TestSchemaAndConfig(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = load_config(REPO_ROOT)

    def test_load_config_and_strategies(self) -> None:
        self.assertEqual(self.cfg.overlay.name, "locez")
        self.assertGreaterEqual(len(self.cfg.packages), 19)
        for pkg, spec in self.cfg.packages.items():
            if spec.strategy:
                self.assertIn(spec.strategy, STRATEGIES, f"Unknown strategy for {pkg}")
            if spec.detect.get("type") != "none":
                self.assertIsNotNone(
                    get_local_version(self.cfg.repo_root, pkg, spec.strategy),
                    f"Missing local version for {pkg}",
                )

    def test_invalid_config_rejected_eagerly(self) -> None:
        with self.assertRaises(ConfigError):
            _parse_build_spec("cat/pkg", {"mode": "invalid-mode"})
        with self.assertRaises(ConfigError):
            _parse_retention_spec("cat/pkg", {"type": "invalid"})
        with self.assertRaises(ConfigError):
            _parse_retention_spec("cat/pkg", {"type": "keep-latest", "count": 0})
        with self.assertRaises(ConfigError):
            _parse_retention_spec("cat/pkg", {"guard": "unknown-guard"})

    def test_generate_nvchecker_toml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = generate_nvchecker_toml(
                self.cfg, Path(tmp) / "nv.toml", Path(tmp) / "old.json", Path(tmp) / "new.json"
            )
            data = tomllib.loads(out.read_text(encoding="utf-8"))
            tracked = [k for k in data if k != "__config__"]
            self.assertEqual(len(tracked), 13)
            self.assertIn("media-sound/cider", tracked)
            self.assertIn("net-im/tencent-qq", tracked)


class TestBuilderScripts(unittest.TestCase):
    def test_all_package_build_scripts_valid_bash(self) -> None:
        cfg = load_config(REPO_ROOT)
        modes: set[str] = set()
        with tempfile.TemporaryDirectory() as tmp:
            for pkg, spec in cfg.packages.items():
                atom = resolve_target_atom(cfg, pkg)
                script = render_container_test_script(cfg, spec, atom=atom, log_dir="/build-logs")
                self.assertIn("<<'MAKECONF'", script)
                self.assertIn("PORTAGE_LOGDIR=/build-logs", script)
                self.assertIn("getuto", script)
                self.assertNotIn('MAKEOPTS="-j$(nproc)"', script)
                if spec.smoke_test_script:
                    self.assertIn(f"bash /overlay/{spec.smoke_test_script}", script)
                spath = Path(tmp) / f"{spec.slug}.sh"
                spath.write_text(script, encoding="utf-8")
                subprocess.run(["bash", "-n", str(spath)], check=True)
                modes.add(spec.build.mode)
        self.assertEqual(modes, {"emerge-full", "emerge-bin", "kernel-sources", "dep-resolve"})

    def test_smoke_scripts_valid_bash(self) -> None:
        smoke_dir = REPO_ROOT / ".github" / "smoke"
        scripts = sorted(smoke_dir.rglob("*.sh"))
        self.assertGreaterEqual(len(scripts), 1)
        for s in scripts:
            subprocess.run(["bash", "-n", str(s)], check=True)


class TestRetentionAndKernelHelpers(unittest.TestCase):
    def test_zfs_guard_keeps_last_compatible_kernel(self) -> None:
        eb_736 = REPO_ROOT / "sys-kernel/xanmod-sources/xanmod-sources-7.3.6.ebuild"
        eb_725 = REPO_ROOT / "sys-kernel/xanmod-sources/xanmod-sources-7.2.5.ebuild"
        self.assertTrue(ebuild_forbids_zfs(eb_736.read_text(encoding="utf-8")))
        self.assertFalse(ebuild_forbids_zfs(eb_725.read_text(encoding="utf-8")))
        self.assertTrue(ebuild_supports_zfs("7.2.5", eb_725.read_text(encoding="utf-8"), "7.2"))
        self.assertFalse(ebuild_supports_zfs("7.3.6", eb_736.read_text(encoding="utf-8"), "7.2"))

        # Dropping 7.3.6 when 7.2.5 exists -> safe (keep=False)
        self.assertFalse(must_keep_for_zfs(REPO_ROOT, "sys-kernel/xanmod-sources", eb_736, "7.3.7", eb_736.read_text(), "7.2"))
        # Dropping 7.2.5 for a !zfs 7.3.7 -> MUST KEEP 7.2.5 (keep=True)
        self.assertTrue(must_keep_for_zfs(REPO_ROOT, "sys-kernel/xanmod-sources", eb_725, "7.3.7", eb_736.read_text(), "7.2"))
        # Replacing 7.2.5 with a ZFS-compatible 7.2.15 -> safe (keep=False)
        self.assertFalse(must_keep_for_zfs(REPO_ROOT, "sys-kernel/xanmod-sources", eb_725, "7.2.15", eb_725.read_text(), "7.2"))

    def test_extract_filesdir_patches(self) -> None:
        pdir = REPO_ROOT / "sys-kernel/xanmod-sources"
        eb = pdir / "xanmod-sources-7.3.6.ebuild"
        patches = _extract_filesdir_patches(eb.read_text(encoding="utf-8"), pdir, "xanmod-sources", "7.3.6")
        self.assertEqual([p.name for p in patches], ["xanmod-sources-7.2-x86-native-cache-shift.patch"])

    def test_version_key_ordering(self) -> None:
        self.assertLess(version_key("7.2.5"), version_key("7.2.15"))
        self.assertLess(version_key("11.0_p9"), version_key("11.0_p17"))
        self.assertLess(version_key("4.0.9.1"), version_key("4.0.17"))


class TestGuardrailsContextAndNotify(unittest.TestCase):
    def test_ai_repair_scope_guardrail(self) -> None:
        self.assertEqual(
            validate_allowed_changes(["net-im/wechat/wechat-4.1.14.ebuild", "licenses/Tencent-Weixin"], "net-im/wechat", ("licenses/",)),
            [],
        )
        self.assertEqual(
            validate_allowed_changes(["net-im/wechat/Manifest", ".github/workflows/autobump.yml"], "net-im/wechat", ("licenses/",)),
            [".github/workflows/autobump.yml"],
        )

    def test_select_latest_ebuild_uses_version_order(self) -> None:
        latest = _select_latest_ebuild(REPO_ROOT, "app-emulation/proton-ge-bin")
        self.assertIsNotNone(latest)
        self.assertEqual(latest.name, "proton-ge-bin-11.14.ebuild")

    def test_telegram_html_escaping(self) -> None:
        msg = format_telegram_event(
            overlay_name="locez",
            event="failed",
            pkg="net-im/wechat",
            title="4.1 -> 4.2",
            detail="<tag> & error",
            url="https://example.com/?a=1&b=2",
        )
        self.assertIn("&lt;tag&gt; &amp; error", msg)
        self.assertIn("https://example.com/?a=1&amp;b=2", msg)

    def test_cli_config_info_and_live_packages(self) -> None:
        info = json.loads(
            subprocess.check_output(
                [sys.executable, str(AUTOBUMP_DIR / "main.py"), "config-info"],
                text=True,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
        )
        self.assertEqual(info["overlay"]["name"], "locez")
        live = json.loads(
            subprocess.check_output(
                [sys.executable, str(AUTOBUMP_DIR / "main.py"), "live-packages"],
                text=True,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
        )
        self.assertEqual([x["pkg"] for x in live], ["app-i18n/rime-ice", "app-misc/bilihud", "media-plugins/kotonoha"])


class TestPythonCompatSync(unittest.TestCase):
    def test_parse_targets_and_compute_updates(self) -> None:
        desc = """
        # comment
        pypy3_12 - Build with PyPy 3.12
        python3_12 - Build with Python 3.12
        python3_13 - Build with Python 3.13
        python3_14 - Build with Python 3.14
        python3_14t - Build with Python 3.14 freethreading
        python3_15 - Build with Python 3.15
        """
        active, pypys = parse_python_targets_desc(desc)
        self.assertEqual(active, [12, 13, 14, 15])
        self.assertEqual(pypys, {"pypy3_12"})

        # Preserves intentional floor (13) while bumping max (14 -> 15)
        parsed = parse_python_compat("PYTHON_COMPAT=( python3_{13..14} )\n")
        self.assertEqual(parsed, ([13, 14], []))
        new_m, new_o = compute_target_compat([13, 14], active, [], pypys)
        self.assertEqual(format_python_compat(new_m, new_o), "PYTHON_COMPAT=( python3_{13..15} )")

        # Drops EOL python3_11 and EOL pypy3_11 automatically
        new_m2, new_o2 = compute_target_compat([11, 12, 13], [12, 13, 14, 15], ["pypy3_11", "pypy3_12"], pypys)
        self.assertEqual(format_python_compat(new_m2, new_o2), "PYTHON_COMPAT=( python3_{12..15} pypy3_12 )")


if __name__ == "__main__":
    unittest.main()
