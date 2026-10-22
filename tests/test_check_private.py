import importlib.util

from iap import config

spec = importlib.util.spec_from_file_location("check_private", config.REPO / "scripts" / "check_private.py")
cp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cp)

ANSWERS = {"phone": "(205) 555-0142", "email_school": "someone@crimson.example.edu", "address": {"street": "42 Elm St"}}
PERSONAL = cp.personal_patterns(ANSWERS, ["Secret Club Name"])


def test_blocks_personal_data_paths():
    for path in ("data/app.db", "profile.yaml", "x/answers.yml", ".env", "out/Me_Resume.pdf", "gmail_token.json"):
        assert cp.problems(path, "", {}), path


def test_allows_examples_and_code():
    for path in ("examples/sample_profile.yaml", "src/iap/profile.py", "docs/screenshot.pdf"):
        assert cp.problems(path, "print('hi')", PERSONAL) == [], path


def test_detects_secrets():
    fake = "token = '" + "ghp_" + "a1" * 20 + "'"
    assert cp.problems("x.py", fake, {})
    assert cp.problems("x.py", "key=" + "sk-ant-" + "x" * 30, {})


def test_detects_personal_info_in_any_format():
    assert cp.problems("README.md", "call 205.555.0142", PERSONAL)
    assert cp.problems("README.md", "mail SOMEONE@crimson.example.edu", PERSONAL)
    assert cp.problems("README.md", "lives at 42 Elm St", PERSONAL)
    assert cp.problems("README.md", "secret club name", PERSONAL)
    assert cp.problems("README.md", "nothing private here", PERSONAL) == []
