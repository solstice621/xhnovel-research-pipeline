from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import research_library as lib
import shared_acquisition as shared
from test_source_acquisition import fixture_config, write_json, reviewed
from xhnovel_pipeline.phase0_handoff import work_ref_from_declaration
from xhnovel_pipeline.errors import ValidationError, PipelineError


@pytest.fixture
def case(tmp_path):
    library = lib.Library.initialize(tmp_path / 'library')
    request = write_json(tmp_path / 'request.json', {'goal':'synthetic shared acquisition'})
    a = library.new_research(request, key='a', name='first')['record_id']
    b = library.new_research(request, key='b', name='second')['record_id']
    source = tmp_path / 'source'; source.mkdir()
    config, inputs = fixture_config(source)
    work = work_ref_from_declaration({'work': {
        'canonical_title':'测试仙途', 'author':'测试作者', 'language':'zh',
        'identity':{'basis':'TITLE_AUTHOR','normalized_title':'测试仙途','normalized_author':'测试作者','language':'zh'},
        'aliases':[], 'external_ids':[]}})
    work_path = write_json(tmp_path / 'work.json',work)
    return library, shared.SharedAcquisition(library,a), shared.SharedAcquisition(library,b), config, inputs, work_path


def test_concurrent_follower_skips_and_can_acquire_other_work(case, tmp_path, monkeypatch):
    library, first, follower, config, inputs, work = case
    entered, release = Event(), Event()
    original = shared.acq.Run.import_local
    calls=[]
    def slow(run, source):
        calls.append(str(run.root))
        if len(calls)==1:
            entered.set()
            assert release.wait(15)
        return original(run, source)
    monkeypatch.setattr(shared.acq.Run, 'import_local', slow)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(first.acquire,config,work_ref=work,input=inputs)
        try:
            assert entered.wait(15)
            skipped=follower.acquire(config,work_ref=work,input=inputs)
            assert skipped['status']=='BUSY_SKIPPED' and skipped['next_action']=='TRY_OTHER_WORK'
            assert Path(skipped['observation_path']).is_file()
            assert follower.resume(skipped['acquisition_id'],inspect=True)['status']=='BUSY_SKIPPED'
            # The host can immediately try another work while the first is held.
            second_root=tmp_path/'other';second_root.mkdir()
            other_config,other_inputs=fixture_config(second_root)
            cfg=shared.acq.read_json(other_config);cfg['work']['title']='另一作品'
            write_json(other_config,cfg)
            w=shared.acq.read_json(work);w['canonical_title']='另一作品';w['identity']['normalized_title']='另一作品'
            other_work=write_json(tmp_path/'other-work.json',work_ref_from_declaration({'work':w}))
            other=follower.acquire(other_config,work_ref=other_work,input=other_inputs)
            assert other['status']=='READY_FOR_REVIEW'
            assert other['acquisition_id']!=skipped['acquisition_id']
        finally:
            release.set()
        result=future.result(timeout=15)
    again=follower.acquire(config,work_ref=work,input=inputs)
    assert again['acquisition_id']==result['acquisition_id']
    assert again['status']=='READY_FOR_REVIEW'
    assert len(calls)==2  # one call per work, no follower import
    assert library.list_records('source')['records']==[]


def test_complete_seal_is_shared_before_handoff_and_revalidated(case, tmp_path, monkeypatch):
    library,first,follower,config,inputs,work=case
    result=first.acquire(config,work_ref=work,input=inputs)
    key=result['acquisition_id'];run=shared.acq.Run(Path(result['run_dir']))
    sealed=first.seal(key,review=reviewed(run,tmp_path))
    assert sealed['status']=='SEALED'
    monkeypatch.setattr(shared.acq.Run,'import_local',lambda *a:pytest.fail('duplicate import'))
    assert follower.resume(key,input=inputs)['sealed_path']==sealed['sealed_path']
    assert follower.acquire(config,work_ref=work,input=inputs)['status']=='SEALED'
    assert follower.seal(key,review=tmp_path/'unused.json')['sealed_path']==sealed['sealed_path']
    chapter=next(Path(sealed['sealed_path']).joinpath('chapters').glob('*.txt'))
    chapter.write_bytes(chapter.read_bytes()+b'changed')
    with pytest.raises((ValidationError, shared.acq.AcquisitionError)):
        follower.resume(key,inspect=True)


