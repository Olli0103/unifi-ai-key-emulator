# Read-only synthetic control-site demo

After installing the package, run:

```sh
local-aikey-demo --port 8766
```

Or from a source checkout with its dependencies installed:

```sh
PYTHONPATH=src python -m aikey.control_demo --port 8766
```

Open `http://127.0.0.1:8766/` on the same host. Stop with Ctrl-C. No login,
credential, model download, controller, configuration file or state directory is
needed. The command has no non-loopback bind, controller, credential or state
argument. If the port is busy, choose another unprivileged port; do not replace
an existing service. No LAN exposure or proxy deployment is part of this demo.

The preview shows separate fictional model roles, camera opt-outs, an
illustrative rolling budget, clearly written example results and independent
retention copies. It is intentionally read-only: no provider-save/test,
discovery/adoption, rollout, camera mutation or device-health route exists.
It initializes no processor and reads no deployment configuration or media.
No model prediction, native result, quality score, measured latency or price
is claimed. It reuses the existing HTTP library and page renderer, not a new
framework or background service. It writes no application state; host/browser
history or host-level logging is outside that guarantee.

This is a credentials-free synthetic preview for #25, **not** the first-run
wizard or a working deployment. Use [the real control-site guide](control-site.md)
for authenticated configuration. The broader fresh-install, native adoption,
browser/keyboard/screen-reader, mobile, uninstall and restore acceptance remains
open. The page uses semantic headings, section links and a skip link; HTTP/HTML
tests alone do not prove visual or assistive-technology usability.
