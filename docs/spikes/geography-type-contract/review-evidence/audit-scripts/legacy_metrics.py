import pathlib,json,sys
R=pathlib.Path.cwd();sys.path.insert(0,str(R/'scripts/spikes'));sys.path.insert(0,str(R/'src'))
import geography_experiment_c as c
from xhnovel_pipeline.store import ArtifactStore
E=R/'.runtime/experiments/geography-type-contract-v1';f=json.loads((E/'reference-freeze.json').read_text());store=ArtifactStore(E/'reference-objects')
sample=json.loads((R/'docs/spikes/geography-capacity-stats/experiment-b-sample.json').read_text())
unique=[json.loads(x) for x in store.get(f['development']['unique']).splitlines()]
tasks=json.loads((E/'tasks.json').read_text());out={}
for arm in ['baseline','strict']:
 answers={}
 for u in sample['units']:
  t=next(t for t in tasks if t['key']==f"{arm}/u{u['ordinal']:04d}")
  raw=pathlib.Path(t['answer']).read_bytes();answers[u['unit_id']]=(raw,json.loads(raw))
 out[arm]=c.score_configuration(sample=sample,unique_rows=unique,answers=answers)
(E/'fresh-legacy-metrics.json').write_text(json.dumps({'status':'FRESH_D_RUNS_NOT_HISTORICAL_C_RESCORE','citation':'Not calculated in old-coordinate scorer; use metrics.json for mapped citations','arms':out},indent=2)+'\n')
for arm,r in out.items():
 m=r['cohorts']['all10_diagnostic'];print(arm,{k:m[k] for k in ['place_unique','place_name','mean_explicit_type_accuracy','weighted_explicit_type_accuracy','perfect_type_unit_rate','explicit_type_counts']})
