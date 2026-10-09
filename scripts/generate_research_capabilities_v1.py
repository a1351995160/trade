"""生成同源能力说明，并通过公共preview检查全部目录示例。"""
import argparse
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'src'))

from chanlun_trader.research_factory.research_capabilities_v1 import capabilities, render_markdown
from chanlun_trader.research_factory.strategy_submission_v1 import StrategySubmissionV1


class MetadataOnlyProvider:
    def catalog(self):
        return {'datasets': [{'dataset_id': 'documentation_example', 'symbols': ['000003.SZ', '600004.SH'],
                             'start': 20230102, 'end': 20231229, 'metadata_hash': 'DOCUMENTATION_METADATA_ONLY'},
            {'dataset_id': 'documentation_full_universe', 'universe_id': 'documentation_three_boards',
             'adapter': 'TDX_FULL_UNIVERSE_V1', 'target_symbols': ['000003.SZ', '300004.SZ', '600004.SH'],
             'target_count': 3, 'by_board': {board: {'target_count': 1, 'cached_count': 0}
                for board in ('SZ_MAIN', 'SH_MAIN', 'CHINEXT')},
             'completeness': 'UNIVERSE_COMPLETENESS_UNKNOWN', 'start': 20230102, 'end': 20231229,
             'metadata_hash': 'DOCUMENTATION_METADATA_ONLY'}]}

    def prepare(self, *args, **kwargs):
        raise AssertionError('DOCUMENTATION_MUST_NOT_READ_MARKET_DATA')


def check_examples(snapshot):
    def no_authority(reference):
        raise AssertionError('DOCUMENTATION_MUST_NOT_AUTHORIZE_EXECUTION')
    results = {}
    with TemporaryDirectory(prefix='capability_preview_') as temporary:
        root = Path(temporary).resolve()
        service = StrategySubmissionV1(MetadataOnlyProvider(), no_authority, root)
        for name, rule in snapshot['examples'].items():
            request = {'strategy_id': name, 'rule': rule, 'dataset_id': 'documentation_example',
                'feature_start': 20230102, 'account_start': 20230403, 'account_end': 20230410,
                'symbols': ['000003.SZ', '600004.SH'], 'initial_cash': 50000,
                'max_positions': 2, 'max_symbol_exposure_bps': 5000, 'costs': ['BASE', 'STRESS'],
                'benchmark': 'FULL_POOL_BUY_HOLD', 'purpose': 'EXPLORATORY',
                'authorization_ref': 'DOCUMENTATION_NO_EXECUTION_AUTHORITY'}
            if rule['version'] == 'RESEARCH_RULE_STRATEGY_V4':
                from chanlun_trader.research_factory.universe_execution_profile_v1 import execution_profile, SEGMENTED_PROFILE
                from chanlun_trader.research_factory.universe_research_report_v2 import default_observation_plan
                request.pop('symbols')
                request.update(version='FULL_UNIVERSE_SUBMISSION_V3', dataset_id='documentation_full_universe',
                    universe_id='documentation_three_boards', benchmark='CASH_AND_PRICE_REFERENCE',
                    account_scope='DATA_QUALIFIED', execution_profile=execution_profile(SEGMENTED_PROFILE, 6),
                    observation_plan=default_observation_plan({'account_start': request['account_start'], 'account_end': request['account_end']}))
                preview = service.preview(request)
                if preview['status'] != 'PREVIEW_ONLY_CONTENT_AND_AUTHORIZATION_NOT_CHECKED' or preview['coverage']['target_count'] != 3:
                    raise ValueError('DOCUMENTATION_LONG_UNIVERSE_PREVIEW_BOUNDARY_CHANGED')
                results[name] = {'status': preview['status'], 'rule_identity': preview['rule_identity'],
                                 'long_universe': {'submission_version': request['version'], 'target_count': 3,
                                                  'execution_profile': request['execution_profile']['profile_id']}}
                continue
            preview = service.preview(request)
            if preview['status'] != 'PREVIEW_ONLY_CONTENT_AND_AUTHORIZATION_NOT_CHECKED':
                raise ValueError('DOCUMENTATION_PREVIEW_BOUNDARY_CHANGED')
            full_request = {key: value for key, value in request.items() if key != 'symbols'}
            full_request.update(version='FULL_UNIVERSE_SUBMISSION_V1',
                dataset_id='documentation_full_universe', universe_id='documentation_three_boards',
                benchmark='CASH_AND_PRICE_REFERENCE')
            full_preview = service.preview(full_request)
            qualified_request = {**full_request, 'version': 'FULL_UNIVERSE_SUBMISSION_V2', 'account_scope': 'DATA_QUALIFIED'}
            qualified_preview = service.preview(qualified_request)
            if (full_preview['status'] != preview['status'] or full_preview['coverage']['target_count'] != 3
                    or full_preview['rule_identity'] != preview['rule_identity']
                    or qualified_preview['rule_identity'] != preview['rule_identity']
                    or qualified_preview['coverage']['target_count'] != 3
                    or qualified_preview['request']['symbols'] != full_preview['request']['symbols']):
                raise ValueError('DOCUMENTATION_FULL_UNIVERSE_PREVIEW_BOUNDARY_CHANGED')
            results[name] = {'status': preview['status'], 'rule_identity': preview['rule_identity'],
                'full_universe': {'status': full_preview['status'], 'target_count': 3,
                                  'completeness': full_preview['coverage']['completeness']},
                'qualified_universe': {'status': qualified_preview['status'], 'target_count': 3,
                                      'account_scope': qualified_preview['request']['account_scope']}}
        if list(root.iterdir()):
            raise ValueError('DOCUMENTATION_PREVIEW_CREATED_ARTIFACTS')
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--output', type=Path, default=ROOT / 'docs' / 'RESEARCH_CAPABILITIES.md')
    args = parser.parse_args(argv)
    snapshot = capabilities()
    examples = check_examples(snapshot)
    expected = render_markdown(snapshot)
    if args.check:
        if not args.output.exists() or args.output.read_text(encoding='utf-8') != expected:
            print('RESEARCH_CAPABILITIES_DOCUMENT_STALE', file=sys.stderr)
            return 1
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(expected, encoding='utf-8')
    print(json.dumps({'status': 'MATCHED' if args.check else 'GENERATED', 'examples': examples,
                      'acceptance': snapshot['acceptance']}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
