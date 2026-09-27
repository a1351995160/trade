"""CLI 与 UI 共用固定历史数据装配；不从配置加载代码。"""
from pathlib import Path


def qualified_research_loader(root):
    root = Path(root).absolute()
    def qualified_loader(manifest, strategy):
        from scripts.run_historical_process_research_v1 import build_bundle
        from chanlun_trader.research_factory.rule_account_backend_v2 import rule_input_identity, rule_window
        data_root = Path(manifest['data_root']).absolute()
        if (data_root.resolve() != data_root or '..' in data_root.parts or not data_root.is_relative_to(root)
                or manifest.get('profile') != 'HISTORICAL_MODELED'
                or manifest.get('candidate_capability') != 'RESEARCH_RULE_STRATEGY_V2'):
            raise ValueError('LIFECYCLE_QUALIFIED_DATA_SCOPE_INVALID')
        window, bundle, _ = build_bundle(data_root, symbols=manifest['window']['symbols'])
        identity = rule_input_identity(bundle, window)
        if rule_window(window) != rule_window(manifest['window']) or identity != manifest['input_identity']:
            raise ValueError('LIFECYCLE_QUALIFIED_INPUT_CHANGED')
        bundle['input_identity'] = identity
        return bundle, tuple(bundle['events'])
    return qualified_loader
