# Security policy

This project handles controller credentials, camera media and model-provider keys. Treat every deployment as security-sensitive. The current code is experimental and has no supported release line yet.

## Reporting a vulnerability

Do not include credentials, camera images, recordings, private hostnames or controller traces in a public issue. Use GitHub's private vulnerability-reporting form if the repository exposes it. If that form is unavailable, private reporting remains `needs_evidence`; contact the repository owner without sending the sensitive material and arrange a private channel first.

The maintainer must confirm receipt, affected versions and disclosure timing. No response target is promised until the maintenance policy in issue #11 is adopted.

## Deployment boundary

Run the service on a trusted host and network. Protect the state directory as a secret store and back it up only to encrypted, access-controlled storage. Expose device-service ports only where Protect requires them. The planned administration site is not implemented and must not share the device-service listener or credentials.

Remote inference and embedding services receive selected camera-derived data or text. Their destinations require explicit configuration. Plain HTTP outside loopback requires a separate opt-in. The clients reject redirects and do not inherit proxy settings from the environment.

See [the security contract](docs/security-contract.md) for trust boundaries, implemented controls and open work.
