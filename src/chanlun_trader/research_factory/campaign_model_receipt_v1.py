"""模型原始账单必须归属于原候选、上下文及已预留操作。"""
import hashlib
import json
from pathlib import Path

from .common import stable_hash


def model_receipt_proof(campaign_directory, operation, receipt_path, operations):
    root = Path(campaign_directory).absolute() / 'diagnosis_v4'
    path = Path(receipt_path).absolute()
    if (path.resolve() != path or not path.is_file() or path.is_symlink()
            or not path.is_relative_to(root) or path.name != 'INVOCATION.json'
            or path.parent.name != 'model' or path.parent.parent.parent != root):
        raise PermissionError('CAMPAIGN_MODEL_USAGE_RECEIPT_INVALID')
    directory = path.parent.parent
    def read(name):
        file = directory / name
        if file.resolve() != file or not file.is_file():
            raise PermissionError('CAMPAIGN_MODEL_USAGE_RECEIPT_INVALID')
        return json.loads(file.read_text(encoding='utf-8'))
    intent, context, guarantee = read('INTENT.json'), read('CONTEXT.json'), read('MODEL_GUARANTEE.json')
    candidate = intent.get('candidate_id')
    receipt = json.loads(path.read_bytes())
    usage = receipt.get('usage', {})
    names = {'input_tokens', 'output_tokens', 'total_tokens', 'cost_microunits', 'model_calls'}
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if (operation.get('kind') != 'MODEL' or not isinstance(candidate, str)
            or directory.name != candidate.lower()
            or operation['operation_id'] != candidate + '_MODEL'
            or operation['batch_id'] != intent.get('batch_id')
            or operation['subject_identity'] != stable_hash({'context': context, 'candidate': candidate})
            or receipt.get('context_hash') != stable_hash(context)
            or guarantee.get('enforced') is not True
            or operation.get('cost_bound_evidence') != guarantee.get('evidence_identity')
            or guarantee.get('max_tokens') != operation['upper_bounds']['model_tokens']
            or guarantee.get('max_cost_microunits') != operation['upper_bounds']['model_cost_microunits']
            or set(usage) != names or any(type(value) is not int or value < 0 for value in usage.values())
            or usage['total_tokens'] != usage['input_tokens'] + usage['output_tokens']
            or any(other['operation_id'] != operation['operation_id']
                and (other.get('usage_receipt') == str(path) or other.get('evidence_identity') == digest)
                for other in operations.values())):
        raise PermissionError('CAMPAIGN_MODEL_USAGE_RECEIPT_BINDING_INVALID')
    return usage, digest
