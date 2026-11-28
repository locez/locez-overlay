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

from engine.builder import render_container_test_script, render_health_check_script, resolve_target_atom
from engine.cli import detect_toml_changed_packages, resolve_package_arches
from engine.common import get_local_version, version_key
from engine.context import _select_latest_ebuild
from engine.deps import (
    DEPS_PLACEHOLDER,
    _expand_ebuild_vars,
    deps_download_url,
    deps_ebuild_uri_template,
    deps_release_tag,
    deps_tarball_name,
    detect_deps_kind,
    ebuild_needs_deps_resolution,
    extract_rust_git_sources,
    parse_primary_src_uri,
    rewrite_ebuild_deps_metadata,
)
from engine.detect import filter_throttled_updates
from engine.notify import extract_log_excerpt, format_telegram_event
from engine.plugins import STRATEGIES
from engine.plugins.base import BumpContext
from engine.plugins.cn_apps import sign_wps_url
from engine.plugins.kernel import _extract_filesdir_patches
from engine.plugins.rust import parse_crates_from_ebuild
from engine.plugins.simple import bump_github_release
from engine.plugins.tencent import sign_tencent_qq_url
from engine.python_compat import (
    compute_target_compat,
    format_python_compat,
    parse_python_compat,
    parse_python_targets_desc,
)
from engine.repair import validate_allowed_changes
from engine.retention import ebuild_forbids_zfs, ebuild_supports_zfs, must_keep_for_zfs
from engine.schema import (
    AutomationConfig,
    ConfigError,
    ExtraDistSpec,
    OverlayConfig,
    PackageSpec,
    _parse_build_spec,
    _parse_retention_spec,
    generate_nvchecker_toml,
    load_config,
)


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
            expected = sum(1 for spec in self.cfg.packages.values() if spec.detect.get("type") == "nvchecker")
            self.assertEqual(len(tracked), expected)
            self.assertGreater(expected, 0)
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
                self.assertIn("-binpkg-request-signature", script)
                self.assertIn("buildpkg", script)
                self.assertIn("PROFILE=default/linux/amd64/23.0/desktop", script)
                self.assertIn('eselect profile set "${PROFILE}"', script)
                self.assertIn('aarch64) GENTOO_ARCH="arm64"; BINHOST_SUBARCH="arm64" ;;', script)
                self.assertIn('CONFIG_PROTECT="-*"', script)
                self.assertIn("parallel-install", script)
                self.assertIn("-merge-sync", script)
                self.assertNotIn("--binpkg-respect-use", script)
                self.assertNotIn('MAKEOPTS="-j$(nproc)"', script)
                if spec.build.mode != "dep-resolve":
                    self.assertIn('--jobs="${NPROC}" --load-average="${NPROC}"', script)
                    self.assertIn(f"--usepkg-exclude={pkg}", script)
                    self.assertIn(f"--buildpkg-exclude={pkg}", script)
                if spec.build.mode == "emerge-full" and spec.build.test:
                    self.assertIn(f"echo '{pkg}", script)
                    self.assertIn("test", script.split("/etc/portage/package.use/10-target")[0].splitlines()[-1])
                if spec.smoke_test_script:
                    self.assertIn(f"bash /overlay/{spec.smoke_test_script}", script)
                spath = Path(tmp) / f"{spec.slug}.sh"
                spath.write_text(script, encoding="utf-8")
                subprocess.run(["bash", "-n", str(spath)], check=True)
                modes.add(spec.build.mode)
        self.assertEqual(modes, {"emerge-full", "emerge-bin", "kernel-sources", "dep-resolve"})

    def test_python_target_single_mode_probe_and_epython(self) -> None:
        cfg = load_config(REPO_ROOT)
        spec = cfg.packages["app-misc/bilihud"]
        script = render_container_test_script(
            cfg, spec, atom="=app-misc/bilihud-9999", log_dir="/build-logs", python_target="python3_15"
        )
        self.assertIn("dev-python/* PYTHON_TARGETS: -* python3_15", script)
        self.assertIn("app-misc/bilihud PYTHON_TARGETS: -* python3_15", script)
        self.assertIn("export EPYTHON=python3.15", script)
        self.assertIn("falling back to additive PYTHON_TARGETS", script)
        with tempfile.TemporaryDirectory() as tmp:
            spath = Path(tmp) / "bilihud-py315.sh"
            spath.write_text(script, encoding="utf-8")
            subprocess.run(["bash", "-n", str(spath)], check=True)

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
        self.assertLess(version_key("0_pre9756"), version_key("0_pre12816"))

    def test_github_release_multi_dist_and_preserve_dists(self) -> None:
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdir = root / "cat" / "demo"
            pdir.mkdir(parents=True)
            (pdir / "demo-0_pre100.ebuild").write_text("EAPI=8\n", encoding="utf-8")
            keep_line = "DIST static-model-abc.gguf 42 BLAKE2B " + ("a" * 128) + " SHA512 " + ("b" * 128)
            old_line = "DIST demo-0_pre100.tar.gz 10 BLAKE2B " + ("0" * 128) + " SHA512 " + ("0" * 128)
            (pdir / "Manifest").write_text(f"{old_line}\n{keep_line}\n", encoding="utf-8")

            spec = PackageSpec(
                pkg="cat/demo",
                strategy="github-release",
                cadence="weekly",
                tag_ver_strip_prefix="0_pre",
                url_template="https://example.invalid/tags/b{tag_ver}.tar.gz",
                dist_template="{pn}-{ver}.tar.gz",
                extra_dists=(
                    ExtraDistSpec(
                        url_template="https://example.invalid/releases/b{tag_ver}/{pn}-b{tag_ver}-ui.tar.gz",
                        dist_template="{pn}-{ver}-ui.tar.gz",
                    ),
                ),
                preserve_dists=("static-model-*.gguf",),
            )
            cfg = AutomationConfig(
                repo_root=root,
                overlay=OverlayConfig(name="test", repository="org/test", base_branch="master", maintainer="me"),
                build=load_config(REPO_ROOT).build,
                notify=load_config(REPO_ROOT).notify,
                ai_repair=load_config(REPO_ROOT).ai_repair,
                packages={"cat/demo": spec},
            )
            work = root / "work"
            work.mkdir()
            fetched_urls: list[str] = []

            def fake_download(url: str, dest: Path) -> Path:
                fetched_urls.append(url)
                dest.write_bytes(url.encode("utf-8"))
                return dest

            ctx = BumpContext(cfg=cfg, spec=spec, target_ver="0_pre200", work=work)
            with patch("engine.plugins.simple.download_file", side_effect=fake_download):
                bump_github_release(ctx)

            self.assertEqual(
                fetched_urls,
                [
                    "https://example.invalid/tags/b200.tar.gz",
                    "https://example.invalid/releases/b200/demo-b200-ui.tar.gz",
                ],
            )
            self.assertFalse((pdir / "demo-0_pre100.ebuild").exists())
            self.assertTrue((pdir / "demo-0_pre200.ebuild").exists())
            manifest = (pdir / "Manifest").read_text(encoding="utf-8")
            self.assertIn("DIST demo-0_pre200.tar.gz ", manifest)
            self.assertIn("DIST demo-0_pre200-ui.tar.gz ", manifest)
            self.assertIn(keep_line, manifest)
            self.assertNotIn("demo-0_pre100.tar.gz", manifest)

    def test_generate_nvchecker_toml_respects_only_pkgs_filter(self) -> None:
        cfg = load_config(REPO_ROOT)
        with tempfile.TemporaryDirectory() as tmp:
            weekly = {pkg for pkg, s in cfg.packages.items() if s.cadence == "weekly"}
            self.assertIn("sci-ml/llama-cpp", weekly)
            daily_nv = {
                pkg
                for pkg, s in cfg.packages.items()
                if s.detect.get("type") == "nvchecker" and s.cadence != "weekly"
            }
            out = generate_nvchecker_toml(
                cfg,
                Path(tmp) / "nv.toml",
                Path(tmp) / "old.json",
                Path(tmp) / "new.json",
                only_pkgs=daily_nv,
            )
            data = tomllib.loads(out.read_text(encoding="utf-8"))
            self.assertNotIn("sci-ml/llama-cpp", data)
            self.assertIn("media-sound/cider", data)


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

    def test_log_excerpt_ansi_stripping_and_throttle_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.log"
            log = Path(tmp) / "build.log"
            log.write_text("\x1b[31mERROR:\x1b[0m pytest failed\nline2\n", encoding="utf-8")
            excerpt = extract_log_excerpt([missing, log], max_lines=5)
            self.assertEqual(excerpt, "ERROR: pytest failed\nline2")

        updates = [
            {"pkg": "app-office/wps-office", "upstream_ver": "12.1.2.30000"},
            {"pkg": "app-office/wps-office", "upstream_ver": "12.1.2.30001"},
            {"pkg": "net-im/dingtalk", "upstream_ver": "9.0.1"},
        ]
        open_titles = [
            "[autobump-failed] app-office/wps-office: 12.1.2.29183 -> 12.1.2.30000 (build-test failed)",
        ]
        kept, throttled = filter_throttled_updates(updates, open_titles, "autobump-failed")
        self.assertEqual([u["upstream_ver"] for u in kept], ["12.1.2.30001", "9.0.1"])
        self.assertEqual([u["upstream_ver"] for u in throttled], ["12.1.2.30000"])

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
        cfg = load_config(REPO_ROOT)
        expected_live = sorted(pkg for pkg, spec in cfg.packages.items() if spec.weekly_live_test)
        self.assertEqual([x["pkg"] for x in live], expected_live)


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


