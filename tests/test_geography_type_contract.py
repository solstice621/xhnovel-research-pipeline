from __future__ import annotations
import importlib.util
from pathlib import Path
import pytest
from xhnovel_pipeline.generic_profile import load_extraction_profile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('geography_type_contract', ROOT/'scripts/spikes/geography_type_contract.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def test_shared_projection_does_not_reward_schema_split_or_invent_places():
    legacy = [{'payload': {'kind':'PLACE_MENTION','name':'甲城','explicit_type':'城市'}},
              {'payload': {'kind':'PLACE_MENTION','name':'甲城'}}]
    split = [{'payload': {'kind':'PLACE_MENTION','name':'甲城'}},
             {'payload': {'kind':'PLACE_TYPE_ASSERTION','place_name':'甲城','explicit_type':'城市'}}]
    assert m.project_records(legacy) == m.project_records(split)
    assert ('PLACE', '甲城') not in m.project_records(split[1:])
    missing = m.score_atoms(m.project_records(split[:1]), m.project_records(legacy))
    assert missing['PLACE']['recall'] == 1
    assert missing['TYPE']['recall'] == 0
    assert missing['TYPE']['precision'] is None
    assert missing['JOINT_PLACE']['precision'] == 0


def test_projection_preserves_synonyms_and_rejects_unknown_kinds():
    with pytest.raises(ValueError): m.project_payload({'kind':'UNKNOWN'})
    a = {('PLACE','甲城'),('TYPE','甲城','城')}
    b = {('PLACE','甲城'),('TYPE','甲城','城市')}
    assert m.score_atoms(a,b)['TYPE']['tp'] == 0
    assert m.attribution(a,b)['same_name_type_substitution'] == ['甲城']
    assert not m.contains([], [(0,100)])
    assert not m.contains([(1,10)], [(1,5),(6,10)])
    assert m.contains([(1,10)], [(0,11)])
    assert m.aggregate([])['TYPE']['precision'] is None


def test_experimental_profiles_keep_window_policy_and_split_type_contract():
    from jsonschema import Draft202012Validator
    profiles = ROOT/'docs/spikes/geography-type-contract/profiles'
    shipped = load_extraction_profile('geography-unique-v1',root=ROOT)
    baseline = load_extraction_profile('baseline',root=ROOT,profiles_root=profiles)
    assert baseline.package_hash == shipped.package_hash
    for arm in ('strict','split'):
        profile = load_extraction_profile(arm,root=ROOT,profiles_root=profiles)
        assert profile.unit_policy == shipped.unit_policy
        assert profile.limits == shipped.limits
    split = load_extraction_profile('split',root=ROOT,profiles_root=profiles)
    validator = Draft202012Validator(split.payload_schema)
    assert validator.is_valid({'kind':'PLACE_TYPE_ASSERTION','place_name':'甲城','explicit_type':'城市'})
    assert not validator.is_valid({'kind':'PLACE_MENTION','name':'甲城','explicit_type':'城市'})
    assert split.evidence_policy['by_kind']['PLACE_TYPE_ASSERTION']['required_groups'] == [['/place_name','/explicit_type']]