def test_partial_failure_releases_lock_and_resumes_same_native_run(case, monkeypatch):
    _,first,follower,config,inputs,work=case
    original=shared.acq.Run.import_local
    saved=(inputs/'0003.txt').read_bytes();(inputs/'0003.txt').unlink()
    partial=first.acquire(config,work_ref=work,input=inputs)
    assert partial['status']=='PARTIAL' and partial['next_action']=='TRY_OTHER_WORK'
    key=partial['acquisition_id'];directory=Path(partial['run_dir'])
    accepted={p.name:p.read_bytes() for p in (directory/'accepted').glob('*.json')}
    def fail(*args):raise OSError('synthetic interruption')
    monkeypatch.setattr(shared.acq.Run,'import_local',fail)
    with pytest.raises(OSError,match='interruption'):
        follower.resume(key,input=inputs)
    monkeypatch.setattr(shared.acq.Run,'import_local',original)
    (inputs/'0003.txt').write_bytes(saved)
    done=follower.resume(key,input=inputs)
    assert done['status']=='READY_FOR_REVIEW'
    assert done['run_dir']==partial['run_dir']
    assert all((directory/'accepted'/name).read_bytes()==raw for name,raw in accepted.items())


def test_os_process_death_releases_claim_without_ttl(case):
    library,first,follower,config,inputs,work=case
    result=first.acquire(config,work_ref=work,input=inputs);key=result['acquisition_id']
    code="""import sys,time
sys.path.insert(0,sys.argv[1])
import research_library as lib
from shared_acquisition import SharedAcquisition
s=SharedAcquisition(lib.Library(sys.argv[2]),sys.argv[3])
with s.claim(sys.argv[4]) as claimed:
 print('claimed' if claimed else 'busy',flush=True)
 time.sleep(30)
"""
    proc=subprocess.Popen([sys.executable,'-c',code,str(lib.ROOT/'scripts'),str(library.root),first.research_id,key],stdout=subprocess.PIPE,text=True)
    try:
        assert proc.stdout.readline().strip()=='claimed'
        assert follower.resume(key,inspect=True)['status']=='BUSY_SKIPPED'
    finally:
        proc.kill();proc.wait(timeout=10);proc.stdout.close()
    assert follower.resume(key,inspect=True)['status']=='READY_FOR_REVIEW'


@pytest.mark.parametrize('change',['limits','attestation','identity','symlink','binding'])
def test_conflicts_cannot_fork_another_download(case,change,tmp_path):
    _,first,follower,config,inputs,work=case
    result=first.acquire(config,work_ref=work,input=inputs);key=result['acquisition_id']
    if change=='limits':
        cfg=shared.acq.read_json(config);cfg['limits']={'max_run_seconds':301};write_json(config,cfg)
    elif change=='attestation':
        cfg=shared.acq.read_json(config);att=shared.acq.read_json(Path(cfg['attestation']['path']));att['may_store_full_text']=False
        write_json(Path(cfg['attestation']['path']),att);cfg['attestation']=shared.acq.ref(Path(cfg['attestation']['path']));write_json(config,cfg)
    elif change=='identity':
        value=shared.acq.read_json(work);value['work_ref_id']='WREF-00000000000000000000';write_json(work,value)
    elif change=='symlink':
        directory=first.directory(key);(directory/'coordination/.novel-ingest.lock').unlink()
        (directory/'coordination/.novel-ingest.lock').symlink_to(tmp_path/'elsewhere')
    else:
        (first.directory(key)/'binding.json').write_text('{}')
    with pytest.raises(PipelineError):
        follower.acquire(config,work_ref=work,input=inputs)


