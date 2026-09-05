import pathlib,json,sys,hashlib,os
R=pathlib.Path(os.environ.get('XHNOVEL_EXPERIMENT_ENGINE_ROOT', '/tmp/xhnovel-type-contract-engine-3372edd'));sys.path.insert(0,str(R/'src'))
from xhnovel_pipeline.novel_ingest import load_novel_spec
from xhnovel_pipeline.generic_extraction import run_generic_corpus_workflow,validate_generic_work_dir
from xhnovel_pipeline.generic_agent_files import GenericAgentFileExecutor,GenericAgentResponsesPending
from xhnovel_pipeline.generic_profile import load_extraction_profile
from xhnovel_pipeline.runtime import utc_now
E=pathlib.Path(__file__).resolve().parent;P=E/'frozen-profiles'
MODE=sys.argv[1];chosen=sys.argv[2:]
sample=json.loads((E/'sample.json').read_text());pending=[];results=[]
for row in sample['units']:
 n=row['ordinal'];spec=load_novel_spec(pathlib.Path(row['spec']))
 for arm in ['baseline','strict','split']:
  key=f'{arm}/u{n:04d}'
  if chosen and key not in chosen:continue
  w=E/'runs'/arm/f'u{n:04d}';p=load_extraction_profile(arm,root=R,profiles_root=P)
  ex=GenericAgentFileExecutor(w/'agent-files',model_label='codex-default-isolated')
  try:
   result=run_generic_corpus_workflow(spec,w,profile_ref=arm,executor=ex,root=R,profiles_root=P,now=utc_now())
   valid=validate_generic_work_dir(spec,w,profile_ref=arm,root=R,profiles_root=P,now=utc_now())
   record={'key':key,'status':'SUCCEEDED','profile_hash':p.package_hash,'corpus':str(result.corpus_snapshot_path),'count':result.corpus_snapshot['corpus_record_count'],'validation':'VALID','cohort':row['cohort']}
   (w/'result.json').write_text(json.dumps(record,indent=2)+'\n');results.append(record);print(json.dumps(record),flush=True)
  except GenericAgentResponsesPending as e:
   entries=[{'key':key,'ordinal':n,'cohort':row['cohort'],'task':str(i.task_path),'answer':str(i.answer_path),'profile_hash':p.package_hash} for i in e.pending]
   assert len(entries)==1, (key,len(entries))
   pending+=entries
  except Exception as e:
   record={'key':key,'status':'FAILED','error':str(e)};results.append(record);print(json.dumps(record),flush=True)
if MODE=='prepare':
 (E/'tasks.json').write_text(json.dumps(pending,indent=2)+'\n');print('pending',len(pending))
else:print('remaining selected',len(pending))
