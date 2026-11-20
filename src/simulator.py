"""Discrete-event at-least-once delivery model. All times are simulated seconds."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
import hashlib
import heapq
import json
import random


@dataclass(frozen=True)
class Config:
    jobs: int = 120
    workers: int = 6
    arrival_window: float = 20.0
    processing_min: float = 0.4
    processing_max: float = 1.8
    visibility: float = 3.0
    max_attempts: int = 6
    transient_probability: float = 0.08
    crash_probability: float = 0.25
    poison_fraction: float = 0.05
    outage_until: float = 0.0
    backoff_base: float = 1.0
    backoff_cap: float = 16.0
    restart_delay: float = 2.0
    budget: float = 180.0

    def __post_init__(self):
        if self.jobs < 1 or self.workers < 1 or self.max_attempts < 1:
            raise ValueError('jobs, workers and max_attempts must be positive')
        if not (0 < self.processing_min <= self.processing_max) or self.visibility <= 0:
            raise ValueError('Processing duration and visibility must be positive')
        if any(not 0 <= v <= 1 for v in (self.transient_probability, self.crash_probability, self.poison_fraction)):
            raise ValueError('Probabilities must be in [0, 1]')
        if min(self.arrival_window, self.outage_until, self.backoff_base, self.backoff_cap, self.restart_delay, self.budget) < 0:
            raise ValueError('Durations must be nonnegative')


@dataclass
class Job:
    id: str
    arrival: float
    payload: int
    poison: bool
    status: str = 'pending'
    attempts: int = 0
    receipt: int | None = None
    first_delivery: float | None = None
    terminal_at: float | None = None


def draw(seed, job, attempt, purpose):
    """Identity-indexed randomness: scheduling changes never shift another draw."""
    value = f'{seed}|{job}|{attempt}|{purpose}'.encode()
    return random.Random(int.from_bytes(hashlib.sha256(value).digest()[:8], 'big')).random()


def retry_delay(config, seed, job, attempt, backoff):
    if not backoff:
        return 0.0
    ceiling = min(config.backoff_cap, config.backoff_base * 2 ** min(attempt - 1, 30))
    return draw(seed, job, attempt, 'backoff') * ceiling


def quantile(values, q):
    if not values:
        return None
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


class AtomicLedger:
    """The model's transaction boundary includes dedup key AND effect update.

    This is an assumption, not a claim about external APIs or a real broker.
    A reused key with a different payload is rejected, never silently deduped.
    """
    def __init__(self):
        self.payloads = {}

    def apply(self, key, payload, effects):
        if key in self.payloads:
            if self.payloads[key] != payload:
                raise ValueError('Idempotency key reused with a different payload')
            return False
        self.payloads[key] = payload
        effects[key] += 1
        return True


def simulate(config: Config, seed: int, *, atomic=True, backoff=True, trace=False, scripted=None):
    """Run one logical-time scenario, optionally overriding attempt outcomes in tests.

    Same-time order: lease expiry, completion, arrival/retry readiness, restart.
    Expiry invalidates the receipt only; a busy worker keeps running and may
    apply an effect after its lease or after the broker dead-letters the job.
    """
    events, ready = [], []
    sequence = 0
    workers = set(range(config.workers))
    active = {}  # worker -> (job id, attempt); expiry never releases a worker.
    effects, dispatch_buckets = Counter(), Counter()
    ledger = AtomicLedger()
    logs = []
    counters = Counter()
    now = 0.0
    jobs = {
        f'J{i:04d}': Job(f'J{i:04d}', draw(seed, f'J{i:04d}', 0, 'arrival') * config.arrival_window,
                        1 + int(draw(seed, f'J{i:04d}', 0, 'payload') * 1000),
                        draw(seed, f'J{i:04d}', 0, 'poison') < config.poison_fraction)
        for i in range(config.jobs)
    }

    def log(event, job=None, **values):
        if trace:
            logs.append({'time': round(now, 9), 'event': event, **({'job': job.id} if job else {}), **values})

    def schedule(at, priority, event, *data):
        nonlocal sequence
        sequence += 1
        heapq.heappush(events, (at, priority, sequence, event, data))

    def retry(job, reason):
        job.receipt = None
        if job.attempts >= config.max_attempts:
            job.status, job.terminal_at = 'dlq', now
            log('dlq', job, attempts=job.attempts, reason=reason, effect_count=effects[job.id])
        else:
            delay = retry_delay(config, seed, job.id, job.attempts, backoff)
            log('retry_scheduled', job, after=delay, reason=reason)
            schedule(now + delay, 2, 'ready', job.id)

    def dispatch():
        while workers and ready:
            _, job_id = heapq.heappop(ready)
            job = jobs[job_id]
            if job.status != 'pending' or job.receipt is not None:
                continue
            worker = min(workers)
            workers.remove(worker)
            job.attempts += 1
            job.receipt = job.attempts
            attempt = job.attempts
            active[worker] = (job.id, attempt)
            if job.first_delivery is None:
                job.first_delivery = now
            counters['deliveries'] += 1
            dispatch_buckets[int(now)] += 1
            duration = config.processing_min + draw(seed, job.id, attempt, 'duration') * (config.processing_max - config.processing_min)
            outcome = ('poison' if job.poison else 'transient'
                       if draw(seed, job.id, attempt, 'transient') < config.transient_probability else 'crash'
                       if draw(seed, job.id, attempt, 'crash') < config.crash_probability else 'ack')
            if scripted and (job.id, attempt) in scripted:
                outcome, duration = scripted[(job.id, attempt)]
            log('delivered', job, worker=worker, attempt=attempt, receipt=attempt, lease_until=now + config.visibility)
            schedule(now + config.visibility, 0, 'expired', job.id, attempt)
            schedule(now + duration, 1, 'completed', worker, job.id, attempt, outcome)

    if trace:
        logs.append({'event': 'setup', 'time': 0.0, 'seed': seed, 'config': asdict(config),
                     'atomic': atomic, 'backoff': backoff,
                     'jobs': [{'id': j.id, 'arrival': j.arrival, 'payload': j.payload, 'poison': j.poison} for j in jobs.values()]})
    for job in jobs.values():
        schedule(job.arrival, 2, 'ready', job.id)

    while events:
        at, _, seq, event, data = heapq.heappop(events)
        if at > config.budget:
            now = config.budget
            break
        now = at
        if event == 'ready':
            job = jobs[data[0]]
            if job.status == 'pending':
                heapq.heappush(ready, (seq, job.id))
                log('ready', job)
        elif event == 'expired':
            job_id, receipt = data
            job = jobs[job_id]
            if job.status == 'pending' and job.receipt == receipt:
                counters['timeouts'] += 1
                log('lease_expired', job, receipt=receipt)
                retry(job, 'visibility_timeout')
        elif event == 'restart':
            workers.add(data[0])
            log('worker_restarted', worker=data[0])
        elif event == 'completed':
            worker, job_id, attempt, outcome = data
            if active.get(worker) != (job_id, attempt):
                raise AssertionError('Worker completion does not match its active task')
            del active[worker]
            job = jobs[job_id]
            # A modeled downstream outage fails before the effect. Identity-indexed
            # random failures are otherwise identical across policy comparisons.
            if outcome != 'poison' and now < config.outage_until:
                outcome = 'transient'
            if outcome in ('poison', 'transient'):
                counters['failures_before_effect'] += 1
                log('failed_before_effect', job, worker=worker, attempt=attempt, reason=outcome)
                if job.status == 'pending' and job.receipt == attempt:
                    retry(job, outcome)
            else:
                applied = ledger.apply(job.id, job.payload, effects) if atomic else True
                if not atomic:
                    effects[job.id] += 1
                log('effect_applied' if applied else 'effect_deduplicated', job, attempt=attempt,
                    worker=worker, effect_count=effects[job.id], broker_status=job.status)
                if outcome == 'crash':
                    counters['crashes'] += 1
                    log('crashed_before_ack', job, worker=worker, attempt=attempt)
                elif job.status == 'pending' and job.receipt == attempt:
                    job.status, job.terminal_at, job.receipt = 'acked', now, None
                    log('ack_accepted', job, receipt=attempt)
                else:
                    counters['stale_acks'] += 1
                    log('ack_ignored_stale', job, receipt=attempt, current_receipt=job.receipt,
                        broker_status=job.status)
            if outcome == 'crash':
                schedule(now + config.restart_delay, 3, 'restart', worker)
            else:
                workers.add(worker)
        dispatch()
        if all(j.status != 'pending' for j in jobs.values()) and not active:
            break

    acked = [j for j in jobs.values() if j.status == 'acked']
    dlq = [j for j in jobs.values() if j.status == 'dlq']
    pending = [j for j in jobs.values() if j.status == 'pending']
    ack_latencies = [j.terminal_at - j.arrival for j in acked]
    queue_lags = [j.first_delivery - j.arrival for j in jobs.values() if j.first_delivery is not None]
    metrics = {
        'jobs': config.jobs, 'acked': len(acked), 'dlq': len(dlq), 'pending_at_budget': len(pending),
        'inflight_at_stop': len(active), 'drained': not pending and not active,
        'stop_time': now, 'deliveries': counters['deliveries'],
        'retry_amplification': counters['deliveries'] / config.jobs,
        'effects_total': sum(effects.values()), 'unique_jobs_with_effect': len(effects),
        'duplicate_effects': sum(max(0, count - 1) for count in effects.values()),
        'acked_without_effect': sum(effects[j.id] == 0 for j in acked),
        'dlq_with_effect': sum(effects[j.id] > 0 for j in dlq),
        'poison_dlq': sum(j.poison for j in dlq),
        'nonpoison_dlq_without_effect': sum(not j.poison and effects[j.id] == 0 for j in dlq),
        'pending_with_effect': sum(effects[j.id] > 0 for j in pending),
        'ack_latency_p50': quantile(ack_latencies, .5), 'ack_latency_p95': quantile(ack_latencies, .95),
        'first_delivery_lag_p95': quantile(queue_lags, .95),
        'peak_deliveries_per_second': max(dispatch_buckets.values(), default=0),
        'crashes': counters['crashes'], 'visibility_timeouts': counters['timeouts'],
        'stale_acks_ignored': counters['stale_acks'], 'failures_before_effect': counters['failures_before_effect'],
    }
    if metrics['acked_without_effect'] or len(acked) + len(dlq) + len(pending) != config.jobs:
        raise AssertionError('Accounting invariant violated')
    if atomic and metrics['duplicate_effects']:
        raise AssertionError('Atomic idempotency invariant violated')
    if trace:
        logs.append({'event': 'summary', 'time': round(now, 9), 'metrics': metrics})
    return {'metrics': metrics, 'trace': logs,
            'jobs': {j.id: asdict(j) for j in jobs.values()}, 'effects': dict(effects)}


def replay_trace(records):
    """Audit event accounting without running the scheduler or trusting summary counts."""
    if not records or records[0]['event'] != 'setup' or records[-1]['event'] != 'summary':
        raise ValueError('Trace must have setup and summary records')
    ids = {j['id'] for j in records[0]['jobs']}
    effects, acked, dlq = Counter(), set(), set()
    prior = -1.0
    for event in records[1:-1]:
        if event['time'] < prior:
            raise ValueError('Trace logical time decreased')
        prior = event['time']
        job = event.get('job')
        if job is not None and job not in ids:
            raise ValueError('Unknown job id')
        if event['event'] == 'effect_applied':
            effects[job] += 1
        elif event['event'] == 'ack_accepted':
            if job in acked or job in dlq or not effects[job]:
                raise ValueError('Invalid accepted acknowledgement')
            acked.add(job)
        elif event['event'] == 'dlq':
            if job in dlq or job in acked:
                raise ValueError('Duplicate terminal event')
            dlq.add(job)
    actual = {'acked': len(acked), 'dlq': len(dlq), 'pending_at_budget': len(ids - acked - dlq),
              'effects_total': sum(effects.values()), 'duplicate_effects': sum(max(0, n - 1) for n in effects.values())}
    expected = records[-1]['metrics']
    for key, value in actual.items():
        if value != expected[key]:
            raise ValueError(f'Trace summary mismatch for {key}')
    return actual
