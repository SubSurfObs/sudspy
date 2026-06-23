# Handoff to sudspy — branch-name convention + cleanup of legacy `master` local branches

**From:** eqserver_2_seiscomp (Dan)
**Date:** 2026-06-23
**Priority:** Low — diagnostic + convention. No code change. The point is to
make sure no future "where am I, what am I tracking" confusion bites a
production run, given sudspy is now Stage 2 and Stage 4 critical-path
(see `eqserver_2_seiscomp/CLAUDE.md` "Pipeline stages and their
dependencies").

## What I found

Auditing repo state across hosts on 2026-06-23, the staging VM
(`rs-l-0ezd3a.desktop.cloud.unimelb.edu.au`) had sudspy installed at
`/home/.../projects/SubSurfObs/sudspy/` with a local branch called
`master` that was NOT tracking anything in particular. After `git fetch
origin`, `git branch -a` showed:

```
* master                           (local-only, no upstream configured)
  remotes/origin/main              (the actual canonical branch)
```

The local `master` branch's HEAD was at the previous origin/main HEAD,
so the *content* was correct. But the branch name didn't match upstream,
and `git pull` would have failed silently in a way that's easy to miss.

The Mac copy of sudspy was correctly on `main` tracking `origin/main`,
and the GitHub remote (`origin`) only has `main` — there is no `master`
branch upstream.

## Why this happened (most likely)

Prior to ~2026-06-12, sudspy on the staging VM was installed by copying
a snapshot of an older sudspy checkout — possibly from before the
project migrated to the `main` branch convention. The local branch
name "master" was preserved by the copy. No remote was configured at
the time. See `eqserver_2_seiscomp/handoffs/sudspy/2026-06-22_int-
dtype-and-fast-merge-split/01_request.md` for the engine-provenance
context — the same incident class that drove that handoff (operator
side-loading library code via copy instead of `git clone`) is what
left the VM with this branch-name mismatch.

## What I did

Fast-forwarded the VM's `master` branch to `origin/main` content
(commit 32e5769) so the on-disk code matches the canonical
`origin/main` HEAD. The VM's local branch is still called `master`
though — content correct, name still drifting.

## What I think sudspy should declare as policy

A single-line statement in `sudspy/CLAUDE.md` or `README.md`:

> The canonical branch is `main`. Local clones MUST use `main` as the
> tracking branch. A `master` branch is a stale local artifact from
> pre-migration snapshots — rename to `main` and set the upstream to
> `origin/main`.

And a one-time normalisation command for any host that still has a
`master`-named local branch:

```bash
cd /path/to/sudspy
git branch -m master main
git branch --set-upstream-to=origin/main main
git pull --ff-only
```

## Why this matters for the pipeline

sudspy is in the dependency matrix for Stage 2 PROFILE and Stage 4
CONVERT in eqserver_2_seiscomp. Stage 4 is where the 2026-06 side-load
incident silently produced ~320 station-years of bytes from
non-committed engine code. The branch-name convention is part of the
same provenance discipline that pre-flights every Stage 4 run by
verifying the sudspy SHA matches origin/main HEAD.

If a host has a `master` branch with stale content, the SHA check might
pass against the local commit but the host wouldn't pick up new
upstream commits via `git pull`. That's the failure mode this convention
prevents.

## What I'm asking for

1. Confirm `main` is the canonical branch going forward (no `master`).
2. Optionally add the normalisation paragraph to `sudspy/CLAUDE.md` so
   future operators don't reinvent this fix.
3. No code change needed. This is convention + documentation.

I'm normalising the VM's `master` → `main` right after sending this
handoff. The state will be:

- Mac: branch `main`, tracking `origin/main`, clean ✓
- VM: branch `main` (after normalisation), tracking `origin/main`, clean ✓
- Origin: branch `main` ✓
