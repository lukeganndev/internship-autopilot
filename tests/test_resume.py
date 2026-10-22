import pytest
from pypdf import PdfReader

from iap import config, profile as prof, resume

SAMPLE = config.EXAMPLES / "sample_profile.yaml"


def test_tex_escaping():
    assert resume.tex("C# & 100% of $5_x {a}") == r"C\# \& 100\% of \$5\_x \{a\}"
    assert resume.tex("a\\b") == r"a\textbackslash{}b"


def test_render_contains_sections_and_escaped_text():
    src = resume.render_tex(prof.load(SAMPLE))
    for s in ("Alex Rivera", r"\section{Education}", r"\section{Projects}", r"\section{Experience}",
              r"Data Structures \& Algorithms", "GPA: 3.72", r"Leadership \& Activities"):
        assert s in src
    assert "<<" not in src and "<%" not in src


def test_render_refuses_excluded_terms():
    p = prof.load(SAMPLE)
    p["skills"]["Languages"].append("Rivera Lawn Care")
    with pytest.raises(ValueError, match="excluded"):
        resume.render_tex(p)


def _has_tectonic():
    try:
        resume.find_tectonic()
        return True
    except FileNotFoundError:
        return False


@pytest.mark.skipif(not _has_tectonic(), reason="tectonic not installed")
def test_sample_compiles_to_one_ats_readable_page(tmp_path):
    pdf, pages = resume.build(prof.load(SAMPLE), tmp_path)
    assert pages == 1
    text = PdfReader(pdf).pages[0].extract_text()
    for s in ("Alex Rivera", "PacketScope", "Wireshark", "Relevant Coursework"):
        assert s in text
