---
title: locez-overlay bump workflow design
date: 2026-03-14
status: approved-in-chat
---

# locez-overlay Bump Workflow Design

## Goal

Define a repository-specific workflow for maintaining release ebuilds in `locez-overlay` with `nvchecker`, while excluding live-only packages and packages that are no longer maintained.

This round also removes `dev-util/claude-code-router` from the overlay and updates the automation so stale `nvchecker` issues are closed when a package is removed or only has `9999` ebuilds left.

## Scope

In scope:

- Remove `dev-util/claude-code-router` from the overlay and from `nvchecker` tracking.
- Change `nvchecker` issue automation so only maintained release packages participate.
- Close existing open `nvchecker` issues for packages that are no longer maintained.
- Define a repeatable local workflow for adding, bumping, and removing packages in this repository.
- Capture that workflow as a skill under `~/locez-skills`.

Out of scope:

- Auto-merging or release automation.
- Turning this into a generic Gentoo overlay skill.
- Building a full remote CI matrix for package compilation.

## Maintained Package Rule

A package is considered bump-managed by this repository only when all of the following are true:

1. The package directory still exists in the overlay.
2. The package has at least one non-`9999` ebuild.
3. The package is still intentionally maintained by the repository owner.

Operationally, the GitHub workflow will infer this from repository state:

- Missing package directory: not maintained.
- Only `9999` ebuilds present: not bump-managed.
- At least one release ebuild present: maintained.

This keeps the source of truth in the repository tree instead of a separate allowlist.

## `nvchecker` and Issue Workflow

### Tracking Input

`nvchecker.toml` remains the list of upstream sources that can be checked.

Rules:

- Release packages that are actively maintained may stay in `nvchecker.toml`.
- Live-only `9999` packages may remain in `nvchecker.toml` for version visibility, but they must not create or keep open bump issues.
- Removed packages must be deleted from `nvchecker.toml`.

### Issue Creation and Update

For each `nvchecker` result:

- If the package is maintained and has a release ebuild, the workflow creates or updates the existing `[nvchecker]` issue.
- The issue title continues to use the current convention:
  `[nvchecker] category/package: LOCAL_VER -> NEW_VER`

### Issue Closure

If an open `[nvchecker]` issue exists for a package that is no longer maintained:

- add a short comment explaining why the package is no longer tracked for bumps
- close the issue automatically

Reasons include:

- the package was removed from the overlay
- the package only has `9999` ebuilds remaining

This keeps the open issue list aligned with actual maintenance scope.

## Package Lifecycle Rules

### Adding a New Package

When adding a package:

- If the package is intended for ongoing release maintenance, add:
  - ebuild
  - `Manifest`
  - `metadata.xml`
  - `nvchecker.toml` entry
- If the package is live-only (`9999`), do not treat it as bump-managed and do not rely on `nvchecker` issue automation for it.

### Bumping an Existing Release Package

When bumping a maintained release package:

- create or update the target version ebuild
- update checksums and `Manifest`
- adjust dependencies, EAPI details, or install logic as needed
- preserve or prune older release ebuilds according to repository preference at the time

### Updating a `9999` Package

`9999` packages may still be edited for:

- build fixes
- dependency changes
- upstream layout changes

But they do not participate in the bump-issue workflow.

### Removing a Package

When removing a package:

- delete the release ebuilds
- delete `Manifest`
- keep or remove `metadata.xml` based on whether the package directory remains useful; for a full removal, remove the directory contents
- remove the package from `nvchecker.toml`
- rely on the workflow to close any related open `nvchecker` issue

## Local Verification Strategy

Local verification is the primary quality gate. GitHub Actions is a secondary safety net.

### Required Local Checks

For every package add, bump, or substantial ebuild change:

1. Run `pkgdev manifest`.
2. Run `pkgcheck scan`.

### Required Build Validation for New or Bumped Release Ebuilds

For each newly added or bumped release ebuild, run:

`ebuild <target.ebuild> clean fetch unpack compile install clean`

If the ebuild supports useful tests and the cost is acceptable, optionally run:

`ebuild <target.ebuild> test clean`

The final `clean` step is required so local verification does not leave build/install residue behind.

### CI Positioning

GitHub CI remains useful for:

- catching repository-level `pkgcheck` issues
- providing remote confirmation after push

GitHub CI is not a substitute for local verification and should not be treated as the primary build check.

## Planned Repository Changes for This Round

1. Remove `dev-util/claude-code-router` from the overlay tree.
2. Remove `dev-util/claude-code-router` from `.github/workflows/nvchecker.toml`.
3. Update `.github/workflows/nvchecker.yml` so issue handling uses the maintained-package rule.
4. Ensure stale issues for removed or live-only packages are commented and closed automatically.
5. Write a repository-specific skill in `~/locez-skills` documenting the workflow.

## Skill Design

The skill will be repository-specific and aimed at future maintenance of `locez-overlay`.

It should trigger when work involves:

- bumping packages in this overlay
- adding new release ebuilds
- removing packages from maintenance
- reconciling `nvchecker` issues with repository state

The skill should instruct the agent to:

1. inspect open `nvchecker` issues and repository state
2. determine whether the package is maintained, live-only, or removed
3. perform the package add/bump/remove work
4. update `nvchecker.toml` and workflow logic when maintenance scope changes
5. run local verification, including `clean`
6. avoid merge actions

## Risks and Mitigations

- Risk: `nvchecker.toml` still includes entries that are no longer meaningful.
  Mitigation: remove entries when packages are removed; tolerate live-only entries only if their retained visibility is intentional.

- Risk: issue-closing logic could match the wrong issue.
  Mitigation: continue matching on the existing `[nvchecker] category/package` naming convention.

- Risk: local verification may be skipped because CI exists.
  Mitigation: make local `pkgdev`, `pkgcheck`, and targeted `ebuild` runs part of the documented required workflow.

## Success Criteria

This design is complete when:

- removed packages no longer participate in `nvchecker` tracking
- live-only packages no longer generate or retain bump issues
- release packages continue to generate actionable bump issues
- local verification is explicit and includes cleanup
- the process is documented as a reusable skill under `~/locez-skills`
