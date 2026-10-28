# Bugfix Requirements Document

## Introduction

The `DUPLICATES` control relation is symmetric: a relation between controls A and B carries the same meaning regardless of direction. To keep the symmetric relation representable by the single `UNIQUE (from_control_id, to_control_id, relation_type)` constraint on `control_relations`, the write path in `review/materialize.py` (`_materialize_relation`) canonicalizes every `DUPLICATES` row so that `from_control_id < to_control_id`. Without this, A→B and B→A would be stored as two distinct, mirror-image rows that the unique constraint cannot catch.

The control-merge path does not apply the same canonicalization. When the loser's relations are re-pointed onto the winner, `merge_controls` rewrites `from_control_id` / `to_control_id` directly with no `DUPLICATES` swap, and the preview planner `_plan_relations` builds its `occupied` set and post-move keys from raw, un-canonicalized tuples. As a result, merging can persist two mirror-image `DUPLICATES` rows for the same relationship, and the merge preview can mis-report that collision as a "move" instead of a discard.

This is a data-integrity defect against a hard project invariant: only `review/materialize.py` may write `control_relations`, and `DUPLICATES` must always be stored in canonical order. The downstream graph display de-duplicates via `_pair_key`, so the corruption is hidden in the rendered graph while the confirmed underlying data is wrong. This fix must preserve the merge path's role as part of the sole write path and must not delete data outside the established merge semantics.

Concrete trigger: loser `L` holds a `DUPLICATES` row canonicalized as `(L, X)` with `L < X`; winner `W` already holds a `DUPLICATES` row `(X, W)` with `X < W`. Merging `L` into `W` re-points the loser's row to `(W, X)`. Its canonical form is `(X, W)`, which already exists, but the planner compares the raw tuple `(W, X, DUPLICATES)` against `occupied = {(X, W, DUPLICATES)}`, finds no match, counts a move, and execution writes `(W, X)` — producing mirror rows `(X, W)` and `(W, X)`.

## Bug Analysis

### Current Behavior (Defect)

What currently happens when a merge re-points a symmetric `DUPLICATES` relation.

1.1 WHEN a merge re-points a loser's `DUPLICATES` relation onto the winner such that the resulting raw tuple has `from_control_id > to_control_id` THEN the system stores the relation in non-canonical order instead of swapping ends so that `from_control_id < to_control_id`

1.2 WHEN a re-pointed `DUPLICATES` relation's canonical form equals an existing winner `DUPLICATES` relation but its raw tuple differs (mirror image) THEN the system persists both rows, leaving two mirror-image `DUPLICATES` records for the same relationship that the unique constraint cannot detect

1.3 WHEN the merge preview plan evaluates a re-pointed `DUPLICATES` relation whose canonical form collides with an existing winner relation but whose raw tuple differs THEN the system counts it as a move rather than reporting it as a duplicate discard

### Expected Behavior (Correct)

What should happen instead.

2.1 WHEN a merge re-points a loser's `DUPLICATES` relation onto the winner such that the resulting raw tuple has `from_control_id > to_control_id` THEN the system SHALL swap the ends so the relation is stored with `from_control_id < to_control_id`, matching the canonicalization the write path applies

2.2 WHEN a re-pointed `DUPLICATES` relation's canonical form equals an existing winner `DUPLICATES` relation but its raw tuple differs (mirror image) THEN the system SHALL treat it as a duplicate and SHALL NOT create a second mirror-image row, so no mirror-image `DUPLICATES` rows exist in `control_relations` after the merge

2.3 WHEN the merge preview plan evaluates a re-pointed `DUPLICATES` relation whose canonical form collides with an existing winner relation but whose raw tuple differs THEN the system SHALL report it as a duplicate discard (`merge.discard.relation_duplicate`) rather than counting it as a move

### Unchanged Behavior (Regression Prevention)

Existing behavior that must be preserved.

3.1 WHEN a merge re-points a non-symmetric relation (e.g. `DEPENDS_ON`) THEN the system SHALL CONTINUE TO re-point it without reordering its ends

3.2 WHEN a merge re-points a relation that becomes a self-loop (both ends resolve to the winner) THEN the system SHALL CONTINUE TO discard it with code `merge.discard.relation_self_loop`

3.3 WHEN a merge re-points a relation whose post-move raw tuple already exactly matches an existing winner relation THEN the system SHALL CONTINUE TO discard it with code `merge.discard.relation_duplicate`

3.4 WHEN a merge re-points a `DUPLICATES` relation that does not collide with any existing winner relation in canonical form THEN the system SHALL CONTINUE TO count it as a move

3.5 WHEN the write path (`_materialize_relation`) stores or looks up a `DUPLICATES` relation THEN the system SHALL CONTINUE TO canonicalize it so that `from_control_id < to_control_id`

3.6 WHEN a control is merged THEN the system SHALL CONTINUE TO mark the loser as `merged` with `merged_into_id` set and SHALL CONTINUE TO record an audit trail, without deleting confirmed data outside existing merge-discard semantics

## Bug Condition

Using the bug condition methodology, the defect is characterized as follows.

- **F** — the original merge path (`_plan_relations` in `controls/merge.py` and the relation re-point loop in `merge_controls` in `review/materialize.py`) before the fix.
- **F'** — the merge path after the fix, with `DUPLICATES` canonicalization applied to both the plan and the execution.

### Bug Condition Function

```pascal
FUNCTION isBugCondition(X)
  INPUT: X of type MergeRelationMove   // a loser DUPLICATES relation re-pointed onto the winner
  OUTPUT: boolean

  // The relation is symmetric and, after re-pointing onto the winner,
  // its raw ends are out of canonical order.
  rawFrom ← (winner.id IF X.from_control_id = loser.id ELSE X.from_control_id)
  rawTo   ← (winner.id IF X.to_control_id   = loser.id ELSE X.to_control_id)

  RETURN X.relation_type = DUPLICATES
     AND rawFrom ≠ rawTo
     AND rawFrom > rawTo
END FUNCTION
```

### Property Specification (Fix Checking)

```pascal
// Property: Fix Checking - merge canonicalizes symmetric relations
FOR ALL X WHERE isBugCondition(X) DO
  canonical ← (min(rawFrom, rawTo), max(rawFrom, rawTo), DUPLICATES)

  // Plan: a canonical-form collision with an existing winner relation is a discard, not a move.
  IF canonical ∈ occupied_canonical THEN
    ASSERT plan reports discard with code "merge.discard.relation_duplicate"
    ASSERT plan does NOT count a move for X
  END IF

  // Execution: the persisted row is in canonical order and no mirror exists.
  result ← F'(X)
  ASSERT result.from_control_id < result.to_control_id
  ASSERT NOT exists_mirror_row(result)   // no (to, from, DUPLICATES) alongside (from, to, DUPLICATES)
END FOR
```

### Preservation Specification (Preservation Checking)

```pascal
// Property: Preservation Checking - all non-buggy merge moves behave identically
FOR ALL X WHERE NOT isBugCondition(X) DO
  ASSERT F(X) = F'(X)
END FOR
```
