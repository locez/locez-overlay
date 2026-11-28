# AGENTS.md — `locez-overlay` Contributor & CI/CD Guide

This document is the primary operating manual for human maintainers and AI coding agents contributing to `locez-overlay` (a self-maintained Gentoo Portage overlay). Follow every invariant below when adding/updating packages, writing ebuilds, or modifying CI/automation.

---

## 1. Repository Boundaries & Layout

- **Overlay identity**: `locez` (`profiles/repo_name = locez`, `masters = gentoo`, `thin-manifests = true`, `manifest-hashes = BLAKE2B SHA512` in `metadata/layout.conf`).
- **Hard Portage directory rule**: **NEVER** place non-Portage files (`.toml`, `.sh`, notes, scripts, `__pycache__`) inside `<category>/<package>/` directories. `pkgcheck` fails immediately on stray files.
- **Directory map**:
  - `<category>/<package>/` — Gentoo ebuilds, thin `Manifest` (`DIST` entries only), `metadata.xml`, optional `files/`.
  - `licenses/` — Custom licenses absent from main `gentoo` tree (e.g. `Tencent-QQ`, `Tencent-Weixin`, `QQMusic-EULA`).
  - `metadata/autobump.toml` — Single declarative source of truth for all package automation, build/test modes, retention policies, and notifications.
  - `.github/smoke/<category>/<pn>.sh` — Multi-line integration smoke-test scripts (shared helpers in `.github/smoke/_shared/`).
  - `.github/autobump/engine/` — 100% overlay-agnostic Python automation engine (stdlib only, Python 3.11+).
  - `.github/autobump/engine/plugins/` — Registered bump strategies (`pypi`, `github-release`, `cider`, `proton-ge`, `dwproton`, `tencent-qq`, `qqmusic`, `wechat`, `feishu`, `dingtalk`, `wps-office`, `go-vendor`, `rust-vendor`, `rust-crates`, `zig-vendor`, `kernel-xanmod`, `kernel-cachyos`).
  - `overlay.distfiles_repo` (`locez/locez-overlay-distfiles`) — Dedicated public GitHub repo hosting Go/Rust `${P}-deps.tar.xz` tarballs under release tag `${CATEGORY}-${PN}-${PV}`.
  - `.github/autobump/tests/test_engine.py` — Zero-dependency `unittest` suite.
  - `.github/actions/gentoo-build-test/action.yml` — Reusable Composite Action running real `gentoo/stage3` builds + smoke tests with daily Portage tree cache and weekly CI binpkg cache.
  - `.github/workflows/`:
    - `pkgcheck.yml` — Unit tests + `pkgcheck --net` on push/PR/weekly.
    - `build-test.yml` — Real `gentoo/stage3` build & smoke-test for changed packages on PRs and direct pushes to `master` (skipping bot merges; plus manual `workflow_dispatch`).
    - `autobump.yml` — Scheduled/manual version detection, bump, `pkgcheck`, `stage3` build/smoke-test, auto-merge or deduped failure Issue/Telegram + optional AI Draft PR.
    - `python-compat.yml` — Weekly/manual `PYTHON_COMPAT` sync against official Gentoo `profiles/desc/python_targets.desc` (adds latest active `python3_X`, drops EOL targets, verifies via `pkgcheck` + `stage3`, auto-merges on pass).
    - `weekly-health.yml` — Weekly live (`9999`) ebuild tests and kernel ZFS compatibility checks.
    - `build-deps.yml` — Maintainer-triggered (`/build-deps` PR comment or `workflow_dispatch`) Go/Rust `${P}-deps.tar.xz` builder that resolves `__DEPS_TARBALL_URI__` placeholders, skips already-hosted tarballs, verifies via `pkgcheck` + `stage3`, and pushes the updated ebuild + `Manifest` back to the PR.
    - `ai-repair.yml` — Manual `/ai-fix` Issue comment trigger for pluggable AI repair (Draft PR only).

