"""Typed CLI producer/consumer contract with invented paths and own fake daemon."""
import json
import os
from pathlib import Path
import subprocess
import sys

from aikey import aiport_apple_cli as cli
from aikey.config import atomic_private
from test_aiport_apple_reconcile import actor_fixture,Host


def arguments(tmp_path):
    inputs=actor_fixture(tmp_path)
    manifest,plan,report,states,options,host_state=inputs
    plan_path=tmp_path/'deployment-plan.json'
    report_path=tmp_path/'inventory.json'
    atomic_private(plan_path,json.dumps(plan))
    atomic_private(report_path,json.dumps(report))
    output=tmp_path/'apple-manifest.json'
    common=['--plan',str(plan_path),'--slot-state','1='+str(states[1]),
        '--controller-ip',options['controller_ip'],'--controller-pin',options['controller_pin'],
        '--image',options['image'],'--uid',str(options['uid']),'--gid',str(options['gid']),
        '--dns',options['dns'],'--aikey-state',str(options['aikey_state']),'--inventory',str(report_path)]
    return inputs,common,output,host_state


def test_cli_create_only_plan_repeat_and_conflicting_output_preserves_private_bytes(tmp_path,capsys):
    inputs,common,output,_=arguments(tmp_path)
    before={path:path.read_bytes() for root in [*inputs[3].values(),inputs[4]['aikey_state']]
            for path in root.rglob('*') if path.is_file()}
    args=['plan',*common,'--output',str(output)]
    assert cli.main(args)==0
    result=json.loads(capsys.readouterr().out)
    assert result['mode']=='manifest_created' and result['container_commands']==0
    assert result['native_verification']=='needs_evidence' and output.stat().st_mode&0o777==0o600
    assert cli.main(args)==0 and json.loads(capsys.readouterr().out)['mode']=='manifest_unchanged'
    assert all(path.read_bytes()==raw for path,raw in before.items())
    output.write_bytes(b'fictional retained different manifest')
    assert cli.main(args)==2
    assert json.loads(capsys.readouterr().out)['error']=='apple_manifest_already_exists_with_other_bytes'
    assert output.read_bytes()==b'fictional retained different manifest'


def test_cli_actual_module_plan_does_not_run_native_or_print_private_key(tmp_path):
    inputs,common,output,_=arguments(tmp_path)
    env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[1]/'src')}
    result=subprocess.run([sys.executable,'-m','aikey.aiport_apple_cli','plan',*common,'--output',str(output)],
        env=env,capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stderr
    value=json.loads(result.stdout)
    assert value['mode']=='manifest_created' and not result.stderr
    assert 'PRIVATE KEY' not in result.stdout and not value['identity_files_changed']
    assert json.loads(output.read_bytes())==inputs[0]


def test_cli_parser_and_real_core_apply_share_exact_manifest_receipt_contract(tmp_path,capsys,monkeypatch):
    inputs,common,output,host_state=arguments(tmp_path)
    assert cli.main(['plan',*common,'--output',str(output)])==0
    capsys.readouterr()
    host=Host(inputs[0])
    original=cli.reconcile
    def own_core(*args,**kwargs):
        return original(*args,**kwargs,run=host.run,probe=host.probe,pause=lambda _:None)
    class Checks:
        observations=[]
        def __init__(self,controller):
            assert controller==inputs[4]['controller_ip']
        def __call__(self,instance):
            return host.check(instance)
    monkeypatch.setattr(cli,'reconcile',own_core)
    monkeypatch.setattr(cli,'HostPreflight',Checks)
    args=['run',*common,'--manifest',str(output),'--state-dir',str(host_state),'--apply']
    assert cli.main(args)==0
    result=json.loads(capsys.readouterr().out)
    assert result['mode']=='applied' and result['native_verification']=='needs_evidence'
    assert json.loads((host_state/'reconcile.json').read_bytes())['phase']=='completed'
    assert cli.main(args)==0
    assert json.loads(capsys.readouterr().out)['mode']=='unchanged'
