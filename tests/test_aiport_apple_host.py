"""Mocked host observations only; never change routes/firewalls or bind a port."""
import pytest

from aikey.aiport_apple_host import HostPreflight
from aikey.aiport_apple_reconcile import AppleRuntimeError


class Host:
    def __init__(self):
        self.route='interface: en0\n'
        self.interfaces='en0: flags=active\n inet 192.168.10.135 netmask 0xffffff00\n'
        self.firewall='Firewall is disabled. (State = 0)\n'
        self.calls=[]
        self.bound=[]
        self.closed=0
        self.bind_error=None
    def run(self,args,timeout):
        self.calls.append(args)
        if args[0]=='/sbin/ifconfig':
            return self.interfaces
        if args[0]=='/sbin/route':
            assert args==['/sbin/route','-n','get','192.168.10.1']
            return self.route
        assert args==['/usr/libexec/ApplicationFirewall/socketfilterfw','--getglobalstate']
        return self.firewall
    def socket(self,*args):
        return self
    def settimeout(self,value):
        assert value==2
    def bind(self,target):
        self.bound.append(target)
        if self.bind_error:
            raise self.bind_error
    def close(self):
        self.closed+=1


def checker(host,system='Darwin'):
    return HostPreflight('192.168.10.1',run=host.run,socket_factory=host.socket,system=lambda:system)


def instance():
    return {'slot':1,'host_ip':'192.168.10.135','management_tcp':443,'container_tcp':8443}


def test_exact_address_route_and_application_firewall_observation_no_service_or_repair():
    host=Host()
    check=checker(host)
    assert check(instance()) is True
    assert host.bound==[('192.168.10.135',443)] and host.closed==1
    assert check.observations[0]['other_packet_filters_and_controller_reachability']=='needs_evidence'
    assert len(host.calls)==3 and not any('--set' in word for args in host.calls for word in args)


@pytest.mark.parametrize('fault',['address_absent','duplicate_address','different_route','vpn_route',
    'firewall_enabled','firewall_unknown','permission','occupied','linux'])
def test_unknown_or_unsupported_preflight_does_not_guess_or_repair(fault):
    host=Host()
    system='Darwin'
    if fault=='address_absent':
        host.interfaces='en0: flags=active\n inet 192.168.10.136 netmask 0xffffff00\n'
    elif fault=='duplicate_address':
        host.interfaces+='en1: flags=active\n inet 192.168.10.135 netmask 0xffffff00\n'
    elif fault=='different_route':
        host.route='interface: en1\n'
    elif fault=='vpn_route':
        host.interfaces=host.interfaces.replace('en0:','utun0:')
        host.route='interface: utun0\n'
    elif fault=='firewall_enabled':
        host.firewall='Firewall is enabled. (State = 1)\n'
    elif fault=='firewall_unknown':
        host.firewall='unknown state; disabled not established\n'
    elif fault=='permission':
        host.bind_error=PermissionError('fictional privileged bind refused')
    elif fault=='occupied':
        host.bind_error=OSError('fictional occupied port')
    else:
        system='Linux'
    check=checker(host,system)
    with pytest.raises(AppleRuntimeError):
        check(instance())
    assert not check.observations
    assert host.closed==(1 if fault in ('permission','occupied') else 0)