---

## 2. Common Task Playbooks

### 2.1 Adding a New Package (`<category>/<pn>`)

1. **Create package files** under `<category>/<pn>/`:
   - `<pn>-<ver>.ebuild` (and/or `<pn>-9999.ebuild` if live). Follow Section 4 (Writing Good Ebuilds).
   - `metadata.xml` with `<maintainer>`, `<upstream>` (`<remote-id>`), and `<use>` descriptions for all non-global `IUSE` flags.
   - If the license does not exist in `/var/db/repos/gentoo/licenses/`, add the exact license text to `licenses/<LICENSE_NAME>`.
2. **Generate thin `Manifest`**:
   ```bash
   pkgdev manifest <category>/<pn>   # or: ebuild <category>/<pn>/<pn>-<ver>.ebuild manifest
   ```
3. **Register in `metadata/autobump.toml` & add a smoke test script** (mandatory for every package in the overlay):
   - Add `[packages."<category>/<pn>"]` using only standard top-level keys (`detect`, `strategy`, `cadence`, `retention`, `policy`, `build`, `weekly_live_test`, optional `smoke_test_script`). Place all strategy-specific parameters inside `strategy = { type = "...", ... }`.
   - Create `.github/smoke/<category>/<pn>.sh` (auto-discovered by `schema.py`; inline `smoke_test` commands in `metadata/autobump.toml` are strictly disallowed).
   - If the package has a `9999` live ebuild, set `weekly_live_test = true`.
4. **Preview the generated CI container script** to verify USE flags, test setup, and smoke test:
   ```bash
   PYTHONDONTWRITEBYTECODE=1 python3 .github/autobump/main.py render-build-script --pkg <category>/<pn>
   ```
5. **Run all required local checks** (Section 6) before committing.

### 2.2 Building & Hosting Go / Rust Offline Deps (`locez/locez-overlay-distfiles`)

Both Go (`go-vendor`) and Rust (`rust-vendor`) share a single offline dependency tarball convention hosted in `overlay.distfiles_repo` (`locez/locez-overlay-distfiles`):

- **Release tag**: `${CATEGORY}-${PN}-${PV}` (e.g. `net-misc-tsshd-0.1.10`)
- **Asset filename**: `${P}-deps.tar.xz` (e.g. `tsshd-0.1.10-deps.tar.xz`)
- **Ebuild `SRC_URI`**:
  ```bash
  https://github.com/locez/locez-overlay-distfiles/releases/download/${CATEGORY}-${PN}-${PV}/${P}-deps.tar.xz
  ```
- **Tarball internal layout** (matches Gentoo eclasses natively — zero custom `src_unpack` boilerplate):
  - **Go (`inherit go-module`)**: `${P}/vendor/...` -> overlays directly into `${S}/vendor`.
  - **Rust (`inherit cargo`)**: `cargo_home/gentoo/...` -> overlays directly into `${ECARGO_VENDOR}`. If `Cargo.lock` includes git crates, `cargo_home/git-sources.toml` is bundled too; append `cat "${ECARGO_HOME}/git-sources.toml" >> "${ECARGO_HOME}/config.toml" || die` right after `cargo_src_unpack`.
