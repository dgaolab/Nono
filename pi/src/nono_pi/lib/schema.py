"""Shared JSON-Schema validation against packaged schema files."""
import json


def validate(schema_path, doc):
    """Validate `doc` against the Draft 2020-12 schema at `schema_path`.

    Raises jsonschema.ValidationError if `doc` does not conform.
    """
    import jsonschema
    with open(schema_path, encoding="utf-8") as fh:
        schema = json.load(fh)
    jsonschema.Draft202012Validator(schema).validate(doc)
