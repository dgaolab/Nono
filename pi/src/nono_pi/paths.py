"""Locate packaged data (schemas, templates, routing) regardless of CWD or install mode."""
from importlib import resources
import pathlib


def data_file(*parts):
    """Return a concrete filesystem Path to a file under nono_pi/data."""
    root = resources.files("nono_pi.data")
    return pathlib.Path(str(root.joinpath(*parts)))


def schemas_dir():
    return data_file("schemas")


def templates_dir():
    return data_file("templates")


def routing_dir():
    return data_file("routing")