- **PR Placeholder Convention (recommended for external contributors & agents without `DISTFILES_TOKEN`)**:
  - In `SRC_URI`, simply put **`__DEPS_TARBALL_URI__`** right after the upstream source URL:
    ```bash
    SRC_URI="
    	https://github.com/owner/${PN}/archive/refs/tags/v${PV}.tar.gz -> ${P}.tar.gz
    	__DEPS_TARBALL_URI__
    "
    ```
  - Open the PR (including the package entry in `metadata/autobump.toml`). After reviewing the PR, a maintainer comments **`/build-deps`** (or triggers `build-deps.yml` via `workflow_dispatch`):
    1. If `${CATEGORY}-${PN}-${PV}/${P}-deps.tar.xz` **already exists** on `locez/locez-overlay-distfiles`, CI **skips rebuilding/re-uploading** and reuses the existing tarball.
    2. Otherwise CI builds `${P}-deps.tar.xz` cleanly from the upstream source URL and uploads it to `locez/locez-overlay-distfiles`.
    3. CI replaces `__DEPS_TARBALL_URI__` with the real URL, updates `Manifest`, and verifies both **`pkgcheck`** and **`gentoo/stage3` build & smoke-test**.
    4. Only after verification passes does CI commit and push the updated ebuild + `Manifest` back to the PR branch (or post the exact diff if fork edits are disabled).
- **One-command local build & upload** (for maintainers with local `gh auth login`):
  ```bash
  PYTHONDONTWRITEBYTECODE=1 python3 .github/autobump/main.py build-deps \
    --pkg <category>/<pn> --ver <ver> --type go|rust \
    --src-url https://github.com/owner/repo/archive/refs/tags/v<ver>.tar.gz \
    [--go-build-target ./cmd/foo] [--cargo-manifest-dir subdir] \
    --upload
  ```
  Prints the exact `SRC_URI` and `DIST` `Manifest` lines. In GitHub Actions (`autobump.yml` and `build-deps.yml`), uploading to the separate `distfiles_repo` requires repository secret **`DISTFILES_TOKEN`** (a fine-grained PAT scoped ONLY to `locez/locez-overlay-distfiles` with `Contents: Read and write`).
- **Alternative for Rust (`strategy = "rust-crates"`)**: For small Rust packages where you prefer zero external tarball hosting, use `dev-util/pycargoebuild` (`pycargoebuild -i <ebuild> <src_dir>`) to populate `CRATES` / `GIT_CRATES` / `LICENSE` inline.

### 2.3 Adding a New Autobump Strategy Plugin

1. Add a module (or function) in `.github/autobump/engine/plugins/` implementing `Strategy = Callable[[BumpContext], str | None]`.
   - Raise `BumpSkip("reason")` when an upstream version should be intentionally skipped without alerting.
   - Raise `BumpError("reason")` for real failures.
   - Update ebuild(s), regenerate thin `Manifest`, and apply `spec.retention` (`replace` / `keep-latest` / `keep-last-zfs-compatible`).
2. Register the strategy name in `STRATEGIES` inside `.github/autobump/engine/plugins/__init__.py`.
3. Add unit tests in `.github/autobump/tests/test_engine.py`.
4. Keep the plugin 100% overlay-agnostic (read package-specific config from `ctx.spec` / `metadata/autobump.toml`).

### 2.4 Local CLI Debugging & Dry Runs

All engine entrypoints live in `.github/autobump/main.py` (always run with `PYTHONDONTWRITEBYTECODE=1`):

```bash
# Inspect parsed overlay/build/ai_repair config
PYTHONDONTWRITEBYTECODE=1 python3 .github/autobump/main.py config-info

# Preview the exact bash script executed inside gentoo/stage3
PYTHONDONTWRITEBYTECODE=1 python3 .github/autobump/main.py render-build-script --pkg <cat>/<pn> [--live] [--python-target python3_15]

# Detect packages needing PYTHON_COMPAT bump or EOL cleanup
PYTHONDONTWRITEBYTECODE=1 python3 .github/autobump/main.py python-compat-detect [--only <cat>/<pn>]

# List packages changed relative to base branch or tracked for weekly live health
PYTHONDONTWRITEBYTECODE=1 python3 .github/autobump/main.py changed-packages --base origin/master
PYTHONDONTWRITEBYTECODE=1 python3 .github/autobump/main.py live-packages
```

---

## 3. Declarative Automation (`metadata/autobump.toml`) & Smoke Tests

