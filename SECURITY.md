# Security policy

This project handles controller credentials, camera media and model-provider keys. Treat every deployment as security-sensitive. The current code is experimental and has no supported release line yet.

## Reporting a vulnerability

Do not include credentials, camera images, recordings, private hostnames or controller traces in a public issue. Use [GitHub's private vulnerability-reporting form](https://github.com/Olli0103/unifi-ai-key-emulator/security/advisories/new). Private vulnerability reporting was enabled and independently read back through GitHub's repository API on 29 October 2026. This verifies the enabled reporting route, not a test report or notification delivery.

Report the affected exact commit, Protect version, a synthetic reproduction, impact and proposed disclosure constraints. Redact sensitive material before submitting it. If GitHub cannot offer the private form to your account, use GitHub Support or contact the repository owner without disclosing the vulnerability, then arrange a private route. Never fall back to posting the details publicly.

The maintainer handling the report must agree receipt, affected versions and disclosure timing. There is no guaranteed response target; see [maintenance and support](MAINTAINERS.md). No stable supported release line exists yet. Report development-commit defects privately as well, rather than assuming experimental software has no security boundary.

## Deployment boundary

Run the service on a trusted host and network. Protect the state directory as a secret store and back it up only to encrypted, access-controlled storage. Expose device-service ports only where Protect requires them. The implemented administration site defaults to loopback and uses its own listener and credentials. It must not share the device-service authentication boundary; LAN administration is not enabled merely by serving that local site.

Remote inference and embedding services receive selected camera-derived data or text. Their destinations require explicit configuration. Plain HTTP outside loopback requires a separate opt-in. The clients reject redirects and do not inherit proxy settings from the environment.

See [the security contract](docs/security-contract.md) for trust boundaries, implemented controls and open work.
