# Compatibility and limitations

The project uses private, version-dependent Protect protocols. It is experimental
source, not a supported replacement for all AI Key or AI Port functions. The
package version `0.1.0` is not a stable release or a compatibility guarantee.

## Read evidence by feature and version

The [compatibility matrix](evidence/compatibility-matrix.md) and its
[manifest](evidence/compatibility-manifest.json) distinguish implementation,
synthetic tests and native observations. The [evidence index](evidence/README.md)
links later dated observations. Native trials cover selected Protect 7.3.x
versions through 7.3.74; static inspection of 7.2.105 is a different kind of
source. Success on one version does not establish a later version's behavior.

- `implemented` means source exists.
- `fixture-tested` means synthetic tests exercised that contract.
- `native-verified` applies only to the behavior, version and conditions in the
  cited observation. It does not qualify the whole product.
- `needs_evidence` and `unsupported` are not success states.

## Deployment limits

Linux AMD64 and ARM64 default-image installation is checked in native CI.
Those checks use isolated synthetic services, not your controller or cameras.
Docker host networking requires Linux and free host ports. Multi-instance AI
Ports need separate reserved addresses and a reviewed network plan. macvlan
usually prevents direct host-to-container access without another network path.

Apple container has different NAT, DNS, privileged-port and restart behavior.
Use its specific guides. Linux ARM64 image success does not prove Apple runtime
networking, daemon recovery or power-loss recovery. No universal NAS OS, firmware,
filesystem or hardware guarantee is made.

## Feature limits

AI Key descriptions and optional search are separate from AI Port stream
inference. Callback acceptance does not prove saved native results. Search
requires matching models, dimensions, database extensions, migrations and
controller-managed credentials. Do not replace an embedding model without the
[index migration procedure](index-migration.md).

Faces, plates, speech, reverification, zones and enhancement have separate
configuration and native acceptance boundaries. Local/API model success is not
detection accuracy, identity accuracy or alarm delivery. No model weights are
bundled. ONVIF metadata planning does not implement ONVIF ingest, PTZ or recording.

Fresh independent installation, full native feature parity, broad quality
calibration, sustained multi-camera operation and complete disaster recovery
remain unqualified. Consult the [roadmap](../PLAN.md) and open issues for missing
work. Do not widen scope merely because an unrelated native trial passed.

## Reporting a result

Record the exact source SHA/image digest, Protect version, profile, host runtime,
model identity and test outcome. Use synthetic identifiers in public reports.
Separate config saved, process loaded, callback accepted and native result saved.
Retain failures as well as successes. Use the repository's compatibility issue
form and follow [contributing privacy rules](../CONTRIBUTING.md).
