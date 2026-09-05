"""Read immutable native outputs; map locators for scoring only."""
import pathlib,json,sys,hashlib,collections,importlib.util
R=pathlib.Path.cwd();sys.path.insert(0,str(R/'scripts/spikes'))
import geography_type_contract as score
sys.path.insert(0,str(R/'src'))
from xhnovel_pipeline.store import ArtifactStore
E=R/'.runtime/experiments/geography-type-contract-v1'
S=json.loads((E/'sample.json').read_text());T=json.loads((E/'tasks.json').read_text())
G=R/'.runtime/generic-geography/doupo-v1-44966c9/experiment-b-gold/refreeze-fce97ac'
freeze=json.loads((E/'reference-freeze.json').read_text())
cas=ArtifactStore(E/'reference-objects')
def loadlines(p):
 key='unique' if p.name=='unique.jsonl' else 'occurrences'
 return [json.loads(x) for x in cas.get(freeze['development'][key]).splitlines()]
def map_spans(spans,layout):
 mapped=[]
 for span in spans:
  offset=0;ok=False
  for chunk in layout:
   if chunk['segment_id']==span['segment_id'] and chunk['start']<=span['start'] and span['end']<=chunk['end']:
    mapped.append((offset+span['start']-chunk['start'],offset+span['end']-chunk['start']));ok=True;break
   offset+=chunk['end']-chunk['start']
  if not ok:raise ValueError(('unmapped citation',span))
 return mapped

def evidence_index(records,layout):
 idx=collections.defaultdict(list)
 for r in records:
  for atom in score.project_payload(r['payload']):
   # Retain each complete native evidence set; no fabricated offset repair.
   spans=r.get('source_spans') or [s for b in r['evidence_bindings'] for s in b['source_spans']]
   idx[atom].append(map_spans(spans,layout))
 return idx

def reference(row):
 n=row['ordinal'];origin=json.loads((E/'units'/f'u{n:04d}'/'origin.json').read_text())
 if row['cohort']=='development':
  unique=[r for r in loadlines(G/'unique.jsonl') if r['ordinal']==n]
  ids={o['annotation_id'] for r in unique for o in r['occurrences']}
  occurrences=[r for r in loadlines(G/'occurrences.jsonl') if r['annotation_id'] in ids]
  return score.project_records(unique),evidence_index(occurrences,origin['source_spans'])
 else:
  doc=json.loads(cas.get(freeze['holdout'][str(n)]['final.json']));atoms=set();idx=collections.defaultdict(list)
  text=(E/'units'/f'u{n:04d}'/'source.txt').read_text()
  for a in doc['atoms']:
   k=a['kind'];atom=(k,a['name']) if k=='PLACE' else ((k,a['name'],a['explicit_type']) if k=='TYPE' else (k,a['subject_name'],a['relation'],a['object_name']))
   spans=[]
   for ev in a['evidence']:
    assert 0<=ev['start']<ev['end']<=len(text) and text[ev['start']:ev['end']]==ev['quote']
    spans.append((ev['start'],ev['end']))
   assert spans;atoms.add(atom);idx[atom].append(spans)
  return atoms,idx

out=[];diffs=[]
for row in S['units']:
 n=row['ordinal'];gold,gindex=reference(row)
 for arm in ['baseline','strict','split']:
  entry=next(t for t in T if t['key']==f'{arm}/u{n:04d}')
  w=E/'runs'/arm/f'u{n:04d}';result=json.loads((w/'result.json').read_text());assert result['validation']=='VALID'
  task=json.loads(pathlib.Path(entry['task']).read_text());answer=json.loads(pathlib.Path(entry['answer']).read_text())
  corpus=json.loads(pathlib.Path(result['corpus']).read_text())
  native_store=ArtifactStore(w/'ingestion/objects')
  records=[json.loads(line) for line in native_store.get(corpus['corpus_artifact_id']).splitlines()]
  pred=score.project_records(records)
  assert pred==score.project_records(answer['records'])
  pindex=evidence_index(records,task['input']['unit']['source_spans'])
  matched=pred&gold
  contained=sum(any(score.contains(g,p) for g in gindex[a] for p in pindex[a]) for a in matched)
  metrics=score.score_atoms(pred,gold)
  out.append({'key':entry['key'],'ordinal':n,'cohort':row['cohort'],'arm':arm,**metrics,'citation':{'matched':len(matched),'contained':contained,'containment':score.ratio(contained,len(matched))},'raw_count':len(answer['records']),'completion':answer['completion']['status'],'answer_hash':hashlib.sha256(pathlib.Path(entry['answer']).read_bytes()).hexdigest(),'task_hash':hashlib.sha256(pathlib.Path(entry['task']).read_bytes()).hexdigest()})
  diffs.append({'key':entry['key'],'cohort':row['cohort'],**score.attribution(pred,gold)})
cohorts={}
for cohort in ['development','development_stress','development_control','holdout']:
 cohorts[cohort]={}
 for arm in ['baseline','strict','split']:
  stress={5,310,395,426,513,596}
  rs=[r for r in out if r['arm']==arm and (r['cohort']==cohort or (cohort=='development_stress' and r['cohort']=='development' and r['ordinal'] in stress) or (cohort=='development_control' and r['cohort']=='development' and r['ordinal'] not in stress))];m=score.aggregate(rs)
  matched=sum(r['citation']['matched'] for r in rs);contained=sum(r['citation']['contained'] for r in rs)
  m['citation']={'matched':matched,'contained':contained,'containment':score.ratio(contained,matched)}
  m['raw_count']=sum(r['raw_count'] for r in rs);m['saturated_units']=sum(r['raw_count']>=64 for r in rs);m['overflow_units']=sum(r['completion']=='OVERFLOW' for r in rs)
  cohorts[cohort][arm]=m
accept={}
for arm in ['strict','split']:
 m=cohorts['holdout'][arm];b=cohorts['holdout']['baseline'];checks={}
 for family,p,r in [('PLACE',.9,.9),('TYPE',.8,.8),('REL',.85,.85)]:
  checks[family+'_precision']=m[family]['precision'] is not None and m[family]['precision']>=p
  checks[family+'_recall']=m[family]['recall'] is not None and m[family]['recall']>=r
 checks['joint_precision']=m['JOINT_PLACE']['precision'] is not None and m['JOINT_PLACE']['precision']>=.75
 checks['joint_recall_loss']=m['JOINT_PLACE']['recall'] is not None and b['JOINT_PLACE']['recall'] is not None and m['JOINT_PLACE']['recall']>=b['JOINT_PLACE']['recall']-.05
 checks['citation']=m['citation']['containment'] is not None and m['citation']['containment']>=.95
 checks['capacity']=m['overflow_units']==0
 sufficient=m['TYPE']['gold']>=20 and m['REL']['gold']>=10
 accept[arm]={'status':('PASS' if all(checks.values()) else 'FAIL') if sufficient else 'INCONCLUSIVE_SMALL_REFERENCE','reference_size_sufficient':sufficient,'checks':checks}
report={'cohorts':cohorts,'acceptance':accept,'per_unit':out,'limitations':['Single model draw per arm/unit; source-only references are model reviewed, not human gold.','Matched-atom citation containment is locator agreement, not proof of entailment.','Historical Experiment C answer bundle unavailable unless supplied separately.']}
(E/'metrics.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');(E/'payload-diff.json').write_text(json.dumps(diffs,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'cohorts':cohorts,'acceptance':accept},ensure_ascii=False,indent=2))
