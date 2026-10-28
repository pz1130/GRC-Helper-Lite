# Bugfix Requirements Document

## Introduction

The login-failure rate limiter in `backend/app/iam/throttle.py` can leave a Redis
counter key with no expiry (TTL), turning the intended time-windowed cooldown into a
permanent, unrecoverable lockout.

The counter key is created by `redis.incr` at value 1, and the TTL is applied in a
separate command that only runs when `count == 1`. If the `incr` commits but the
following `expire` does not (a transient Redis error or a dropped connection between
the two commands — swallowed by the surrounding broad `except Exception`), the key is
left at value 1 with no TTL. On every later failure `count != 1`, so `expire` is never
retried. The counter then climbs to its ceiling and stays there forever.

This affects both `record_failure` (the account and per-IP login counters) and the
generic `allow` helper, which share the same incr-then-conditional-expire pattern. The
impact is most severe on the per-IP counter: by deliberate design `clear` never resets
the IP key, so once the IP key loses its TTL there is no recovery path at all —
a permanent, IP-wide lockout. The account key is only recoverable via a later
successful login's `clear`. This breaks the module's stated reliance on window-based
auto-expiry and is an availability / security-correctness defect.

## Bug Analysis

### Current Behavior (Defect)

The TTL is applied in a command separate from the increment and only on the first
increment, so a transient failure of the expiry command leaves the key counting with
no window.

1.1 WHEN a counter key's `incr` succeeds but the subsequent `expire` fails on the first failure for that key THEN the system leaves the key at value 1 with no TTL
1.2 WHEN a key that is counting with no TTL receives further failures THEN the system never retries `expire` (because `count != 1`) and the key retains no TTL
1.3 WHEN the per-IP counter key has lost its TTL THEN the system keeps the IP locked permanently, since `clear` never resets the IP key and there is no other recovery path
1.4 WHEN the account counter key has lost its TTL THEN the system keeps the account locked until a successful login triggers `clear`, instead of the window expiring on its own

### Expected Behavior (Correct)

A counter key must always carry a TTL so its window always expires on its own.

2.1 WHEN a counter key is first created on a failure THEN the system SHALL ensure the key carries a TTL even if the expiry step encounters a transient failure
2.2 WHEN a subsequent failure is recorded on a key that is missing its TTL THEN the system SHALL (re)assert the TTL so the key can expire
2.3 WHEN the per-IP counter key is counting THEN the system SHALL guarantee it always has a TTL so the IP lockout expires on its own without relying on `clear`
2.4 WHEN the account counter key is counting THEN the system SHALL guarantee it always has a TTL so the account lockout expires on its own without relying on a successful login

### Unchanged Behavior (Regression Prevention)

3.1 WHEN a counter key's first failure is recorded THEN the system SHALL CONTINUE TO anchor the TTL at the first failure (fixed window of `WINDOW_SECONDS` / `window_seconds`), not reset the TTL on every subsequent failure
3.2 WHEN Redis is unavailable THEN the system SHALL CONTINUE TO fail open (allow the attempt) per the module's deliberate tradeoff
3.3 WHEN a successful login occurs THEN the system SHALL CONTINUE TO clear only the account key and never the per-IP key
3.4 WHEN counting login failures THEN the system SHALL CONTINUE TO use the existing thresholds (MAX_ACCOUNT_FAILURES=5, MAX_IP_FAILURES=50, WINDOW_SECONDS=15min) and the two-dimension (account + IP) counting
3.5 WHEN a failure is recorded under the per-key TTL window THEN the system SHALL CONTINUE TO let the window expire naturally on its own, rather than extending it on each hit (fixed window, not sliding window)