class TestDistfilesAndDeps(unittest.TestCase):
    def test_distfiles_repo_and_strategies(self) -> None:
        cfg = load_config(REPO_ROOT)
        self.assertEqual(cfg.overlay.effective_distfiles_repo, "locez/locez-overlay-distfiles")
        self.assertIn("go-vendor", STRATEGIES)
        self.assertIn("rust-vendor", STRATEGIES)
        self.assertIn("rust-crates", STRATEGIES)
        self.assertEqual(deps_release_tag("net-misc/tsshd", "0.1.10"), "net-misc-tsshd-0.1.10")
        self.assertEqual(deps_tarball_name("tsshd", "0.1.10"), "tsshd-0.1.10-deps.tar.xz")
        self.assertEqual(
            deps_download_url("locez/locez-overlay-distfiles", "net-misc/tsshd", "0.1.10"),
            "https://github.com/locez/locez-overlay-distfiles/releases/download/net-misc-tsshd-0.1.10/tsshd-0.1.10-deps.tar.xz",
        )
        self.assertEqual(
            deps_ebuild_uri_template("locez/locez-overlay-distfiles"),
            "https://github.com/locez/locez-overlay-distfiles/releases/download/${CATEGORY}-${PN}-${PV}/${P}-deps.tar.xz",
        )

    def test_extract_rust_git_sources(self) -> None:
        pure = """
[source.crates-io]
replace-with = "vendored-sources"

[source.vendored-sources]
directory = "/tmp/vendor"
"""
        self.assertEqual(extract_rust_git_sources(pure), "")

        with_git = """
[source.crates-io]
replace-with = "vendored-sources"

[source."git+https://github.com/Smithay/smithay?rev=812bd33"]
git = "https://github.com/Smithay/smithay"
rev = "812bd33"
replace-with = "vendored-sources"

[source.vendored-sources]
directory = "/tmp/vendor"
"""
        extracted = extract_rust_git_sources(with_git)
        self.assertIn('[source."git+https://github.com/Smithay/smithay?rev=812bd33"]', extracted)
        self.assertIn('replace-with = "gentoo"', extracted)
        self.assertNotIn("crates-io", extracted)
        self.assertNotIn("vendored-sources", extracted)

    def test_placeholder_detection_and_primary_src_uri_parsing(self) -> None:
        eb = f"""
EAPI=8
inherit cargo
SRC_URI="
\thttps://github.com/owner/${{PN}}/archive/refs/tags/v${{PV}}.tar.gz -> ${{P}}.tar.gz
\t{DEPS_PLACEHOLDER}
"
"""
        self.assertEqual(detect_deps_kind(eb), "rust")
        self.assertTrue(
            ebuild_needs_deps_resolution(eb, "locez/locez-overlay-distfiles", {}, "demo", "1.2.3")
        )
        self.assertEqual(
            parse_primary_src_uri(eb, "app-misc/demo", "1.2.3", "locez/locez-overlay-distfiles"),
            ("https://github.com/owner/demo/archive/refs/tags/v1.2.3.tar.gz", "demo-1.2.3.tar.gz"),
        )
        resolved_eb = eb.replace(DEPS_PLACEHOLDER, deps_ebuild_uri_template("locez/locez-overlay-distfiles"))
        self.assertFalse(
            ebuild_needs_deps_resolution(
                resolved_eb,
                "locez/locez-overlay-distfiles",
                {"demo-1.2.3-deps.tar.xz": "DIST ..."},
                "demo",
                "1.2.3",
            )
        )
        self.assertTrue(
            ebuild_needs_deps_resolution(resolved_eb, "locez/locez-overlay-distfiles", {}, "demo", "1.2.3")
        )

    def test_expand_vars_prefix_safety_rewrite_and_crates_parser(self) -> None:
        eb = 'MY_P="${PN}-v${PV}"\nMY_PV="v${PV}"\n'
        # Single-pass regex must not corrupt $MY_PV using $MY_P or $PN using $P
        self.assertEqual(
            _expand_ebuild_vars("https://example.com/$MY_PV/$MY_P-$PN-$P.tar.gz", "app-misc/demo", "1.2.3", eb),
            "https://example.com/v1.2.3/demo-v1.2.3-demo-demo-1.2.3.tar.gz",
        )
        rewritten = rewrite_ebuild_deps_metadata(
            f'RUST_MIN_VER="1.80.0"\nSRC_URI="\n\t{DEPS_PLACEHOLDER}\n"\n',
            kind="rust",
            distfiles_repo="locez/locez-overlay-distfiles",
            min_toolchain_ver="1.85.0",
        )
        self.assertIn('RUST_MIN_VER="1.85.0"', rewritten)
        self.assertIn(deps_ebuild_uri_template("locez/locez-overlay-distfiles"), rewritten)
        self.assertNotIn(DEPS_PLACEHOLDER, rewritten)

        crates = parse_crates_from_ebuild('CRATES="\n\tserde@1.0.229\n\ttokio@1.45.0\n"\n')
        self.assertEqual(crates, [("serde", "1.0.229"), ("tokio", "1.45.0")])

    def test_direct_upstream_signing_helpers(self) -> None:
        from unittest.mock import MagicMock, patch

        wps_signed = sign_wps_url(
            "https://wps-linux-personal.wpscdn.cn/wps/download/ep/Linux2023/29183/"
            "wps-office_12.1.2.29183.AK.preread.sw.Personal_806791_amd64.deb",
            now=1795854806,
        )
        self.assertEqual(
            wps_signed,
            "https://wps-linux-personal.wpscdn.cn/wps/download/ep/Linux2023/29183/"
            "wps-office_12.1.2.29183.AK.preread.sw.Personal_806791_amd64.deb"
            "?t=1795854806&k=75aa665471fa885e34931750f1fcb839",
        )

        fake_resp = MagicMock()
        fake_resp.__enter__.return_value = fake_resp
        fake_resp.read.return_value = json.dumps(
            {
                "retcode": 0,
                "data": {
                    "url": "https://qqdl.gtimg.cn/qqfile/QQNTV2/test.deb?sign=abc123&t=1795855379",
                },
            }
        ).encode("utf-8")
        with patch("urllib.request.urlopen", return_value=fake_resp):
            qq_signed = sign_tencent_qq_url("https://qqdl.gtimg.cn/qqfile/QQNTV2/test.deb")
        self.assertEqual(qq_signed, "https://qqdl.gtimg.cn/qqfile/QQNTV2/test.deb?sign=abc123&t=1795855379")

    def test_health_script_valid_bash_and_package_registration_drift(self) -> None:
        cfg = load_config(REPO_ROOT)
        script = render_health_check_script(cfg, overlay_mount="/overlay", log_dir="/health-logs")
        self.assertIn("OVERLAY_HEALTH_PASSED", script)
        self.assertIn("https://overlay.locez.com/healthz", script)
        for pkg in cfg.packages:
            self.assertIn(pkg, script)
        with tempfile.TemporaryDirectory() as tmp:
            spath = Path(tmp) / "overlay-health.sh"
            spath.write_text(script, encoding="utf-8")
            subprocess.run(["bash", "-n", str(spath)], check=True)

        # Ensure every ebuild package directory in repo is either registered in metadata/autobump.toml
        # or explicitly listed in health.untracked_packages (prevents silent CI bypass).
        disk_pkgs: set[str] = set()
        for ebuild in REPO_ROOT.glob("*/*/*.ebuild"):
            rel = ebuild.relative_to(REPO_ROOT)
            if rel.parts[0].startswith("."):
                continue
            disk_pkgs.add(f"{rel.parts[0]}/{rel.parts[1]}")
        tracked = set(cfg.packages) | set(cfg.health.untracked_packages)
        self.assertEqual(disk_pkgs - tracked, set(), f"Unregistered packages found: {sorted(disk_pkgs - tracked)}")
        self.assertEqual(set(cfg.packages) - disk_pkgs, set(), f"Stale entries in autobump.toml: {sorted(set(cfg.packages) - disk_pkgs)}")

    def test_detect_toml_changed_packages(self) -> None:
        old_toml = """
[packages."app-i18n/rime-ice"]
build = { mode = "emerge-full", use = ["lua"] }

[packages."media-sound/cider"]
build = { mode = "emerge-bin" }
"""
        new_toml = """
[packages."app-i18n/rime-ice"]
build = { mode = "emerge-full", use = ["-lua"] }

[packages."media-sound/cider"]
build = { mode = "emerge-bin" }

[packages."net-misc/newpkg"]
build = { mode = "emerge-full" }
"""
        self.assertEqual(
            detect_toml_changed_packages(old_toml, new_toml),
            {"app-i18n/rime-ice", "net-misc/newpkg"},
        )

    def test_resolve_package_arches(self) -> None:
        cfg = load_config(REPO_ROOT)
        self.assertEqual(resolve_package_arches(cfg, "net-im/tencent-qq"), ["x64", "arm64"])
        self.assertEqual(resolve_package_arches(cfg, "app-office/wps-office"), ["x64"])
        self.assertEqual(resolve_package_arches(cfg, "net-misc/tsshd"), ["x64", "arm64"])
        self.assertEqual(resolve_package_arches(cfg, "app-misc/bilihud", live=True), ["x64", "arm64"])
        self.assertEqual(resolve_package_arches(cfg, "sci-ml/llama-cpp", live=True), ["x64"])


if __name__ == "__main__":
    unittest.main()
