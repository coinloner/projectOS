"""Run ten additional matched pairs (groups 6–15); keep failed runs in evidence."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', required=True)
    parser.add_argument('--env-file', required=True)
    args = parser.parse_args()
    root = Path('/Users/coinloner/projectOS/project') / f'architecture-fault-matrix-{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:6]}'
    root.mkdir()
    scenarios = ['pre-input-read', 'post-input-read', 'pre-formal-commit',
                 'pre-formal-commit-twice', 'post-formal-commit']
    manifest = dict(scope='module matched fault experiments, not E2E', bundle=args.bundle, pairs=[])
    script = Path(__file__).with_name('run_architecture_comparison.py')
    def arm_run(group, scenario, arm):
        log = root / f'group-{group}-{arm}.log'
        command = [sys.executable, str(script), '--bundle', args.bundle, '--env-file', args.env_file,
                   '--module', 'task-persistence', '--arm', arm, '--group-id', str(group),
                   '--fault-scenario', scenario]
        with log.open('w') as stream:
            result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT)
        paths = [line.split('COMPARISON_STARTED ', 1)[1] for line in log.read_text().splitlines()
                 if line.startswith('COMPARISON_STARTED ')]
        return dict(arm=arm, returncode=result.returncode, log=str(log), evidence=paths[-1] if paths else None)
    print(f'MATRIX_STARTED {root}', flush=True)
    for group in range(6, 16):
        scenario = scenarios[(group - 6) // 2]
        print(f'GROUP_STARTED {group} {scenario}', flush=True)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(arm_run, group, scenario, arm) for arm in ('A', 'B')]
            arms = [future.result() for future in futures]
        manifest['pairs'].append(dict(group=group, scenario=scenario, arms=arms))
        (root / 'matrix.json').write_text(json.dumps(manifest, indent=2))
        print(f'GROUP_FINISHED {group} {json.dumps(arms)}', flush=True)
    print(f'MATRIX_FINISHED {root}', flush=True)


if __name__ == '__main__':
    main()
