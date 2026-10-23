"""Render a profile (or a tailored subset of one) to Jake's Resume LaTeX, compile with tectonic."""
import os
import shutil
import subprocess
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from pypdf import PdfReader

from . import config
from .profile import find_excluded

_TEX = {
    "\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
    "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
}


def tex(s) -> str:
    return "".join(_TEX.get(ch, ch) for ch in str(s))


def tex_url(u: str) -> str:
    return str(u).replace("\\", "/").replace("%", r"\%").replace("#", r"\#")


def short_url(u: str) -> str:
    for prefix in ("https://", "http://", "www."):
        u = u.removeprefix(prefix)
    return u.rstrip("/")


def dates(item) -> str:
    parts = list(dict.fromkeys(tex(item[k]) for k in ("start", "end") if item.get(k)))  # "Oct 2026" once, not twice
    return " -- ".join(parts)


def header(c: dict) -> str:
    parts = [tex(c["phone"])] if c.get("phone") else []
    if c.get("email"):
        parts.append(rf"\href{{mailto:{tex_url(c['email'])}}}{{\underline{{{tex(c['email'])}}}}}")
    for k in ("linkedin", "github", "portfolio"):
        if c.get(k):
            parts.append(rf"\href{{{tex_url(c[k])}}}{{\underline{{{tex(short_url(c[k]))}}}}}")
    return r" $|$ ".join(parts)


ENV = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    block_start_string="<%", block_end_string="%>",
    variable_start_string="<<", variable_end_string=">>",
    comment_start_string="<#", comment_end_string="#>",
    trim_blocks=True, lstrip_blocks=True, autoescape=False,
)
ENV.filters.update(e=tex, url=tex_url, dates=dates)


def render_tex(p: dict) -> str:
    if bad := find_excluded(p):
        raise ValueError(f"Refusing to render: excluded terms present: {', '.join(bad)}")
    return ENV.get_template("jake.tex.j2").render(p=p, header=header(p["contact"]))


def find_tectonic() -> str:
    exe = shutil.which("tectonic") or config.HOME / "bin" / ("tectonic.exe" if os.name == "nt" else "tectonic")
    if not Path(exe).exists():
        raise FileNotFoundError("tectonic not found. See README > Setup for the install command.")
    return str(exe)


def compile_pdf(tex_path: Path) -> Path:
    r = subprocess.run(
        [find_tectonic(), "--chatter", "minimal", "--outdir", str(tex_path.parent), str(tex_path)],
        capture_output=True, text=True,
    )
    if r.returncode:
        raise RuntimeError(f"tectonic failed:\n{r.stderr or r.stdout}")
    return tex_path.with_suffix(".pdf")


def build(p: dict, outdir: Path, stem: str | None = None) -> tuple[Path, int]:
    """Write <stem>.tex and <stem>.pdf into outdir. Returns (pdf path, page count)."""
    outdir.mkdir(parents=True, exist_ok=True)
    stem = stem or f"{p['name'].replace(' ', '_')}_Resume"
    tex_path = outdir / f"{stem}.tex"
    tex_path.write_text(render_tex(p), encoding="utf-8")
    pdf = compile_pdf(tex_path)
    return pdf, len(PdfReader(pdf).pages)
