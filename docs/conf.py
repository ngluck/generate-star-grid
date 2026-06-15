from importlib.metadata import version as _version

project = "generate-star-grid"
copyright = "2026, Naomi Gluck"
author = "Naomi Gluck"
release = _version("generate-star-grid")
version = release

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "myst_parser",
]

source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}

myst_heading_anchors = 3

html_theme = "sphinx_rtd_theme"
