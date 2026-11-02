"""New Admin defaults must not add unknown fields to legacy Port profiles."""
import json
import pytest
from test_aiport_config_store import fixture
from test_primary_detection_schema import local_settings
from test_primary_api_budget import form
from test_control_site_secrets import signed_in


@pytest.mark.parametrize('schema,budget', [(False,15),(True,15),(False,30),(True,30)])
def test_only_explicit_nondefaults_materialize_on_legacy_profile(tmp_path,schema,budget):
    store,path,_=fixture(tmp_path)
    saved=store.apply(store.snapshot().revision,local_settings(tmp_path,structured_output=schema,request_timeout_s=budget))
    detector=json.loads(path.read_bytes())['live_pool_detector']
    assert ('structured_output' in detector) is schema
    assert ('request_timeout_s' in detector) is (budget!=15)
    assert saved.structured_output is schema and saved.request_timeout_s==budget


def test_existing_explicit_fields_can_reset_without_being_silently_removed(tmp_path):
    store,path,_=fixture(tmp_path)
    enabled=store.apply(store.snapshot().revision,local_settings(tmp_path,structured_output=True,request_timeout_s=30))
    original=path.read_bytes()
    reset=store.apply(enabled.revision,local_settings(tmp_path,structured_output=False,request_timeout_s=15))
    detector=json.loads(path.read_bytes())['live_pool_detector']
    assert detector['structured_output'] is False and detector['request_timeout_s']==15
    assert reset.structured_output is False and reset.request_timeout_s==15
    store.rollback_provider(reset.revision)
    assert path.read_bytes()==original


async def test_rendered_form_with_unchanged_new_defaults_keeps_legacy_fields_absent(tmp_path):
    site,server,client,cookie,_=await signed_in(tmp_path)
    try:
        path=site.aiports['aiport'].path
        data=form(site,cookie)
        data['request_timeout_s']='15'
        data['structured_output_present']='yes'
        response=await client.post('/provider',data=data,headers={'Origin':site.origin},allow_redirects=False)
        assert response.status==303
        detector=json.loads(path.read_bytes())['live_pool_detector']
        assert 'structured_output' not in detector and 'request_timeout_s' not in detector
        page=await (await client.get('/')).text()
        assert 'Requires an updated AI Port that supports this option' in page
    finally:
        await client.close()
        await server.close()