### `metadata/autobump.toml` Rules
- Must remain **purely declarative**. **NEVER** embed shell commands (`smoke_test`) inside `metadata/autobump.toml` — `schema.py` rejects `smoke_test` and any unknown top-level package keys via `ConfigError`.
- Strategy-specific options MUST live inside `strategy = { type = "...", ... }` (never flattened to top-level package keys).
- Schema validation in `.github/autobump/engine/schema.py` eagerly rejects invalid `build.mode`, `retention.type`, `retention.count`, `retention.guard`, unknown top-level keys, or inline `smoke_test` via `ConfigError`.

Minimal package examples:

```toml
# Example 1: GitHub release C/Python package with unit tests (smoke test in .github/smoke/category/example-app.sh)
[packages."category/example-app"]
detect = { type = "nvchecker", source = "github", github = "owner/repo", use_latest_release = true, prefix = "v" }
strategy = { type = "github-release", url_template = "https://github.com/owner/repo/releases/download/v{ver}/example-{ver}.tar.xz", dist_template = "example-{ver}.tar.xz" }
retention = { type = "replace" }
policy = "auto-merge"
build = { mode = "emerge-full", test = true }

# Example 2: Prebuilt binary package without autobump (smoke test in .github/smoke/category/example-bin.sh)
[packages."category/example-bin"]
detect = { type = "none" }
build = { mode = "emerge-bin", use = ["bwrap"] }
```

Field quick reference (only these top-level keys are allowed under `[packages."<cat>/<pn>"]`):
- `detect`: `{ type = "nvchecker", ... }`, `{ type = "wechat-head" }`, or `{ type = "none" }`.
- `strategy`: String (`"pypi"`) or inline table (`{ type = "<key>", ... }`) matching a registered key in `STRATEGIES`. Strategy-specific options live inside the `strategy` table:
  - `github-release`: requires `url_template` and `dist_template` (`{ver}`, `{tag_ver}`, `{pn}` placeholders); optional `tag_ver_strip_prefix`, `extra_dists` (list of `{ url_template, dist_template }`), and `preserve_dists` (globs of static `Manifest` `DIST` entries to keep untouched across bumps).
  - `go-vendor`: optional `build_target` (builds & uploads `${P}-deps.tar.xz` to `overlay.distfiles_repo` under tag `${CATEGORY}-${PN}-${PV}`).
  - `rust-vendor` / `rust-crates`: optional `manifest_dir` if `Cargo.toml` is in a subdirectory, and optional `zig = { manifest_dir = "...", deps = [...] }` for hybrid Rust+Zig builds (`rust-vendor` uploads `${P}-deps.tar.xz` to `overlay.distfiles_repo`; `rust-crates` refreshes `CRATES`/`GIT_CRATES`/`LICENSE` in-place via `pycargoebuild -i`).
  - `zig-vendor`: optional `manifest_dir` and `deps` (lazy Zig dependency allowlist).
- `cadence`: `"daily"` (default) or `"weekly"` (checked on Mondays during scheduled `autobump.yml` runs, and always checked on manual `workflow_dispatch`).
- `retention`: `{ type = "replace" }` or `{ type = "keep-latest", count = N }`, optional `guard = "keep-last-zfs-compatible"`.
- `policy`: `"auto-merge"` (default for verified packages) or `"pr-only"`.
- `build`:
  - `mode`: `"emerge-full"` (build from source), `"emerge-bin"` (prebuilt binary/deb/tarball), `"kernel-sources"` (supports `use_sets` + `check_kconfig = true`), or `"dep-resolve"` (`emerge --pretend`, e.g. virtuals).
  - `use` / `use_sets`, `test = true|false`, `extra_pkgs = [...]`, `env = {...}`.
- `smoke_test_script`: Optional explicit path override if sharing a script; normally omitted because `.github/smoke/<category>/<pn>.sh` is auto-discovered.
- `weekly_live_test`: `true` for packages that maintain a `9999` live ebuild.

