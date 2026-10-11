from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape


ROOT = Path(__file__).resolve().parent / "templates"
env = Environment(loader=FileSystemLoader(ROOT), autoescape=select_autoescape(["html", "xml"]))


def render(name, **context):
    return env.get_template(name).render(**context)
