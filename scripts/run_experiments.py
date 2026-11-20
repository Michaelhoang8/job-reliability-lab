"""Execute seeded logical-time simulations and save inspectable outcomes."""
from dataclasses import asdict, replace
from datetime import datetime, timezone
import argparse
import csv
import json
from pathlib import Path
import platform
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from simulator import Config, replay_trace, simulate

SCENARIOS = {
    'healthy': replace(Config(), transient_probability=0, crash_probability=0),
    'crash_window': replace(Config(), crash_probability=.4),
    'downstream_outage': replace(Config(), workers=24, arrival_window=1,
                                processing_min=.2, processing_max=.5, transient_probability=0,
                                crash_probability=0, poison_fraction=0, outage_until=12),
    'short_visibility': replace(Config(), arrival_window=5, visibility=.6,
                               processing_min=2, processing_max=4, max_attempts=4, budget=600),
    'budget_cutoff': replace(Config(), workers=4, arrival_window=15, processing_min=1,
                            processing_max=3, visibility=5, budget=5),
}
POLICIES = {'naive_backoff': (False, True), 'atomic_backoff': (True, True), 'atomic_immediate': (True, False)}


def csv_write(path, rows):
    with path.open('w', encoding='utf-8', newline='') as file:
        writer = csv.DictWriter(file, list(rows[0]), lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


def run(seed_count, destination):
    if seed_count < 1:
        raise ValueError('seed_count must be positive')
    destination = destination if destination.is_absolute() else ROOT / destination
    destination.mkdir(parents=True, exist_ok=True)
    (destination / 'traces').mkdir(exist_ok=True)
    runs = []
    for scenario, config in SCENARIOS.items():
        for policy, (atomic, backoff) in POLICIES.items():
            for seed in range(seed_count):
                capture = seed == 0 and (policy == 'atomic_backoff' or scenario == 'crash_window' and policy == 'naive_backoff'
                                        or scenario == 'downstream_outage' and policy == 'atomic_immediate')
                outcome = simulate(config, seed, atomic=atomic, backoff=backoff, trace=capture)
                runs.append({'scenario': scenario, 'policy': policy, 'seed': seed, **outcome['metrics']})
                if capture:
                    replay_trace(outcome['trace'])
                    path = destination / 'traces' / f'{scenario}--{policy}--seed0.jsonl'
                    path.write_text(''.join(json.dumps(record, separators=(',', ':')) + '\n' for record in outcome['trace']), encoding='utf-8')
            print(f'{scenario}/{policy}: {seed_count} seeds checked', flush=True)
    csv_write(destination / 'runs.csv', runs)
    summaries = []
    for scenario in SCENARIOS:
        for policy in POLICIES:
            selected = [r for r in runs if r['scenario'] == scenario and r['policy'] == policy]
            row = {'scenario': scenario, 'policy': policy, 'seeds': len(selected)}
            for key in ['acked', 'dlq', 'pending_at_budget', 'inflight_at_stop', 'duplicate_effects',
                        'retry_amplification', 'dlq_with_effect', 'nonpoison_dlq_without_effect',
                        'first_delivery_lag_p95', 'ack_latency_p95', 'peak_deliveries_per_second',
                        'visibility_timeouts', 'stale_acks_ignored']:
                values = [r[key] for r in selected if r[key] is not None]
                row[f'mean_{key}'] = statistics.mean(values) if values else None
                if key == 'ack_latency_p95':
                    row['runs_with_ack_latency'] = len(values)
            row['drained_runs'] = sum(r['drained'] for r in selected)
            summaries.append(row)
    csv_write(destination / 'summary.csv', summaries)
    metadata = {'executed_at_utc': datetime.now(timezone.utc).isoformat(), 'python': platform.python_version(),
                'model': 'discrete-event simulation; logical seconds, no real broker or deployment',
                'seeds': list(range(seed_count)), 'runs': len(runs),
                'scenarios': {k: asdict(v) for k, v in SCENARIOS.items()},
                'policies': {k: {'atomic_ledger': v[0], 'full_jitter_backoff': v[1]} for k, v in POLICIES.items()},
                'event_tie_order': ['lease_expiry', 'completion', 'arrival_or_retry_readiness', 'worker_restart'],
                'comparison_control': 'Draws indexed by seed, job id, attempt and purpose; naive/atomic backoff schedules identical',
                'ack_latency_scope': 'Per-run p95 among acknowledged jobs only; summary is mean of nonempty per-run p95 values',
                'atomicity_assumption': 'Deduplication record and modeled effect commit atomically in one ledger boundary'}
    (destination / 'environment.json').write_text(json.dumps(metadata, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seeds', type=int, default=30)
    parser.add_argument('--output', type=Path, default=ROOT / 'results')
    args = parser.parse_args()
    run(args.seeds, args.output)
