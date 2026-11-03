"""Frozen E5 targets preserve raw low scores and never claim UI filtering."""
from copy import deepcopy
import hashlib

import pytest

from aikey.index_readback import ReadbackError
from aikey.index_session_readback import SessionReadbackPlan, session_readback

TEXT = 'Fictional toy car on a paper road.'
HASH = hashlib.sha256(TEXT.encode()).hexdigest()
NEW = 'b'*64


def specification():
    positive = {'text':'a red toy car','min_cosine':.8,'min_confidence':53,
        'expected_sessions':['session-a'],'description_sha256':{'session-a':HASH},'k':2,
        'camera_ids':[],'start_ms':None,'end_ms':None}
    return {'schema':SessionReadbackPlan.schema,'from_revision':'a'*64,'target_revision':NEW,
        'source_archive_sha256':'c'*64,'queries':[positive,
            dict(positive,text='a violet toy submarine',expected_sessions=[],description_sha256={})]}


class Native:
    def __init__(self):
        self.calls = []
        self.positive = {'id':'session-a','cameras':['camera-a'],'firstSeen':100,'lastSeen':200,
                         'description':TEXT,'confidence':99}
        self.negative = dict(self.positive,id='session-b',confidence=0)
        self.fail_reload = False

    async def search_sessions(self,text):
        self.calls.append(text)
        row = self.positive if text == 'a red toy car' else self.negative
        if self.fail_reload and len(self.calls) == 2:
            row = dict(row,id='wrong-session')
        return {'sessions':[deepcopy(row)]}


def runtime():
    return {'connected':True,'profile':'e5-session-v1','revision':NEW}


async def test_session_lookup_and_fresh_reload_are_not_conflated_with_native_ui_rejection():
    native = Native()
    plan = SessionReadbackPlan(specification())
    receipt = await session_readback(plan,native,runtime=runtime)
    assert receipt['passed'] and len(native.calls) == 3
    assert receipt['queries'][1]['returned_raw'] == 1
    assert receipt['queries'][1]['above_frozen_score'] == 0
    assert 'no_ui_rejection_claim' in receipt['scope']
    assert TEXT not in str(receipt) and 'session-a' not in str(receipt)


@pytest.mark.parametrize('change',['description','camera','time','confidence','reload','negative','runtime'])
async def test_wrong_source_join_score_reload_or_space_refuses_without_fallback(change):
    value = specification()
    native = Native()
    observe = runtime
    if change == 'description':
        native.positive['description']='Different fictional source.'
    elif change == 'camera':
        value['queries'][0]['camera_ids']=['camera-a']
        native.positive['cameras']=['camera-b']
    elif change == 'time':
        value['queries'][0].update(start_ms=300,end_ms=400)
    elif change == 'confidence':
        native.positive['confidence']=52
    elif change == 'reload':
        native.fail_reload=True
    elif change == 'negative':
        native.negative['confidence']=99
    else:
        def observe():
            return dict(runtime(),profile='clip-basic-v1')
    with pytest.raises(ReadbackError):
        await session_readback(SessionReadbackPlan(value),native,runtime=observe)
    assert len(native.calls) <= 3


@pytest.mark.parametrize('field,value',[('min_confidence',0),('min_cosine',float('nan')),
    ('expected_sessions',['session-a','session-a']),('description_sha256',{}),('camera_ids',['../bad'])])
def test_invalid_session_plan_never_becomes_authority(field,value):
    plan = specification()
    plan['queries'][0][field]=value
    with pytest.raises(ReadbackError):
        SessionReadbackPlan(plan)


async def test_other_checkpoint_or_boolean_success_cannot_authorize_session_query():
    native = Native()
    with pytest.raises(ReadbackError,match='running E5 query encoder'):
        await session_readback(SessionReadbackPlan(specification()),native,
                               runtime=lambda: {'connected':1,'profile':'e5-session-v1','revision':NEW})
    assert not native.calls
