"""Private deterministic input contracts; injected tones are not speech evidence."""
import hashlib
import importlib.util
from io import BytesIO
import json
from pathlib import Path
import math
import wave

from PIL import Image
import pytest


def generator(monkeypatch):
    root=Path(__file__).resolve().parents[1]/'tools'
    monkeypatch.syspath_prepend(str(root))
    spec=importlib.util.spec_from_file_location('multimodal_corpus',root/'benchmark_multimodal_corpus.py')
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_unlabelled_hash_bound_media_and_gold_are_separate(tmp_path,monkeypatch):
    m=generator(monkeypatch)
    def render(text,index):
        return m.pcm([round(2000*math.sin(n*(index+1)/20)) for n in range(4800)])
    root=tmp_path/'owned'
    result=m.generate(root,voice='fixture-not-a-real-voice',ffmpeg=Path('/unused'),render=render)
    assert result=={'synthetic_only':True,'plate_cases':12,'speech_cases':8,
                    'retrieval_queries':10,'visual_index_images':24,'actual_model_measurements':0}
    inputs=json.loads((root/'unlabelled-inputs.json').read_text())
    gold=json.loads((root/'gold.json').read_text())
    assert len(inputs['cases'])==len(gold['cases'])==30
    assert len({r['id'] for r in inputs['cases']})==30
    assert all('expected' not in r and 'relevant' not in r for r in inputs['cases'])
    assert [r['kind'] for r in inputs['cases']].count('retrieval')==10
    for row in inputs['cases']:
        if row['kind']=='retrieval':
            continue
        data=(root/row['file_name']).read_bytes()
        assert hashlib.sha256(data).hexdigest()==row['input_sha256']
        assert (root/row['file_name']).stat().st_mode & 0o077 == 0
        if row['kind']=='plate':
            with Image.open(BytesIO(data)) as picture:
                picture.load()
                assert picture.size==(640,320) and not picture.info
        else:
            assert m.check_audio(data)==row['duration_s']
            with wave.open(BytesIO(data),'rb') as audio:
                assert audio.getnchannels()==1 and audio.getframerate()==16000
    labels={r['id']:r for r in gold['cases']}
    assert sum(bool(r['expected']) for r in gold['cases'] if r['kind']=='plate')==8
    assert sum(bool(r['expected']) for r in gold['cases'] if r['kind']=='speech')==6
    assert sum(not r['relevant'] for r in gold['cases'] if r['kind']=='retrieval')==4
    for row in inputs['cases']:
        if row['kind']=='retrieval':
            expected=m.digest(json.dumps({'query':row['query'],'index_sha256':inputs['visual_index_sha256']},
                sort_keys=True,separators=(',',':')).encode())
            assert row['input_sha256']==expected
            assert set(labels[row['id']]['relevant']) <= {r['case_id'] for r in inputs['visual_index']}
    generation=json.loads((root/'generation.json').read_text())
    assert generation['speech_rendering']=='injected-test-fixture'
    assert generation['actual_model_measurements']==0 and not generation['hardware_or_native_acceptance']


def test_existing_or_partial_output_is_not_reused(tmp_path,monkeypatch):
    m=generator(monkeypatch)
    root=tmp_path/'retained'
    root.mkdir()
    marker=root/'canary'
    marker.write_bytes(b'Retained incomplete attempt')
    with pytest.raises(FileExistsError):
        m.generate(root,voice='fixture',ffmpeg=Path('/unused'))
    assert marker.read_bytes()==b'Retained incomplete attempt' and list(root.iterdir())==[marker]


@pytest.mark.parametrize('raw',[b'not wav',b'',b'RIFF'])
def test_malformed_synthesis_does_not_get_a_false_audio_receipt(monkeypatch,raw):
    with pytest.raises((ValueError,wave.Error,EOFError)):
        generator(monkeypatch).check_audio(raw)
