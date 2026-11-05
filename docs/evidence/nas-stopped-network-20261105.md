# NAS stopped-container network verification — 5 November 2026

## Reproduced boundary

A controlled restart of one existing NAS Port reached the real reconciler but
stopped before its Docker start command: the network identity check expected
running endpoint fields on an exited container. The original container was
recovered without replacement or old-state restoration. A separate native
controller read retained the original Key, four Port identities, paired-camera
sets and inspected camera settings. That failed attempt is not a successful
reconciler restart.

A disposable internal-bridge fixture on Docker 29.6.2 independently reproduced
the endpoint lifecycle. Before first start, the explicit MAC and saved static
IPAM address were present, while the runtime IP was empty. While running, the
runtime IP and MAC were present. After stop, both runtime fields were empty;
the saved IPAM address remained. The owned fixture container and network were
removed and their absence checked. This bridge fixture alone does not qualify
the production macvlan start path.

## Narrow correction

The actor retains a successfully inspected container binding in its private
intent, including on an apply that starts nothing. An exited container with a
missing runtime MAC may reuse only the exact previously verified container ID,
under the same manifest, configuration and persistent-identity revision.
Inactive endpoints must still expose the exact saved static IPAM address for
both the LAN and optional health network. Nonempty mismatched fields, unknown
containers, changed revisions and created endpoints without their requested
MAC remain refusals. Running endpoints always require their actual IP and MAC.

The post-start inspection, readiness check, durable command evidence, pending
intent fence, startup-byte reservation and rollback ownership checks remain
in place. A wrong live MAC after start leaves an uncertain intent and blocks
another apply. No image label is used as a substitute for implementing the
startup reservation contract.

This relies on the previously checked binding and Docker's stable container
identity, not a claim that Docker currently reports an inactive MAC. A
privileged daemon operator can change a container's endpoints between reads;
the private intent is not an authority boundary against that operator.

## Qualification limits

Focused regressions exercise creation, no-op binding persistence, same-ID
restart, wrong or missing saved addresses, replaced IDs, changed configuration,
missing proof, post-start MAC mismatch and the isolated health network.
Existing layout fixtures now include Docker's explicit saved IPAM assignment.

At source preparation, the corrected production reconciler restart is still
pending. Mac listener/start qualification, new-slot addition/removal and the
complete native failure matrix are separate original acceptance items. The
broader issue remains OPEN; this change does not attest those boundaries or
close the original issue.

Private receipts retain the failed attempt, same-container recovery, isolated
Docker observations and exact local module/container bindings. No camera
media, private addresses, credentials or deployment identifiers are included
here.
