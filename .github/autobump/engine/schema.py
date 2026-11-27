"""Strongly-typed declarative configuration loader for metadata/autobump.toml."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

BuildMode = Literal["emerge-full", "emerge-bin", "kernel-sources", "dep-resolve"]
MergePolicy = Literal["auto-merge", "pr-only", "issue-only"]
RetentionType = Literal["replace", "keep-latest"]
RetentionGuard = Literal["none", "keep-last-zfs-compatible"]
DetectType = Literal["none", "nvchecker", "wechat-head"]

VALID_BUILD_MODES: frozenset[str] = frozenset({"emerge-full", "emerge-bin", "kernel-sources", "dep-resolve"})
VALID_POLICIES: frozenset[str] = frozenset({"auto-merge", "pr-only", "issue-only"})
VALID_RETENTION_TYPES: frozenset[str] = frozenset({"replace", "keep-latest"})
VALID_RETENTION_GUARDS: frozenset[str] = frozenset({"none", "keep-last-zfs-compatible"})
VALID_DETECT_TYPES: frozenset[str] = frozenset({"none", "nvchecker", "wechat-head"})
VALID_TELEGRAM_EVENTS: frozenset[str] = frozenset({"failed", "ai_draft_pr", "zfs_unlocked", "health_alert", "merged"})


class ConfigError(ValueError):
    """Raised when metadata/autobump.toml contains invalid configuration."""


@dataclass(frozen=True)
class OverlayConfig:
    name: str
    repository: str
    base_branch: str
    maintainer: str


@dataclass(frozen=True)
class BuildDefaults:
    stage3_image: str = "docker.io/gentoo/stage3:latest"
    profile: str = "default/linux/amd64/23.0/desktop"
    use_binhost: bool = True
    binhost_uri: str = "https://distfiles.gentoo.org/releases/amd64/binpackages/23.0/x86-64/"


@dataclass(frozen=True)
class NotifyConfig:
    github_issue: bool = True
    github_label: str = "autobump-failed"
    telegram: bool = True
    telegram_events: tuple[str, ...] = ("failed", "ai_draft_pr", "zfs_unlocked", "health_alert")


@dataclass(frozen=True)
class AIRepairConfig:
    enabled: bool = False
    mode: str = "draft-pr"
    max_attempts: int = 1
    allowed_extra_paths: tuple[str, ...] = ("licenses/",)
    command: str = ""


@dataclass(frozen=True)
class BuildSpec:
    mode: BuildMode = "dep-resolve"
    use: tuple[str, ...] = ()
    use_sets: tuple[tuple[str, ...], ...] = ()
    extra_pkgs: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    test: bool = False
    check_kconfig: bool = False

    def effective_use_sets(self) -> tuple[tuple[str, ...], ...]:
        if self.use_sets:
            return self.use_sets
        if self.use:
            return (self.use,)
        return ((),)


@dataclass(frozen=True)
class RetentionSpec:
    type: RetentionType = "replace"
    count: int = 1
    guard: RetentionGuard = "none"


@dataclass(frozen=True)
class PackageSpec:
    pkg: str
    detect: dict[str, Any] = field(default_factory=dict)
    strategy: str = ""
    retention: RetentionSpec = field(default_factory=RetentionSpec)
    policy: MergePolicy = "pr-only"
    build: BuildSpec = field(default_factory=BuildSpec)
    smoke_test: str = ""
    smoke_test_script: str = ""
    url_template: str = ""
    dist_template: str = ""
    go_build_target: str = ""
    weekly_live_test: bool = False

    @property
    def slug(self) -> str:
        return self.pkg.replace("/", "-")

    @property
    def category(self) -> str:
        return self.pkg.split("/", 1)[0]

    @property
    def pn(self) -> str:
        return self.pkg.split("/", 1)[1]


@dataclass(frozen=True)
class AutomationConfig:
    repo_root: Path
    overlay: OverlayConfig
    build: BuildDefaults
    notify: NotifyConfig
    ai_repair: AIRepairConfig
    packages: dict[str, PackageSpec]

    def get_package(self, pkg: str) -> PackageSpec:
        if pkg not in self.packages:
            raise KeyError(f"Package '{pkg}' is not declared in metadata/autobump.toml")
        return self.packages[pkg]


def find_repo_root(start: Path | None = None) -> Path:
    cur = (start or Path.cwd()).resolve()
    for candidate in (cur, *cur.parents):
        if (candidate / "metadata" / "autobump.toml").is_file():
            return candidate
    fallback = Path(__file__).resolve().parents[3]
    if (fallback / "metadata" / "autobump.toml").is_file():
        return fallback
    raise FileNotFoundError("Could not locate metadata/autobump.toml in repository")


def _parse_build_spec(pkg: str, raw: dict[str, Any]) -> BuildSpec:
    mode = raw.get("mode", "dep-resolve")
    if mode not in VALID_BUILD_MODES:
        raise ConfigError(f"{pkg}: invalid build.mode '{mode}' (expected one of {sorted(VALID_BUILD_MODES)})")
    use = tuple(str(u) for u in raw.get("use", ()))
    use_sets = tuple(tuple(str(u) for u in s) for s in raw.get("use_sets", ()))
    extra_pkgs = tuple(str(p) for p in raw.get("extra_pkgs", ()))
    raw_env = raw.get("env", {})
    if not isinstance(raw_env, dict):
        raise ConfigError(f"{pkg}: build.env must be a table of KEY = VALUE strings")
    env = {str(k): str(v) for k, v in raw_env.items()}
    return BuildSpec(
        mode=mode,
        use=use,
        use_sets=use_sets,
        extra_pkgs=extra_pkgs,
        env=env,
        test=bool(raw.get("test", False)),
        check_kconfig=bool(raw.get("check_kconfig", False)),
    )


def _parse_retention_spec(pkg: str, raw: dict[str, Any]) -> RetentionSpec:
    rtype = raw.get("type", "replace")
    if rtype not in VALID_RETENTION_TYPES:
        raise ConfigError(f"{pkg}: invalid retention.type '{rtype}'")
    guard = raw.get("guard", "none")
    if guard not in VALID_RETENTION_GUARDS:
        raise ConfigError(f"{pkg}: invalid retention.guard '{guard}'")
    count = int(raw.get("count", 1))
    if count < 1:
        raise ConfigError(f"{pkg}: retention.count must be >= 1, got {count}")
    return RetentionSpec(type=rtype, count=count, guard=guard)


def load_config(repo_root: Path | None = None) -> AutomationConfig:
    root = find_repo_root(repo_root)
    raw = tomllib.loads((root / "metadata" / "autobump.toml").read_text(encoding="utf-8"))

    ov = raw.get("overlay", {})
    repo_name_file = root / "profiles" / "repo_name"
    overlay_name = ov.get("name") or (repo_name_file.read_text(encoding="utf-8").strip() if repo_name_file.is_file() else "")
    if not overlay_name:
        raise ConfigError("overlay.name is missing and profiles/repo_name does not exist")
    overlay = OverlayConfig(
        name=overlay_name,
        repository=ov.get("repository", ""),
        base_branch=ov.get("base_branch", "master"),
        maintainer=ov.get("maintainer", ""),
    )

    bd = raw.get("build", {})
    build = BuildDefaults(
        stage3_image=bd.get("stage3_image", BuildDefaults.stage3_image),
        profile=bd.get("profile", BuildDefaults.profile),
        use_binhost=bool(bd.get("use_binhost", True)),
        binhost_uri=bd.get("binhost_uri", BuildDefaults.binhost_uri),
    )

    nt = raw.get("notify", {})
    tg_events = tuple(nt.get("telegram_events", NotifyConfig.telegram_events))
    unknown_events = set(tg_events) - VALID_TELEGRAM_EVENTS
    if unknown_events:
        raise ConfigError(f"notify.telegram_events contains unknown events: {sorted(unknown_events)}")
    notify = NotifyConfig(
        github_issue=bool(nt.get("github_issue", True)),
        github_label=nt.get("github_label", "autobump-failed"),
        telegram=bool(nt.get("telegram", True)),
        telegram_events=tg_events,
    )

    ai = raw.get("ai_repair", {})
    ai_repair = AIRepairConfig(
        enabled=bool(ai.get("enabled", False)),
        mode=ai.get("mode", "draft-pr"),
        max_attempts=int(ai.get("max_attempts", 1)),
        allowed_extra_paths=tuple(ai.get("allowed_extra_paths", ("licenses/",))),
        command=ai.get("command", ""),
    )

    raw_packages: dict[str, dict[str, Any]] = {
        pkg: dict(pdata) for pkg, pdata in raw.get("packages", {}).items()
    }
    # Optional drop-in package configs under metadata/autobump.d/**/*.toml (prevents merge conflicts in large overlays)
    dropin_dir = root / "metadata" / "autobump.d"
    if dropin_dir.is_dir():
        for dropin in sorted(dropin_dir.rglob("*.toml")):
            d_raw = tomllib.loads(dropin.read_text(encoding="utf-8"))
            if "packages" in d_raw and isinstance(d_raw["packages"], dict):
                for pkg, pdata in d_raw["packages"].items():
                    raw_packages[pkg] = dict(pdata)
            elif "pkg" in d_raw:
                pkg = str(d_raw["pkg"])
                raw_packages[pkg] = {k: v for k, v in d_raw.items() if k != "pkg"}

    packages: dict[str, PackageSpec] = {}
    for pkg, pdata in sorted(raw_packages.items()):
        if "/" not in pkg:
            raise ConfigError(f"Invalid package atom '{pkg}': expected 'category/name'")
        detect = dict(pdata.get("detect", {"type": "none"}))
        dtype = detect.get("type", "none")
        if dtype not in VALID_DETECT_TYPES:
            raise ConfigError(f"{pkg}: invalid detect.type '{dtype}'")
        policy = pdata.get("policy", "pr-only")
        if policy not in VALID_POLICIES:
            raise ConfigError(f"{pkg}: invalid policy '{policy}'")

        smoke_test = pdata.get("smoke_test", "").strip()
        if "\n" in smoke_test:
            raise ConfigError(
                f"{pkg}: multi-line inline smoke_test is disallowed; use smoke_test_script instead"
            )
        smoke_script = str(pdata.get("smoke_test_script", "")).strip()
        if smoke_script:
            if not (root / smoke_script).is_file():
                raise ConfigError(f"{pkg}: smoke_test_script '{smoke_script}' does not exist")
        else:
            # Convention over configuration: auto-discover .github/smoke/<cat>/<pn>.sh if present
            convention = f".github/smoke/{pkg}.sh"
            if (root / convention).is_file():
                smoke_script = convention

        packages[pkg] = PackageSpec(
            pkg=pkg,
            detect=detect,
            strategy=pdata.get("strategy", ""),
            retention=_parse_retention_spec(pkg, dict(pdata.get("retention", {}))),
            policy=policy,
            build=_parse_build_spec(pkg, dict(pdata.get("build", {}))),
            smoke_test=smoke_test,
            smoke_test_script=smoke_script,
            url_template=pdata.get("url_template", ""),
            dist_template=pdata.get("dist_template", ""),
            go_build_target=pdata.get("go_build_target", ""),
            weekly_live_test=bool(pdata.get("weekly_live_test", False)),
        )

    return AutomationConfig(
        repo_root=root,
        overlay=overlay,
        build=build,
        notify=notify,
        ai_repair=ai_repair,
        packages=packages,
    )


def generate_nvchecker_toml(cfg: AutomationConfig, dest: Path, oldver: Path, newver: Path) -> Path:
    """Materialize a runtime nvchecker.toml from metadata/autobump.toml (single source of truth)."""

    def _toml_val(val: Any) -> str:
        if isinstance(val, bool):
            return "true" if val else "false"
        if isinstance(val, (int, float)):
            return str(val)
        if isinstance(val, str):
            escaped = val.replace("\\", "\\\\").replace('"', '\\"')
            return f'"{escaped}"'
        raise TypeError(f"Unsupported nvchecker TOML value: {val!r}")

    lines = [
        "[__config__]",
        f"oldver = {_toml_val(str(oldver))}",
        f"newver = {_toml_val(str(newver))}",
        "",
    ]
    for pkg, spec in sorted(cfg.packages.items()):
        if spec.detect.get("type") != "nvchecker":
            continue
        lines.append(f'["{pkg}"]')
        for k, v in spec.detect.items():
            if k == "type":
                continue
            lines.append(f"{k} = {_toml_val(v)}")
        lines.append("")

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(lines), encoding="utf-8")
    return dest
