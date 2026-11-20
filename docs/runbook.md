# Runbook: retries, stale acknowledgements and DLQ recovery

This is a review/runbook artifact for the simulation. Translate the checks into the actual broker, database and application controls before using them operationally.

## Duplicate effects detected

1. Trace one immutable job key across delivery attempts and receipt generations.
2. Locate the first applied effect, crash and subsequent redelivery.
3. Confirm whether effect application and deduplication state share one transaction boundary.
4. Verify the idempotency key includes the intended tenant and operation scope; reject payload mismatch rather than silently treating it as a duplicate.
5. Reconcile application state before repeating the operation. A missing broker acknowledgement does not prove the effect failed.

Use `crash_window--naive_backoff--seed0.jsonl` and its atomic counterpart for the modeled example. A policy change should reduce duplicate effects without falsely claiming fewer deliveries.

## Retry volume rises during an outage

1. Check downstream recovery time and whether failures occur before or after an effect.
2. Compare retry attempts per submitted job with the configured attempt budget.
3. Apply bounded exponential backoff with jitter and consider a circuit breaker or delayed redrive in the real system.
4. Keep poison inputs separate from transient failures; repeated attempts cannot repair a permanently invalid payload.
5. Compare acknowledgements and DLQ counts alongside latency. Low latency among a tiny surviving cohort is not successful recovery.

The configured 12-second outage exhausts immediate retries before recovery. Backoff improves settlement in this particular scenario but does not guarantee all jobs succeed.

## Stale acknowledgement rate rises

1. Compare processing duration with visibility timeout and renewal behavior.
2. Check that acknowledgements carry the current receipt token.
3. Ensure lease expiry does not incorrectly mark a still-running worker free.
4. Investigate concurrent execution of the same job key.
5. Extend or renew leases appropriately, but retain idempotency because crashes still exist.

The short-lease scenario demonstrates why effect deduplication cannot fix a lease that always expires before completion. Old receipt tokens must not delete a newer delivery.

## DLQ redrive

Before redrive, classify each job:

| Broker/application state | Review action |
|---|---|
| Poison DLQ, no effect | Correct or quarantine the payload; do not repeatedly redrive unchanged input |
| Non-poison DLQ, no effect | Verify downstream recovery, attempt policy and deadline |
| DLQ with effect | Reconcile and use the same idempotency key; do not assume another effect is needed |
| DLQ with worker still executing | Wait for or explicitly reconcile the stale execution; broker termination is not execution cancellation |
| Pending at observation budget | Continue observation; do not count it as completed, lost or dead-lettered |

## Verify after a change

Run the adversarial suite, the multi-seed scenarios and trace replay. Require conservation of jobs, no accepted acknowledgement without an effect, no duplicate atomic effects, correct stale-receipt rejection, and explicit pending/in-flight accounting. Then perform live integration/fault-injection tests for the production boundaries the model omits.