### `.github/smoke/` Convention
- **ALL** package smoke tests MUST live in `.github/smoke/<category>/<pn>.sh` (auto-discovered by `schema.py` without extra TOML config). Inline `smoke_test` in `metadata/autobump.toml` is strictly forbidden.
- Shared helper scripts go in `.github/smoke/_shared/` (e.g. `.github/smoke/_shared/check-elf.sh`, `.github/smoke/_shared/proton-compat.sh`), invoked via `dirname "${BASH_SOURCE[0]}"`.
- Every script must start with `#!/usr/bin/env bash`, use `set -euo pipefail`, and pass `bash -n`.
- In `python-compat.yml`, `builder.py` exports `EPYTHON=python3.XX` inside the smoke-test subshell so `python3` resolves to the target interpreter under test.

---

## 4. Writing Good Gentoo Ebuilds & QA Rules

Follow Gentoo's Devmanual and `pkgcheck` standards:

1. **Modern `EAPI=8` & Eclasses**:
   - Always use `EAPI=8`.
   - **Python**: `DISTUTILS_USE_PEP517=...` + `distutils-r1` (+ `pypi` when hosted on PyPI). Always pass `[${PYTHON_USEDEP}]` on multi-impl Python dependencies. Enable tests via `distutils_enable_tests pytest` (+ `EPYTEST_PLUGINS=( ... )` as needed) — **ALWAYS place `distutils_enable_tests` AFTER `BDEPEND="..."` (or use `BDEPEND+="..."`)**, otherwise `BDEPEND="..."` overwrites and drops `dev-python/pytest`! For Qt GUI tests in headless CI, export `local -x QT_QPA_PLATFORM=offscreen` in `python_test()`.
   - **Go**: `inherit go-module` + `${P}-deps.tar.xz` hosted in `locez/locez-overlay-distfiles` (never use deprecated `EGO_SUM`, never run `go mod download` in `src_*`). Pin `BDEPEND=">=dev-lang/go-X.Y:="`, and build/test with `ego build -buildvcs=false -trimpath` / `ego test`.
   - **Rust**: `inherit cargo`. Define `RUST_MIN_VER` (and `CRATES` / `GIT_CRATES` when using inline crates mode) **BEFORE** `inherit cargo` (otherwise `CARGO_CRATE_URIS` is empty). Prefer `${P}-deps.tar.xz` (`rust-vendor`, `cargo_home/gentoo` layout) for clean 2-line `Manifest`s, or `pycargoebuild -i` (`rust-crates`) for small crate lists. Always use `cargo_src_configure` / `cargo_src_compile` / `cargo_src_test` / `cargo_src_install` (or `dobin "$(cargo_target_dir)/<bin>"`) — never invoke raw `cargo build --release` or fetch crates in `src_*`. Use `cargo_live_src_unpack` for `9999` live ebuilds.
   - **Node.js**: Gentoo main tree has no single `nodejs.eclass`; pick the simplest pattern matching upstream: (a) self-contained npm CLI -> fetch registry `.tgz` and install with `npm --prefix="${ED}/usr" --omit=dev --offline --global install`; (b) pure JS frontend/build without native `.node` addons -> host `${P}-node_modules.tar.xz` or prebuilt `${P}-assets.tar.xz` distfile; (c) projects with native C++ addons -> offline `npm-cache`/`pnpm-store` tarball + offline install in `src_compile`.
   - **C/C++**: Use `cmake` / `meson` eclasses; respect user `CC`/`CXX`/`CFLAGS`/`LDFLAGS` (never hardcode `-march=native`, `-O3`, or manual `strip`).
   - **Prebuilt binaries (`*-bin`, `.deb`, Electron/AppImage)**: Use `unpacker`, `desktop`, `xdg`; install under `/opt/<pn>`, create wrapper/symlink in `/usr/bin/`, install `.desktop` (`domenu`) and icons (`doicon`/`newicon`), set `QA_PREBUILT="*"` and appropriate `RESTRICT="strip mirror"` (or `bindist mirror`).
   - **Live ebuilds (`9999`)**: Inherit `git-r3`, set `EGIT_REPO_URI`, and **NEVER** set `KEYWORDS`.
