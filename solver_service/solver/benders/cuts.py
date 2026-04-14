"""No-good cut generation for the Benders loop.

A Conflict says "these k instances cannot coexist on (clinician c, day d)
under any window choice."  The weak no-good cut is:

    sum_{i in unfit} slot[i, c, d, *]  <=  k - 1

Which forbids the full subset but allows proper sub-subsets.  Because the
subproblem already shrinks conflicts to a minimal infeasible subset, each
cut eliminates a whole family of envelopes, not just one.

Stronger cut classes (capacity-based, pairwise, Hooker-style combinatorial)
are listed in the roadmap but deferred — this module implements weak cuts
only.  Profiling drives when to add stronger forms.
"""

from __future__ import annotations

from solver.benders.envelope import CutStore, NoGoodCut
from solver.benders.subproblem import Conflict, SubproblemResult


def generate_cuts(result: SubproblemResult) -> list[NoGoodCut]:
    """Turn each conflict into a weak no-good cut."""
    cuts: list[NoGoodCut] = []
    for conflict in result.conflicts:
        if len(conflict.unfit_subset) < 2:
            # A single unfit instance means the envelope alone is wrong —
            # nothing to cut, the envelope should have excluded it.  We still
            # emit a cut of length 1 to force that exact (i, c, d) off.
            triples = [
                (iid, conflict.clinician_idx, conflict.day_idx)
                for iid in conflict.unfit_subset
            ]
            cuts.append(
                NoGoodCut(forbidden=triples, reason=conflict.reason or "singleton")
            )
            continue

        triples = [
            (iid, conflict.clinician_idx, conflict.day_idx)
            for iid in conflict.unfit_subset
        ]
        cuts.append(NoGoodCut(forbidden=triples, reason=conflict.reason))
    return cuts


def apply_cuts(store: CutStore, new_cuts: list[NoGoodCut]) -> int:
    """Dedupe and add cuts to the store.  Returns count of newly added cuts."""
    existing = {
        tuple(sorted(c.forbidden)) for c in store.cuts
    }
    added = 0
    for c in new_cuts:
        key = tuple(sorted(c.forbidden))
        if key in existing:
            continue
        existing.add(key)
        store.add(c)
        added += 1
    return added
