"""Exclusive private trial artifacts and strict JSON; no model/media imports."""
import json
import os
from pathlib import Path


def strict(raw):
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON key')
            result[key]=value
        return result
    def refuse(_):
        raise ValueError('Nonfinite JSON')
    return json.loads(raw,object_pairs_hook=unique,parse_constant=refuse)


def save(path,raw):
    with Path(path).open('xb') as stream:
        os.fchmod(stream.fileno(),0o600)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def save_json(path,value):
    save(path,(json.dumps(value,sort_keys=True,indent=2,allow_nan=False)+'\n').encode())
