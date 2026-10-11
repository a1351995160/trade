"""专门批准的旧报告修复停派/结清；可在旧runtime完全不变时先部署。"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]


def main(argv=None):
    from chanlun_trader.research_factory.report_repair_stop_v2 import (
        ReportRepairStopV2, read_report_repair_terminal,
    )
    from chanlun_trader.research_factory.secure_file_reference_v1 import read_pinned_json
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('action', choices=('request', 'settle', 'verify'))
    cli.add_argument('--reference', required=True)
    cli.add_argument('--sha256', required=True)
    cli.add_argument('--approval-id')
    cli.add_argument('--summary-hash')
    args = cli.parse_args(argv)
    reference = {'path': args.reference, 'sha256': args.sha256}
    if args.action == 'request':
        if not args.approval_id or not args.summary_hash:
            cli.error('request requires dedicated Owner approval reference')
        result = ReportRepairStopV2.request(ROOT, read_pinned_json(reference, root=ROOT), approval_ref={
            'approval_id': args.approval_id, 'summary_hash': args.summary_hash})
    elif args.action == 'settle':
        result = ReportRepairStopV2.settle(ROOT, reference)
    else:
        value = read_report_repair_terminal(ROOT, reference)
        result = {'status': value['status'], 'identity': value['identity']}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
