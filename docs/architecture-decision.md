# Architecture decision: separate delivery, effects and settlement

**Status:** implemented in the simulator. This decision describes a model, not a production service.

## Problem

A worker may apply a side effect and crash before acknowledgement. The broker cannot infer that application state from the missing acknowledgement and may redeliver. A stale worker may also outlive its visibility lease and finish after another delivery—or after a DLQ transition.

## Decision

Model three separate concerns:

1. **Broker settlement.** Each delivery increments a receipt generation. An acknowledgement changes a job to `acked` only when the job is pending and the receipt equals the current generation. Expiry invalidates that receipt and schedules retry or DLQ.
2. **Worker execution.** A worker stays busy until its completion event, regardless of lease expiry. Expiry never frees the worker, cancels its effect or schedules a second completion. A post-effect crash leaves the receipt unacknowledged and delays worker availability until restart.
3. **Application effect.** Naive handling applies the effect on every successful attempt. Atomic handling checks the immutable key/payload, applies the effect and records the key in one transaction boundary. A repeated identical key suppresses only the effect; it does not suppress delivery or create an acknowledgement.

The ledger rejects an existing key with a different payload. Generated jobs always have unique keys and immutable payloads; tests exercise mismatch rejection independently.

## Atomicity assumption

`AtomicLedger.apply()` models an indivisible transaction containing both the application effect and its deduplication record. There is intentionally no crash point between those two writes. Crashes can occur afterward, before acknowledgement. This permits at-most-once modeled effects for that key while allowing repeated delivery.

This assumption could be satisfied by a single transactional database containing the effect and ledger update. It is not satisfied automatically by a database ledger plus a separate external API call. Outbox/inbox protocols, provider-side idempotency and reconciliation would need additional modeling and integration tests. The lab does not implement or claim those guarantees.

## Event ordering

The priority queue orders by logical time, then event category, then stable insertion sequence:

1. Lease expiry.
2. Worker completion.
3. Arrival or retry readiness.
4. Worker restart.

At exactly the lease deadline, expiry wins, so a completion's acknowledgement is stale. Tests lock this boundary down. Dispatch occurs after processing each event; waiting jobs use readiness order and the lowest available worker ID. Logical processing duration is strictly positive, preventing zero-duration retry loops.

## Randomness and comparisons

Draws are derived independently from `(seed, job ID, attempt, purpose)` using a SHA-256-derived seed. Purposes separate arrival, payload, poison status, processing duration, transient failure, crash and jitter. Scheduling changes never shift a shared PRNG stream.

Naive/backoff and atomic/backoff have identical processing durations and failure schedules; ledger lookup has no modeled timing overhead. Their acknowledgement and delivery metrics must match, which the tests enforce. Immediate retry changes scheduling; outage failures depend on completion time by design. Differences in that scenario reflect the configured outage model, not a universal theorem that jitter always succeeds.

## Retry and DLQ

Known pre-effect failures schedule retry after capped exponential full jitter. Crash-after-effect leaves the job leased until visibility expiry, after which the same retry policy applies. Exhausting the attempt budget moves the broker job to DLQ. A DLQ job may already have an effect, and an executing stale worker can create one later. Metrics therefore track `dlq_with_effect` separately.

Poison jobs fail before application on every attempt. They consume capacity and retry budget, then reach DLQ. The healthy scenario still includes poison jobs; it means healthy workers/downstream behavior, not zero invalid input.

## Termination

A run stops when every job is broker-terminal and no worker task is executing, or when the next event lies beyond the logical-time budget. Pending jobs at the budget are unsettled, not classified as lost. Broker-terminal jobs with in-flight workers do not count as a drained run.

## Deliberately omitted

Persistent broker storage and restart recovery, network partitions, lease renewal, cancellation semantics, clock skew, ledger retention/expiry, multi-tenant key scope, malicious payloads, network round trips and real CPU or storage cost. A production design requires those mechanisms and live integration/fault-injection testing.
