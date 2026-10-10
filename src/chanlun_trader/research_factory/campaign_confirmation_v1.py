"""跨批家族的未来确认协议；没有已发布适用方法时只准备，不授予资格。"""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .common import stable_hash
from .exploration_governance import immutable, read_json
from .mutation_boundary import ObjectiveMutationLock


class CampaignConfirmationV1:
    def __init__(self, research, *, method_resolver=None):
        self.research = research
        self.root = Path(research.root) / 'confirmation'
        if method_resolver is None:
            from .formal_rule_adapter_v3 import method_resolver as published_resolver
            method_resolver = published_resolver
        self.method_resolver = method_resolver

    def preview(self, *, selected, not_before, route='FUTURE_OBSERVED'):
        material = self.research.confirmation_material()
        if route != 'FUTURE_OBSERVED':
            raise ValueError('SEALED_HISTORICAL_NOT_AUTHORIZED_OR_SUPPORTED')
        if (not isinstance(selected, list) or not selected or len(selected) != len(set(selected))
                or not set(selected) <= set(material['selected'])):
            raise ValueError('CONFIRMATION_SCREENED_SELECTION_REQUIRED')
        stamp = datetime.strptime(str(not_before), '%Y%m%d')
        if int(stamp.strftime('%Y%m%d')) <= int(datetime.now(timezone.utc).astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d')):
            raise ValueError('CONFIRMATION_FUTURE_START_REQUIRED')
        scope = deepcopy(material['method_scope'])
        support = {'applicable': False, 'reason': 'METHOD_SCOPE_NOT_PUBLISHED'}
        if self.method_resolver is not None:
            support = self.method_resolver(scope)
        # 部署注入的合成支持仅能建立合成协议，不能产生真实观察资格。
        if support.get('applicable') and (not support.get('method_id') or not support.get('evidence_hash')
                or support.get('scope_hash') != stable_hash(scope)
                or support.get('profile') != material['profile']):
            raise ValueError('CONFIRMATION_METHOD_EVIDENCE_SCOPE_CONFLICT')
        value = {'version': 'CAMPAIGN_CONFIRMATION_V1', 'route': route, 'not_before': not_before,
                 'family': material['attempts'], 'selected': sorted(selected),
                 'selection_policy': material['selection_policy'], 'exposures': material['exposures'],
                 'campaign_budget_hash': material['budget_hash'], 'method_scope': scope,
                 'method_support': support, 'profile': material['profile'],
                 'material_hash': stable_hash(material),
                 'status': 'WAITING_DATA' if support.get('applicable') else 'WAITING_METHOD_APPLICABILITY',
                 'strategy_qualified': False, 'confirmation_results_visible_to_design': False}
        return {**value, 'preview_identity': stable_hash(value)}

    def freeze(self, *, selected, not_before, preview_identity, route='FUTURE_OBSERVED'):
        value = self.preview(selected=selected, not_before=not_before, route=route)
        if value['preview_identity'] != preview_identity:
            raise ValueError('CONFIRMATION_FAMILY_OR_PREVIEW_CHANGED')
        with ObjectiveMutationLock.for_resource(self.root):
            path = self.root / 'PROTOCOL.json'
            if path.exists():
                existing = self.load()
                if existing['preview'] != value:
                    raise ValueError('CONFIRMATION_ALREADY_FROZEN')
                return existing
            body = {'preview': value, 'frozen_at': datetime.now(timezone.utc).isoformat()}
            body['protocol_identity'] = stable_hash(body)
            immutable(path, body)
            return body

    def load(self):
        value = read_json(self.root / 'PROTOCOL.json')
        if value['protocol_identity'] != stable_hash({key: item for key, item in value.items() if key != 'protocol_identity'}):
            raise ValueError('CONFIRMATION_PROTOCOL_CHANGED')
        preview = value['preview']
        if preview['preview_identity'] != stable_hash({key: item for key, item in preview.items() if key != 'preview_identity'}):
            raise ValueError('CONFIRMATION_PREVIEW_CHANGED')
        material = self.research.confirmation_material()
        # 后续探索可以追加，但已冻结家族成员与选择依据不可改写。
        current = {item['candidate_id']: item for item in material['attempts']}
        for prior in preview['family']:
            if current.get(prior['candidate_id']) != prior:
                raise ValueError('CONFIRMATION_FROZEN_FAMILY_CHANGED')
        if (material['selection_policy'] != preview['selection_policy']
                or material['method_scope'] != preview['method_scope']
                or material['profile'] != preview['profile']
                or any(item not in material['exposures'] for item in preview['exposures'])):
            raise ValueError('CONFIRMATION_SELECTION_OR_SCOPE_CHANGED')
        return value

    def readiness(self):
        protocol = self.load()
        scope = protocol['preview']['method_scope']
        support = self.method_resolver(scope) if self.method_resolver else {'applicable': False}
        if support != protocol['preview']['method_support'] or not support.get('applicable'):
            return {'status': 'WAITING_METHOD_APPLICABILITY', 'strategy_qualified': False}
        return {'status': 'WAITING_DATA', 'route': 'FUTURE_OBSERVED',
                'not_before': protocol['preview']['not_before'], 'strategy_qualified': False,
                'requirements': 'QUALIFIED_FUTURE_CAPTURE_AND_CANONICAL_FORMAL_ASSESSMENT'}

    def business_protocol(self, **trusted_dependencies):
        """独立业务账户走新版协议；旧正式方法与 504 日要求保持原合同。"""
        from .business_validation_protocol_v1 import BusinessValidationProtocolV1
        return BusinessValidationProtocolV1(self.research, **trusted_dependencies)
