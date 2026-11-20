# Metric dictionary

`results/runs.csv` contains one row per scenario, policy and seed. `summary.csv` groups the 30 seeds for each comparison. Every time value is simulated seconds.

| Field | Definition |
|---|---|
| jobs | Submitted jobs, including jobs whose arrival lies beyond a short observation budget |
| acked | Broker accepted an acknowledgement for the current receipt |
| dlq | Attempt budget exhausted; broker dead-lettered the job |
| pending_at_budget | Jobs still pending when the simulation stopped; not a lost-job estimate |
| inflight_at_stop | Worker tasks still executing, including jobs already broker-terminal |
| drained | No pending jobs and no executing worker tasks |
| stop_time | Last processed event time or configured observation budget |
| deliveries | Actual dispatched attempts, including first attempts and poison attempts |
| retry_amplification | Deliveries / submitted jobs; can be below one when the budget ends before first delivery |
| effects_total | Every applied effect, including duplicates and late post-DLQ effects |
| unique_jobs_with_effect | Number of distinct job keys with at least one effect |
| duplicate_effects | Sum of max(effect count − 1, 0) across job keys |
| acked_without_effect | Invariant violation count; must be zero |
| dlq_with_effect | DLQ jobs with an effect as of the simulation stop time |
| poison_dlq | Jobs marked poison that reached DLQ |
| nonpoison_dlq_without_effect | Non-poison DLQ jobs with no effect as of stop; separate from pending and from already-applied DLQ |
| pending_with_effect | Unsettled jobs whose effect has already happened |
| ack_latency_p50 / ack_latency_p95 | Submit/arrival to accepted acknowledgement among acknowledged jobs only |
| first_delivery_lag_p95 | Arrival to first dispatch among jobs dispatched at least once |
| peak_deliveries_per_second | Maximum dispatch count in a one-logical-second bucket; not real broker throughput |
| visibility_timeouts | Current receipts invalidated by lease expiry |
| stale_acks_ignored | Completion acknowledgements rejected because receipt/status no longer matches |
| failures_before_effect | Poison, configured outage or random transient failure before effect application |
| crashes | Simulated worker failures after effect handling and before acknowledgement |

Job conservation: `acked + dlq + pending_at_budget = jobs`. DLQ-with-effect is a subset of DLQ; it must not be added as a separate broker disposition. A broker-terminal job may have an active worker, so zero pending does not imply drained.

Quantiles use linear interpolation. Empty acknowledged cohorts produce an empty CSV cell / null trace value, never zero latency. Summary latency values are means of nonempty per-run p95 values, not pooled quantiles. `runs_with_ack_latency` gives the denominator. In the budget-cutoff scenario, one of 30 runs has no acknowledgement and is excluded from that latency mean. Queue lag similarly describes only jobs that started; it is not a latency estimate for pending, never-delivered jobs.

No `lost_jobs` metric is claimed. Proving durable loss would require a persistence and recovery model that is outside this study. The lab instead reports terminal-without-effect and unsettled populations explicitly.
