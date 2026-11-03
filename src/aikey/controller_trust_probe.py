"""Explicit anonymous TLS-only controller tests; never load device credentials."""
import asyncio
import hashlib
import hmac
import ssl
import time

from .controller_trust import TrustObject,context


async def probe(targets,*,verify_hostname,admission=None):
    if (not isinstance(targets,list) or not 1<=len(targets)<=2
            or type(verify_hostname) is not bool
            or any(not isinstance(item,tuple) or len(item)!=2 or not isinstance(item[0],TrustObject)
                   or type(item[1]) is not int or not 1<=item[1]<=65535 for item in targets)
            or len({item[0].purpose for item in targets})!=len(targets)):
        raise ValueError('invalid_controller_probe')
    results=[]
    for trust,port in targets:
        if admission is not None and admission() is not True:
            raise ValueError('controller_probe_authority_expired')
        started=time.monotonic_ns()
        writer=None
        outcome='matched'
        try:
            # This context contains only the reviewed public trust object.
            # No device cert/key, management password, bearer or HTTP request.
            client=context(trust,check_hostname=verify_hostname)
            async with asyncio.timeout(5):
                _,writer=await asyncio.open_connection(trust.host,port,ssl=client,server_hostname=trust.host,
                    ssl_handshake_timeout=4.5,ssl_shutdown_timeout=.25)
                peer=writer.get_extra_info('ssl_object').getpeercert(binary_form=True)
                if not hmac.compare_digest(hashlib.sha256(peer).hexdigest(),trust.fingerprint):
                    outcome='fingerprint_mismatch'
        except ssl.SSLCertVerificationError:
            outcome='certificate_verification_failed'
        except TimeoutError:
            outcome='timeout'
        except (OSError,ValueError,RuntimeError):
            outcome='connection_failed'
        finally:
            if writer is not None:
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(),.5)
                except (OSError,TimeoutError):
                    writer.transport.abort()
        results.append({'channel':trust.purpose,'outcome':outcome,'matched':outcome=='matched',
                        'client_attempts':1,'latency_ms':(time.monotonic_ns()-started)/1e6})
    return {'schema':'aikey-controller-tls-probe/1','channels':results,
            'matched':all(row['matched'] for row in results),'scope':'anonymous_tls_handshake_and_peer_pin',
            'device_credentials_sent':False,'http_requests_sent':0,'configuration_saved':False,
            'native_adoption_verified':False,'automatic_retry':False}
