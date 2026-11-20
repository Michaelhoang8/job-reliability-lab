from collections import Counter
from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'scripts'))
from simulator import AtomicLedger, Config, replay_trace, retry_delay, simulate
from run_experiments import SCENARIOS, POLICIES


class ReliabilityTests(unittest.TestCase):
    def config(self, **kwargs):
        base = Config(jobs=1, workers=1, arrival_window=0, processing_min=.5,
                      processing_max=.5, visibility=2, poison_fraction=0,
                      transient_probability=0, crash_probability=0, restart_delay=.1)
        return replace(base, **kwargs)

    def test_healthy_ack_implies_effect(self):
        result = simulate(self.config(jobs=20), 0)
        self.assertEqual(result['metrics']['acked'], 20)
        self.assertEqual(result['metrics']['effects_total'], 20)
        self.assertTrue(result['metrics']['drained'])

    def test_crash_between_effect_and_ack_produces_duplicate_only_without_ledger(self):
        script = {('J0000', 1): ('crash', .5), ('J0000', 2): ('ack', .5)}
        naive = simulate(self.config(), 0, atomic=False, backoff=False, scripted=script)
        atomic = simulate(self.config(), 0, atomic=True, backoff=False, scripted=script)
        self.assertEqual(naive['metrics']['acked'], 1)
        self.assertEqual(naive['metrics']['effects_total'], 2)
        self.assertEqual(atomic['metrics']['effects_total'], 1)
        self.assertEqual(naive['metrics']['deliveries'], atomic['metrics']['deliveries'])

    def test_poison_job_has_bounded_retries_and_no_effect(self):
        result = simulate(self.config(poison_fraction=1, max_attempts=3), 0, backoff=False)
        self.assertEqual(result['metrics']['deliveries'], 3)
        self.assertEqual(result['metrics']['poison_dlq'], 1)
        self.assertEqual(result['metrics']['effects_total'], 0)

    def test_expired_receipt_cannot_ack_a_new_delivery(self):
        result = simulate(self.config(workers=2, visibility=1, max_attempts=2,
                                      processing_min=2.5, processing_max=2.5), 0,
                          backoff=False, atomic=False, trace=True)
        self.assertEqual(result['metrics']['acked'], 0)
        self.assertEqual(result['metrics']['dlq'], 1)
        self.assertEqual(result['metrics']['stale_acks_ignored'], 2)
        self.assertEqual(result['metrics']['effects_total'], 2)
        self.assertEqual(result['metrics']['inflight_at_stop'], 0)

    def test_exact_lease_boundary_expires_before_completion(self):
        result = simulate(self.config(visibility=.5, max_attempts=1), 0, trace=True)
        events = [row['event'] for row in result['trace']]
        self.assertLess(events.index('lease_expired'), events.index('effect_applied'))
        self.assertEqual(result['metrics']['dlq_with_effect'], 1)
        self.assertEqual(result['metrics']['acked'], 0)

    def test_budget_cutoff_preserves_pending_not_failed_label(self):
        result = simulate(self.config(budget=.1), 0)
        self.assertEqual(result['metrics']['pending_at_budget'], 1)
        self.assertEqual(result['metrics']['dlq'], 0)
        self.assertFalse(result['metrics']['drained'])

    def test_terminal_broker_state_is_not_drained_while_worker_still_runs(self):
        short = simulate(self.config(visibility=.2, max_attempts=1, budget=.3), 0)
        self.assertEqual(short['metrics']['dlq'], 1)
        self.assertEqual(short['metrics']['pending_at_budget'], 0)
        self.assertEqual(short['metrics']['inflight_at_stop'], 1)
        self.assertFalse(short['metrics']['drained'])
        finished = simulate(self.config(visibility=.2, max_attempts=1), 0)
        self.assertEqual(finished['metrics']['dlq_with_effect'], 1)
        self.assertTrue(finished['metrics']['drained'])

    def test_backoff_is_seeded_capped_full_jitter(self):
        c = self.config(backoff_base=2, backoff_cap=5)
        for attempt in range(1, 30):
            delay = retry_delay(c, 3, 'J0000', attempt, True)
            self.assertEqual(delay, retry_delay(c, 3, 'J0000', attempt, True))
            self.assertGreaterEqual(delay, 0)
            self.assertLessEqual(delay, min(5, 2 * 2 ** (attempt - 1)))
            self.assertEqual(retry_delay(c, 3, 'J0000', attempt, False), 0)

    def test_idempotency_key_payload_mismatch_rejected(self):
        ledger, effects = AtomicLedger(), Counter()
        self.assertTrue(ledger.apply('key', 42, effects))
        self.assertFalse(ledger.apply('key', 42, effects))
        with self.assertRaises(ValueError):
            ledger.apply('key', 43, effects)
        self.assertEqual(effects['key'], 1)

    def test_many_seeds_conserve_jobs_and_atomic_effects(self):
        for scenario, c in SCENARIOS.items():
            for seed in range(5):
                c = replace(c, jobs=30)
                naive = simulate(c, seed, atomic=False)
                atomic = simulate(c, seed, atomic=True)
                a, n = atomic['metrics'], naive['metrics']
                self.assertEqual(a['acked'] + a['dlq'] + a['pending_at_budget'], c.jobs)
                self.assertEqual(a['duplicate_effects'], 0)
                self.assertEqual(a['acked_without_effect'], 0)
                for key in ['acked', 'dlq', 'deliveries', 'stop_time', 'ack_latency_p95']:
                    self.assertEqual(a[key], n[key], (scenario, seed, key))

    def test_trace_replay_and_tamper_detection(self):
        result = simulate(self.config(crash_probability=.5), 0, trace=True)
        replay_trace(result['trace'])
        result['trace'][-1]['metrics']['effects_total'] += 1
        with self.assertRaises(ValueError):
            replay_trace(result['trace'])

    def test_all_published_traces_replay(self):
        paths = list((ROOT / 'results/traces').glob('*.jsonl'))
        self.assertEqual(len(paths), 7)
        for path in paths:
            replay_trace([json.loads(line) for line in path.read_text().splitlines()])


if __name__ == '__main__':
    unittest.main()
