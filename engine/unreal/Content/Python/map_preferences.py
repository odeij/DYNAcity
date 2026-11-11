"""Shared, versioned minimap preferences; no Unreal or browser dependency."""
import json
import os
from pathlib import Path
import tempfile

MODES = ('street', 'satellite', 'hybrid')


def defaults():
    return {'version': 1, 'mode': 'street', 'heading_up': True}


def validate(data):
    if (not isinstance(data, dict) or type(data.get('version')) is not int or
            data['version'] != 1 or data.get('mode') not in MODES or
            type(data.get('heading_up')) is not bool):
        raise ValueError('Map settings need version 1, a street/satellite/hybrid mode and boolean heading_up.')
    return {key: data[key] for key in ('version', 'mode', 'heading_up')}


def load(path):
    try:
        return validate(json.loads(Path(path).read_text(encoding='utf-8-sig')))
    except FileNotFoundError:
        return defaults()


def merge(current, patch):
    if not isinstance(patch, dict) or not patch or set(patch) - {'mode', 'heading_up'}:
        raise ValueError('Choose only a map mode and/or heading-up setting.')
    return validate({**validate(current), **patch})


def save(path, data):
    """A failed write never removes the last saved preference file."""
    data = validate(data)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix=path.name + '.', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return data
