"""Narrow local address/route/application-firewall/bind preflight, no repair.

No address alias, firewall change, wildcard bind, sudo or privileged helper is
installed here. Other packet filters and actual controller reachability remain
native acceptance, not facts inferred from local application-firewall state.
"""
import ipaddress
import platform
import re
import socket

from .aiport_apple_reconcile import AppleRuntimeError,run_command


class HostPreflight:
    def __init__(self,controller_ip,*,run=run_command,socket_factory=socket.socket,system=platform.system):
        self.controller_ip=str(ipaddress.IPv4Address(controller_ip))
        self.run,self.socket_factory,self.system=run,socket_factory,system
        self.observations=[]

    def __call__(self,instance):
        if self.system()!='Darwin':
            raise AppleRuntimeError('apple_host_requires_darwin')
        host=str(ipaddress.IPv4Address(instance['host_ip']))
        if (instance['management_tcp']!=443 or instance['container_tcp']!=8443
                or host==self.controller_ip):
            raise AppleRuntimeError('apple_host_listener_not_reviewed')
        interfaces={}
        current=None
        text=self.run(['/sbin/ifconfig'],10)
        for line in text.splitlines():
            header=re.match(r'^([A-Za-z][A-Za-z0-9_.-]*):\s',line)
            if header:
                current=header[1]
            address=re.match(r'\s+inet ([0-9.]+)\s',line)
            if address and current:
                if address[1] in interfaces and interfaces[address[1]]!=current:
                    raise AppleRuntimeError('apple_host_address_ambiguous')
                interfaces[address[1]]=current
        if host not in interfaces:
            raise AppleRuntimeError('apple_reserved_host_address_not_assigned')
        route=self.run(['/sbin/route','-n','get',self.controller_ip],10)
        found=re.findall(r'^\s*interface:\s*([A-Za-z][A-Za-z0-9_.-]*)\s*$',route,re.M)
        if found!=[interfaces[host]] or found[0].startswith(('lo','utun')):
            raise AppleRuntimeError('apple_controller_route_not_same_lan_interface')
        firewall=self.run(['/usr/libexec/ApplicationFirewall/socketfilterfw','--getglobalstate'],10)
        if not re.fullmatch(r'Firewall is disabled\. \(State = 0\)\s*',firewall):
            # Do not recommend disabling a firewall or guess per-app rights.
            raise AppleRuntimeError('apple_application_firewall_policy_needs_qualified_review')
        stream=None
        try:
            stream=self.socket_factory(socket.AF_INET,socket.SOCK_STREAM)
            stream.settimeout(2)
            stream.bind((host,443))
        except PermissionError:
            raise AppleRuntimeError('apple_explicit_443_bind_requires_separate_privileged_transport') from None
        except OSError:
            raise AppleRuntimeError('apple_explicit_443_bind_unavailable') from None
        finally:
            if stream is not None:
                stream.close()
        self.observations.append({'slot':instance['slot'],'interface':interfaces[host],
            'application_firewall':'observed_disabled_not_modified',
            'explicit_tcp443_bind':'available_at_observation_no_listener_started',
            'other_packet_filters_and_controller_reachability':'needs_evidence'})
        return True
