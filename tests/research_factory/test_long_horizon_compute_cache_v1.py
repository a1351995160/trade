"""输出文件不能替代真实成功worker及同用途资源证明。"""
from scripts.run_strategy_account_v1 import proven_compute_result,save,sha


def test_result_alone_and_unknown_charged_segment_do_not_prove_completion(tmp_path):
    save(tmp_path/'RESULT.json',{'advance_allowed':True})
    assert not proven_compute_result(tmp_path,None,{'segments':[]})
    unknown={'dispatch':{'number':1,'dispatch_id':'FIXED'},'charge':{
        'basis':'UNKNOWN_CHARGED_DISPATCH_UPPER_BOUND','outcome':'CONTINUE'}}
    assert not proven_compute_result(tmp_path,None,{'segments':[unknown]})


def test_cached_compute_result_must_match_member_exit_status_and_resource_bytes(tmp_path):
    output=tmp_path/'RESULT_MEMBER.json';resource=tmp_path/'SEGMENT_000001_RESOURCE.json'
    save(output,{'report':'fixed'})
    save(resource,{'returncode':0,'timed_out':False})
    save(tmp_path/'SEGMENT_000001_STATUS.json',{'state':'COMPLETED','member':'MEMBER',
        'dispatch_id':'FIXED','result_sha256':sha(output)})
    proven={'dispatch':{'number':1,'dispatch_id':'FIXED'},'charge':{'basis':'MEASURED_ACTIVE_WALL_SECONDS',
        'outcome':'CONTINUE','evidence_identity':sha(resource)}}
    assert proven_compute_result(tmp_path,'MEMBER',{'segments':[proven]})
    assert not proven_compute_result(tmp_path,'OTHER',{'segments':[proven]})
    output.write_text('{"report":"forged"}',encoding='utf-8')
    assert not proven_compute_result(tmp_path,'MEMBER',{'segments':[proven]})