2. **Strict Dependency Splitting & Sub-Slots**:
   - `BDEPEND`: build-time host tools (`dev-build/cmake`, `dev-build/ninja`, `virtual/pkgconfig`, `dev-util/wayland-scanner`, `test? ( dev-python/pytest ... )`).
   - `DEPEND`: build-time target headers/libraries (`dev-qt/qtbase:6=`, `dev-libs/wayland:=`).
   - `RDEPEND`: runtime dependencies (typically `RDEPEND="${DEPEND} ..."` plus pure runtime tools/modules).
   - Use sub-slot operators (`:=`) on C/C++ shared libraries with ABI-sensitive sonames (Qt, Wayland, ICU, etc.).
   - **Main tree only (`masters = gentoo`)**: Never depend on ebuilds or USE flags from third-party overlays (`gentoo-zh`, `loong` compat packages, etc.).
3. **SRC_URI, Sandbox & Shell Hygiene**:
   - Always rename generic archive filenames (`https://github.com/org/repo/archive/refs/tags/v${PV}.tar.gz -> ${P}.tar.gz`) to avoid `DISTDIR` collisions.
   - Quote path variables (`"${S}"`, `"${D}"`, `"${ED}"`, `"${WORKDIR}"`) and append `|| die` to non-Portage shell commands (`sed`, `rm`, `cp`, `mv`, `ln`, `mkdir`, `tar`). Portage helpers (`dobin`, `doins`, `domenu`, `dosym`, `eapply`) auto-die in `EAPI=8`.
   - Prefer `PATCHES=( ... )` / `eapply` over raw `patch`, and `dosym -r` for relative symlinks.
   - Never write outside `${ED}` during `src_install`, and avoid installing duplicate license files covered by `LICENSE="..."`.
4. **Profile Portability (`no-multilib`, `musl`)**:
   - Avoid `NonsolvableDepsInStable`/`Dev` failures across Gentoo profiles. Gate 32-bit compatibility deps behind `IUSE="+abi_x86_32"` and `[abi_x86_32(-)?]`.
5. **Thin Manifests, Licenses & Keywords**:
   - Update `Manifest` whenever `SRC_URI` changes; keep thin manifests (`DIST` entries only).
   - Use exact Gentoo license names; start new release ebuilds with `KEYWORDS="~amd64"` (never `amd64` directly).
6. **Domain-Specific Overlay Policies**:
   - **Kernel ZFS retention guard**: `sys-kernel/xanmod-sources` and `sys-kernel/cachyos-sources` enforce `guard = "keep-last-zfs-compatible"`. Never delete the last kernel ebuild compatible with Gentoo stable `sys-fs/zfs` `MODULES_KERNEL_MAX` (`!zfs` kernels may coexist alongside, but must never remove it).
   - **Python `PYTHON_COMPAT` hygiene**: Follow official Gentoo active standard CPython targets (`python3_X` from `profiles/desc/python_targets.desc`; exclude freethreading `*t` targets). Preserve intentional minimum version floors (e.g. `python3_{13..15}` keeps floor `13` until `13` goes EOL). Never force-merge if `pkgcheck` flags an upstream Gentoo dependency lacking that target.

---

## 5. Automation Engine & CI Invariants

Preserve these invariants when editing `.github/autobump/`, `.github/actions/`, or `.github/workflows/`:

