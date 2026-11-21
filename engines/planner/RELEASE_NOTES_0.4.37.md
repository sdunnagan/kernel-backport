# kernel-backport-planner 0.4.37

Release: 2026-11-11

## Changes

1. **Independent TAKE-only integration replay.** Runs after the advisor, in a disposable Git worktree at the exact recorded downstream base. Reports clean, conflicting, empty/redundant, and not-tested commits. It does not overwrite discovery-replay annotations. An optional check command now uses the TAKE series when an advisor is active.
2. **Downstream revert audit.** A full-SHA `This reverts commit ...` trailer invalidates an earlier PRESENT identity; a revert of that revert restores it. Subject-only reversions can downgrade matching identities to engineering review, and other unresolvable reversions are disclosed. Unverifiable effective state blocks integration readiness.
3. **Architecture-neutral platform-path discovery.** Positive `paths` in a selected platform profile seed candidates outside the focus list. The active kernel architecture is inferred from `arch/<name>/` focus paths, or specified with repeatable `--architecture` (arm64, x86, powerpc, s390, etc.). The filter also applies to advisor architecture-scope checks. `--no-platform-path-discovery` disables this pass.
4. **Explicit dependency completion audit.** `dependency_closure_incomplete` lists actionable commits not explored before round limits. Incomplete discovery prevents `integration_ready`.
5. **Downstream HEAD divergence check.** Compares `--downstream-ref HEAD` with the fetched remote tracking branch when `--downstream` is provided. Warns and blocks unacknowledged divergence; `--allow-divergent-downstream` is an explicit override.
6. **Shared persistent advisor cache.** Workspace runs reuse `.cache/kernel-backport-planner/advisor-cache.json`, use a Linux file lock and atomic write with merge, and snapshot the shared cache into each completed run archive. User-specified `--advisor-cache` continues to work.
7. **Independent integration gate.** JSON now contains `take_integration`, `integration_ready`, `architecture_filter`, `platform_path_candidates`, `dependency_closure_incomplete`, and `baseline_warning`. `--strict-integration` returns exit status 4 when TAKE-only integration and safety checks aren't ready.

## Project-specific input wrapper

The attached `run-backport-planner` remains explicitly **ARM64** because that run is targeting the Arm subsystem. It now includes `--architecture arm64` and `--strict-integration`. The planner itself is **not ARM64-only**. For an x86, POWER, or IBM Z project, change the focus list and choose the matching kernel architecture; no code changes are necessary.

The wrapper currently uses `--downstream-ref HEAD`. If HEAD differs from `cs10/main`, strict mode blocks until you deliberately select a matching downstream ref or add `--allow-divergent-downstream` after checking that the local branch is intended.

## Verification performed

- **282 tests passed**: the 271 existing tests plus 11 new regression tests covering real Git revert chains, ambiguous reverts, architecture filtering, dependency round exhaustion, TAKE replay outcomes, baseline divergence, argument validation, and cache merging.
- Additional CLI smoke test against a disposable Git repository succeeded; emitted valid JSON and ARM64 architecture inference.
- The code was syntax-compiled with Python 3.13.
- No full CentOS Stream 10 or upstream v7 repository was included in the supplied archives, and no live OpenAI evaluation or production backport execution was performed. Validate against the actual kernel repositories before applying patches.

## Test command

From the extracted `kernel-backport-planner/` directory:

```sh
python3 -m unittest -q test_kernel_backport_planner test_planner_0437
```

## Exit statuses

Existing `0`, `2` (discovery replay conflicts) and `3` (check command failed) behavior is retained by default. In `--strict-integration` mode, `4` means the final TAKE-only integration gate is not ready; conflicts from SKIP/REVIEW-only discovery replay are ignored if the TAKE integration is otherwise ready. A zero status in strict mode **does not** mean the kernel has been built or tested.
