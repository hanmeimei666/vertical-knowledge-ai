"""Versioned platform contracts; deliberately small JSON-Schema vocabulary."""
import json
from pathlib import Path

SCHEMAS = Path(__file__).resolve().parents[1] / 'schemas/v03'


def validate(value, schema, path='$'):
    types = {'object': dict, 'array': list, 'string': str, 'boolean': bool,
             'integer': int, 'number': (int, float), 'null': type(None)}
    if 'type' in schema:
        kind = schema['type']
        if not isinstance(value, types[kind]) or kind in ('integer', 'number') and isinstance(value, bool):
            raise ValueError(path + ': expected ' + kind)
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError(path + ': invalid enum')
    if isinstance(value, str) and len(value) < schema.get('minLength', 0):
        raise ValueError(path + ': empty string')
    if isinstance(value, dict):
        for key in schema.get('required', []):
            if key not in value:
                raise ValueError(path + ': missing ' + key)
        for key, child in value.items():
            if key in schema.get('properties', {}):
                validate(child, schema['properties'][key], path + '.' + key)
            elif schema.get('additionalProperties') is False:
                raise ValueError(path + ': unexpected ' + key)
    if isinstance(value, list):
        if len(value) < schema.get('minItems', 0):
            raise ValueError(path + ': too few items')
        for i, child in enumerate(value):
            validate(child, schema.get('items', {}), path + '[' + str(i) + ']')


def contract(kind, value):
    validate(value, json.loads((SCHEMAS / (kind + '.json')).read_text(encoding='utf-8')))
    return value