1. **100% Overlay-Agnostic**: Zero hardcoded references to `locez`, `locez-overlay`, or specific package atoms inside `.github/autobump/engine/` or `.github/workflows/`. All overlay-specific config comes from `metadata/autobump.toml`.
2. **Zero External Python Dependencies**: `.github/autobump/engine/` and its unit tests must run on Python 3.11+ standard library only.
3. **Never commit `__pycache__/`**: Always run local Python commands with `PYTHONDONTWRITEBYTECODE=1`.
4. **Portage `stage3` Build Invariants (`engine/builder.py` & `action.yml`)**:
   - Switch `stage3` profile via `eselect profile set` to `build.profile` (`default/linux/amd64/23.0/desktop`) so GUI/Qt/GTK USE flags (`X`, `opengl`, etc.) resolve cleanly.
   - **Never pass `--binpkg-respect-use=y` explicitly**: Portage already enables it by default for `--usepkg`/`--getbinpkg`; passing it explicitly silently disables `--autounmask-use`.
   - Always pass `--autounmask=y --autounmask-use=y --autounmask-write=y --autounmask-continue=y` with `CONFIG_PROTECT="-*"` in CI `make.conf`.
   - When `build.test = true`, enable **both** `USE="test"` in `/etc/portage/package.use/10-target` AND `FEATURES="test"` in `/etc/portage/package.env/target`; otherwise Portage skips `test? ( ... )` `BDEPEND`s (e.g. `pytest`) during dependency resolution.
   - Preserve parallel execution & binpkg/tree caching:
     - `MAKEOPTS="-j${NPROC}"` + `emerge --jobs="${NPROC}" --load-average="${NPROC}"`.
     - `FEATURES="... -merge-sync -binpkg-request-signature buildpkg parallel-fetch parallel-install"`.
     - `--usepkg --getbinpkg --usepkg-exclude=<pkg> --buildpkg-exclude=<pkg> --binpkg-changed-deps=y`.
     - Daily Portage tree cache (`gentoo-tree-v1-YYYY-MM-DD`) and weekly CI binpkg cache (`gentoo-binpkgs-v1-YYYY-WVV-<py_tag>-*`), with host `sudo chown` + `if: always()` cache save so compiled dependencies survive retries.
   - In `--python-target` mode, probe single-target `dev-python/*` + `<pkg>` (`-* <target>`) first via `emerge --pretend` (avoids compiling heavy deps like `pyqt6`/`cython`/`lxml` twice) and automatically fall back to additive `*/* PYTHON_TARGETS: <target>` if unsolvable.
5. **Notification & AI Repair Guardrails**:
   - Missing optional secrets (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `AI_REPAIR_CMD`, `AI_REPAIR_API_KEY`) must no-op cleanly without failing CI.
   - GitHub failure Issues are deduplicated per `(pkg, title)` under label `autobump-failed`, and auto-closed when the package passes and merges.
   - **AI Repair MUST NEVER auto-merge**: `mode = "draft-pr"` only, strictly restricted to `<category>/<package>/` + `ai_repair.allowed_extra_paths` (`licenses/`), and must pass both `pkgcheck` and `stage3` build/smoke-test before opening a Draft PR.

---

## 6. Required Local Verification & Commit Conventions

Run these checks before every commit / push:

```bash
# 1. Automation engine unit tests (includes bash -n for all rendered build & smoke scripts)
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s .github/autobump/tests -v

# 2. Gentoo QA scan (must exit 0 with zero errors)
pkgcheck scan --exit error
```

If you modified any workflow or action YAML files, also validate YAML syntax:

```bash
python3 -c "import glob, yaml; [yaml.safe_load(open(f)) for f in glob.glob('.github/workflows/*.yml') + glob.glob('.github/actions/*/action.yml')]"
```

### Commit Message Style
- **Package / ebuild changes**: `<category>/<pn>: <concise description>` (prefer `pkgdev commit` when available).
- **CI / automation / docs changes**: Conventional Commits (`ci(build-test): ...`, `feat(autobump): ...`, `fix(engine): ...`, `docs: ...`).
