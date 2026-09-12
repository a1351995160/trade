"""只基于已导出的限窗子件验收、封装及原子交付；不再次访问原始源。"""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def load_validator():
    path=Path(__file__).with_name('validate_owner_execution_package.py')
    spec=importlib.util.spec_from_file_location('independent_owner_validator',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def finalize(stage,request,authorization,destination,run_backtest=False):
    """缺项只输出独立判定；全部通过才生成最终manifest并调用原账户入口。"""
    import pyarrow.parquet as pq
    from chanlun_trader.data.tdx.owner_export_v1 import sha,write_json,OWNER,START,END
    stage=Path(stage);validator=load_validator()
    component=validator.validate_components(stage,request)
    write_json(stage.parent/'INDEPENDENT_COMPONENT_VALIDATION.json',component)
    if component['status']!='PASS':return component
    roles={'daily_supplement':'daily_supplement.parquet','state_supplement':'state_supplement.parquet',
        'security_master':'security_master.parquet','units':'units.json','unit_source_evidence':'unit_source_evidence.json','actions_manifest':'actions_manifest.json'}
    files={}
    for role,name in roles.items():
        path=stage/name;rows=None;low=None;high=None
        if path.suffix=='.parquet':
            pf=pq.ParquetFile(path);rows=pf.metadata.num_rows
            column={'daily_supplement':'date','state_supplement':'trade_date','security_master':'effective_date'}[role]
            values=pq.read_table(path,columns=[column],use_threads=False).column(0).to_pylist()
            if values:low,high=min(values),max(values)
        files[role]={'file':name,'sha256':sha(path),'size':path.stat().st_size,'row_count':rows,'min_date':low,'max_date':high,
            'source_identity':component['component_hashes'][name],'schema_version':'OWNER_EXECUTION_EXPORT_V1'}
    manifest={'version':'OWNER_EXECUTION_EXPORT_V1','owner':OWNER,'authorization_sha256':sha(authorization),
        'start':START,'end':END,'files':files,'physical_window_attestation':{'owner':OWNER,'start':START,'end':END,'window_enforced_before_export':True}}
    write_json(stage/'OWNER_DELIVERY_MANIFEST.json',manifest)
    verdict=validator.validate_package(stage,request,sha(authorization))
    write_json(stage.parent/'INDEPENDENT_PACKAGE_VALIDATION.json',verdict)
    if verdict['status']!='PASS':return verdict
    destination=Path(destination)
    if destination.exists():raise FileExistsError('OWNER_DESTINATION_ALREADY_EXISTS_RECONCILE')
    temporary=destination.with_name(destination.name+'.pending')
    temporary.mkdir(parents=True,exist_ok=False)
    names=[*roles.values(),'events.jsonl','OWNER_DELIVERY_MANIFEST.json']
    for name in names:
        shutil.copyfile(stage/name,temporary/name)
        if sha(stage/name)!=sha(temporary/name):raise ValueError('OWNER_DELIVERY_COPY_HASH_CONFLICT')
    if validator.validate_package(temporary,request,sha(authorization))['status']!='PASS':raise ValueError('COPIED_PACKAGE_VALIDATION_FAILED')
    os.rename(temporary,destination)
    write_json(stage.parent/'DELIVERY_RECEIPT.json',{'destination':str(destination),'manifest_sha256':sha(destination/'OWNER_DELIVERY_MANIFEST.json')})
    if run_backtest:
        repo=Path(__file__).resolve().parents[1]
        completed=subprocess.run([sys.executable,str(repo/'scripts/run_train_account_v1.py'),'--execute-approved'],cwd=repo,check=False)
        return {'status':'DELIVERED_ACCOUNT_ENTRY_EXECUTED','entry_returncode':completed.returncode}
    return {'status':'DELIVERED'}


if __name__=='__main__':
    root=Path('E:/llmwiki/owner-execution-export-v1/run-v2')
    request=Path('E:/llmwiki/autonomous-strategy-research-v1/execution-data-v1/accounting-v1/OWNER_EXPORT_REQUEST_V1_1.json')
    auth=Path('C:/Users/84219/.codex/attachments/822d4ed4-de89-465c-b177-062c4e63dd49/pasted-text.txt')
    destination=request.parent/'owner-export'
    result=finalize(root/'staging',json.loads(request.read_text(encoding='utf-8')),auth,destination,run_backtest=True)
    print(json.dumps(result,ensure_ascii=False))
