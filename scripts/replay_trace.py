"""Audit an existing JSONL trace without executing the scheduler."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from simulator import replay_trace

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('trace', type=Path)
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.trace.read_text(encoding='utf-8').splitlines()]
    print(json.dumps(replay_trace(rows), indent=2))
