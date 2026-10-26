"""Locate this CI job's retained pytest evidence without changing its bytes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat


FAILED_SUITES = {
    'failed-account-owner', 'failed-account-budget', 'failed-account-context',
    'failed-account-governance', 'failed-account-worker',
}
MANIFEST = 'pytest-resource-artifact-paths.json'


def plain_path(path, *, directory):
    """Check every existing component before using a marker or evidence root."""
    if not path.is_absolute() or '..' in path.parts or any(c in str(path) for c in '\r\n'):
        raise ValueError('absolute plain evidence path required')
    for component in [*reversed(path.parents), path]:
        info = component.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 1024:
            raise ValueError('linked or reparse evidence path rejected')
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise ValueError('unexpected evidence path type')


def evidence_plan(runner_temp, suite):
    runner_temp = Path(runner_temp)
    plain_path(runner_temp, directory=True)
    if suite in FAILED_SUITES:
        base = runner_temp / (suite + '-tests')
        paths = [
            'failed-account-source-identity.json', MANIFEST,
            (runner_temp / 'failed-account-*-resource-evidence/**').as_posix(),
            (runner_temp / 'failed-account-*-tests/**/SUPERVISOR_*').as_posix(),
        ]
        suffixes = ['**/SUPERVISOR_*']
    elif suite == 'workflow':
        base = runner_temp / 'autonomous-completion-tests'
        suffixes = ['**/DATA_WORKER*.json', '**/DATA_WORKER_EXPOSURES.jsonl']
        paths = [MANIFEST, *[(base / 'test_actual_windows_venv_data_*' / s).as_posix() for s in suffixes]]
    elif suite == 'full-universe':
        base = runner_temp / 'full-universe-tests'
        suffixes = ['**/SCALE_RESOURCE_EVIDENCE.json']
        paths = [MANIFEST, *[(base / s).as_posix() for s in suffixes]]
    else:
        raise ValueError('unsupported CI evidence suite')

    marker = base / 'RESEARCH_FACTORY_TMP_ROOT.json'
    short_root = None
    marker_sha = None
    # The original basetemp remains valid for tests that do not request tmp_path.
    if marker.exists() or marker.is_symlink():
        plain_path(marker, directory=False)
        raw = marker.read_bytes()
        if len(raw) > 4096:
            raise ValueError('oversized pytest root marker')
        value = json.loads(raw.decode('utf-8'))
        if set(value) != {'root', 'exclusive'} or value['exclusive'] is not True or not isinstance(value['root'], str):
            raise ValueError('exclusive pytest root marker required')
        short_root = Path(value['root'])
        if short_root.parent != runner_temp or not re.fullmatch(r'rf_[a-z0-9_]{8}', short_root.name):
            raise ValueError('pytest root must be this job temp directory direct child')
        plain_path(short_root, directory=True)
        marker_sha = hashlib.sha256(raw).hexdigest()
        paths += [(short_root / s).as_posix() for s in suffixes]

    if suite == 'failed-account-worker':
        roots = [base] + ([short_root] if short_root else [])
        resources = [p for root in roots for p in root.rglob('SUPERVISOR_RESOURCE.json')]
        if not resources:
            raise ValueError('worker supervisor resource evidence missing')
        for resource in resources:
            plain_path(resource, directory=False)
    return {'suite': suite, 'basetemp': str(base), 'short_root': str(short_root) if short_root else None,
            'marker_sha256': marker_sha, 'artifact_paths': paths, 'evidence_bytes_modified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', required=True)
    args = parser.parse_args()
    plan = evidence_plan(os.environ['RUNNER_TEMP'], args.suite)
    with Path(MANIFEST).open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(plan, stream, ensure_ascii=True, indent=2)
        stream.write('\n')
    with Path(os.environ['GITHUB_OUTPUT']).open('a', encoding='utf-8', newline='\n') as stream:
        stream.write('paths<<PYTEST_RESOURCE_PATHS\n' + '\n'.join(plan['artifact_paths']) + '\nPYTEST_RESOURCE_PATHS\n')


if __name__ == '__main__':
    main()
