import pathlib,json,sys
R=pathlib.Path('/tmp/xhnovel-type-contract-engine-3372edd');sys.path.insert(0,str(R/'src'))
from xhnovel_pipeline.novel_ingest import load_novel_spec
from xhnovel_pipeline.generic_extraction import validate_generic_work_dir
from xhnovel_pipeline.runtime import utc_now
E=pathlib.Path(__file__).resolve().parent;P=E/'frozen-profiles';rows=[]
for row in json.loads((E/'sample.json').read_text())['units']:
 for arm in ['baseline','strict','split']:
  key=f"{arm}/u{row['ordinal']:04d}";w=E/'runs'/key
  old=json.loads((w/'result.json').read_text());results=validate_generic_work_dir(load_novel_spec(pathlib.Path(row['spec'])),w,profile_ref=arm,root=R,profiles_root=P,now=utc_now())
  assert len(results)==1
  assert str(results[0].corpus_snapshot_path)==old['corpus']
  rows.append({'key':key,'validation':'VALID','corpus_snapshot_id':results[0].corpus_snapshot['corpus_snapshot_id']})
(E/'fresh-process-validation.json').write_text(json.dumps({'engine_commit':'3372edd47666175db9f6a17bee1b8446635ce355','count':len(rows),'results':rows},indent=2)+'\n')
print('fresh-process native validation:',len(rows),'VALID')
