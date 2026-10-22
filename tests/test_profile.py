import copy

from iap import config, profile as prof

SAMPLE = config.EXAMPLES / "sample_profile.yaml"


def test_sample_profile_is_valid():
    p = prof.load(SAMPLE)
    assert prof.validate(p) == []
    assert p["projects"][0]["id"] == "packetscope"
    assert p["projects"][0]["bullets"][1]["id"] == "packetscope.2"


def test_existing_ids_survive_inserting_a_bullet():
    p = prof.load(SAMPLE)
    item = p["projects"][0]
    item["bullets"].insert(0, {"text": "New first bullet"})
    prof.normalize(p)
    ids = [b["id"] for b in item["bullets"]]
    assert ids[1:] == ["packetscope.1", "packetscope.2", "packetscope.3"]
    assert ids[0] == "packetscope.4"


def test_duplicate_names_get_unique_ids():
    p = prof.normalize({"projects": [{"name": "Tool"}, {"name": "Tool"}]})
    assert [x["id"] for x in p["projects"]] == ["tool", "tool-2"]


def test_excluded_terms_are_caught_case_insensitively():
    p = prof.load(SAMPLE)
    assert prof.find_excluded(p) == []
    p = copy.deepcopy(p)
    p["projects"][0]["bullets"][0]["text"] += " for RIVERA LAWN CARE"
    assert prof.find_excluded(p) == ["Rivera Lawn Care"]
    assert any("excluded" in e for e in prof.validate(p))