def test_paths_and_source_nicknames_do_not_defeat_dedup(case,tmp_path):
    _,first,follower,config,inputs,work=case
    first_result=first.acquire(config,work_ref=work,input=inputs)
    other=tmp_path/'duplicate';other.mkdir()
    second_config,second_inputs=fixture_config(other)
    cfg=shared.acq.read_json(second_config);cfg['source']['id']='different-local-label';write_json(second_config,cfg)
    second_result=follower.acquire(second_config,work_ref=work,input=second_inputs)
    assert second_result['acquisition_id']==first_result['acquisition_id']
    assert not (other/'run').exists()


def test_native_cooldown_survives_follower(case,monkeypatch):
    _,first,follower,config,inputs,work=case
    cfg=shared.acq.read_json(config);cfg['source']['channel']='C1';write_json(config,cfg)
    original=shared.acq.Run.acquire
    def cooling(run):
        run._state('COOLDOWN',retry_not_before_ms=run.clock.now_ms()+900000)
        return run.status()
    monkeypatch.setattr(shared.acq.Run,'acquire',cooling)
    result=first.acquire(config,work_ref=work)
    monkeypatch.setattr(shared.acq.Run,'acquire',lambda run: original(run, send=lambda *a:pytest.fail('cooldown must prevent requests')))
    resumed=follower.resume(result['acquisition_id'])
    assert resumed['status']=='PARTIAL'
    assert resumed['native_status']['acquisition']=='COOLDOWN'
    assert resumed['native_status']['retry_not_before_ms']==result['native_status']['retry_not_before_ms']


def test_shared_cli_returns_skip_exit_four(case,capsys):
    library,first,follower,config,inputs,work=case
    result=first.acquire(config,work_ref=work,input=inputs)
    with first.claim(result['acquisition_id']) as claimed:
        assert claimed
        exit_code=lib.main(['--library-root',str(library.root),'shared-acquire',follower.research_id,str(config),'--work-ref',str(work),'--input',str(inputs)])
    assert exit_code==4
    output=json.loads(capsys.readouterr().out)
    assert output['result']['status']=='BUSY_SKIPPED'
    assert output['result']['next_action']=='TRY_OTHER_WORK'


def test_seal_rejects_partial_and_resume_uses_snapshotted_inputs(case,tmp_path):
    _,first,follower,config,inputs,work=case
    saved=(inputs/'0003.txt').read_bytes();(inputs/'0003.txt').unlink()
    result=first.acquire(config,work_ref=work,input=inputs);key=result['acquisition_id']
    run=shared.acq.Run(Path(result['run_dir']))
    with pytest.raises(PipelineError):
        first.seal(key,review=reviewed(run,tmp_path))
    assert not (first.directory(key)/'sealed.json').exists()
    cfg=shared.acq.read_json(config)
    Path(cfg['catalog']['path']).unlink();Path(cfg['attestation']['path']).unlink();config.unlink()
    (inputs/'0003.txt').write_bytes(saved)
    assert follower.resume(key,input=inputs)['status']=='READY_FOR_REVIEW'


def test_distinct_source_versions_do_not_share_a_run(case):
    _,first,follower,config,inputs,work=case
    first_result=first.acquire(config,work_ref=work,input=inputs)
    cfg=shared.acq.read_json(config);cfg['source']['edition_label']='explicit other edition'
    write_json(config,cfg)
    second=follower.acquire(config,work_ref=work,input=inputs)
    assert second['acquisition_id']!=first_result['acquisition_id']


def test_interrupted_before_binding_requests_original_acquire(case):
    _,first,follower,config,inputs,work=case
    cfg=shared.acq.read_json(config)
    catalog,_,_=shared.acq.validate_config(cfg,config.parent)
    key=shared.acquisition_key(shared.acq.read_json(work),cfg,catalog)
    with first.claim(key) as acquired:
        assert acquired
        assert follower.acquire(config,work_ref=work,input=inputs)['status']=='BUSY_SKIPPED'
    status=follower.resume(key,inspect=True)
    assert status['status']=='INITIALIZATION_REQUIRED'
    assert status['next_action']=='RETRY_ACQUIRE'
    assert follower.acquire(config,work_ref=work,input=inputs)['status']=='READY_FOR_REVIEW'
