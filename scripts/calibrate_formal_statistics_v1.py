"""先冻结方法与源码，再一次性执行完整合成校准；不读取真实行情。"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
import time

from chanlun_trader.research_factory.bounded_research_v1 import _put, _read
from chanlun_trader.research_factory.common import stable_hash
from chanlun_trader.research_factory.formal_statistics_v1 import (
    CALIBRATION_SPEC, METHOD_HASH, SUPPORT, STRESS, calibration_record,
    load_calibration, source_hashes, summarize_records,
)
from chanlun_trader.research_factory.mutation_boundary import ObjectiveMutationLock


def run_condition(condition):
    records = []
    for i in range(CALIBRATION_SPEC['replicates']):
        records.append(calibration_record(condition, i))
        if (i + 1) % 256 == 0:
            print({'condition': condition, 'completed_replicates': i + 1}, flush=True)
    return condition, records


def run(output, workers):
    output = Path(output).absolute()
    if output.resolve() != output or workers not in (1, 2, 3, 4):
        raise ValueError('CALIBRATION_OUTPUT_OR_WORKERS_INVALID')
    output.mkdir(parents=True, exist_ok=True)
    with ObjectiveMutationLock.for_resource(output / 'calibration'):
        prereg_path = output / 'PREREGISTRATION.json'
        if prereg_path.exists():
            prereg = _read(prereg_path)
            if prereg['spec'] != CALIBRATION_SPEC or prereg['sources'] != source_hashes():
                raise ValueError('CALIBRATION_PREREGISTRATION_CHANGED')
        else:
            if any(p.name != 'calibration.mutation.lock' for p in output.iterdir()):
                raise ValueError('CALIBRATION_EMPTY_OUTPUT_REQUIRED')
            prereg = {'spec': CALIBRATION_SPEC, 'method_hash': METHOD_HASH, 'sources': source_hashes(),
                      'recorded_at': datetime.now(timezone.utc).isoformat(),
                      'real_data_accessed': False}
            _put(prereg_path, prereg)
        final = output / 'CALIBRATION.json'
        if final.exists():
            report = load_calibration(final)
            print({'status': 'EXISTING_COMPLETE', 'method_approved': report['method_approved']}, flush=True)
            return
        started = time.monotonic()
        conditions = (*SUPPORT, *STRESS)
        todo = [c for c in conditions if not (output / (c + '.json')).exists()]
        print({'status': 'PREREGISTERED', 'conditions_remaining': len(todo), 'workers': workers}, flush=True)
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(run_condition, c) for c in todo]
            for future in as_completed(futures):
                condition, records = future.result()
                _put(output / (condition + '.json'), {'condition': condition, 'records': records,
                     'preregistration_hash': stable_hash(prereg)})
                print({'completed_condition': condition, 'elapsed_seconds': round(time.monotonic() - started, 1)}, flush=True)
        if source_hashes() != prereg['sources']:
            raise ValueError('CALIBRATION_SOURCE_CHANGED_DURING_RUN')
        records = []
        for condition in conditions:
            partial = _read(output / (condition + '.json'))
            if partial['condition'] != condition or partial['preregistration_hash'] != stable_hash(prereg):
                raise ValueError('CALIBRATION_PARTIAL_IDENTITY_CONFLICT')
            records.extend(partial['records'])
        summary = summarize_records(records)
        report = {'schema_version': 'FORMAL_STATISTICS_CALIBRATION_REPORT_V1',
                  'method_hash': METHOD_HASH, 'preregistration_hash': stable_hash(prereg),
                  'records': records, 'records_hash': stable_hash(records), 'summary': summary,
                  'wall_seconds_this_run': time.monotonic() - started, 'real_data_accessed': False}
        _put(final, report)
        verified = load_calibration(final)
        print({'status': 'COMPLETE', 'method_approved': verified['method_approved'],
               'wall_seconds': round(report['wall_seconds_this_run'], 1), 'report': str(final)}, flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args()
    run(args.output, args.workers)
