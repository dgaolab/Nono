"""Locate packaged data (templates, schemas) regardless of CWD or install mode."""
from importlib import resources
import pathlib


def data_file(*parts):
    """Return a concrete filesystem Path to a file under nono_librarian/data."""
    root = resources.files("nono_librarian.data")
    p = root.joinpath(*parts)
    return pathlib.Path(str(p))


def schemas_dir():
    return data_file("schemas")


def templates_dir():
    return data_file("templates")
