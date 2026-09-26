"""Local build, inspection and explicitly started device operation."""

import argparse
import asyncio
import json
import logging
from pathlib import Path
import signal

from .config import (ConfigError, atomic_private, hydrate_secrets, initialize, load_config,
                     readiness, validate_config)


def _parser():
    parser = argparse.ArgumentParser(description="Experimental local UniFi Protect AI processor")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Create local identity, private keys and example configuration")
    init.add_argument("--config", type=Path, default=Path("config.json"))
    init.add_argument("--state-dir", type=Path, default=Path("state"))
    init.add_argument("--controller", help="Controller hostname or LAN IP, without contacting it")
    init.add_argument("--device-ip", help="Emulator advertised and bind IP")
    provider = commands.add_parser("provider", help="Configure a vision provider without calling it")
    provider.add_argument("name", choices=("openai", "anthropic", "ollama", "openai-compatible"))
    provider.add_argument("--config", type=Path, default=Path("config.json"))
    provider.add_argument("--model", required=True, help="A vision-capable model available to this provider")
    provider.add_argument("--base-url", help="API base URL; required for a generic compatible provider")
    provider.add_argument("--api-key-file", type=Path, help="Private API key file, never the key itself")
    provider.add_argument("--allow-remote", action="store_true", help="Allow the configured non-loopback API")
    provider.add_argument("--allow-insecure-http", action="store_true", help="Allow unencrypted remote LAN API")
    for name, help_text in (("check", "Report local readiness without network access"),
                            ("run", "Start the configured management and controller connections")):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--config", type=Path, default=Path("config.json"))
    trust = commands.add_parser("trust", help="Import controller certificate using a verified fingerprint")
    trust.add_argument("--config", type=Path, default=Path("config.json"))
    trust.add_argument("--fingerprint", required=True, help="Independently verified SHA-256 fingerprint")
    trust.add_argument("--port", type=int, help="TLS endpoint, defaults to configured control port")
    lab = commands.add_parser("lab", help="Run synthetic end-to-end loopback test, without Protect")
    lab.add_argument("--output", type=Path, default=Path("lab-results.json"))
    inventory = commands.add_parser("inventory", help="Read-only Protect camera preflight; no processing is enabled")
    inventory.add_argument("--config", type=Path, default=Path("config.json"))
    inventory.add_argument("--api-key-file", type=Path, required=True)
    inventory.add_argument("--web-trust-file", type=Path, required=True)
    inventory.add_argument("--web-cert-file", type=Path, required=True)
    inventory.add_argument("--output", type=Path, help="Private report beside the API key file by default")
    return parser


async def _run(config):
    from .runtime import Application
    service = Application(hydrate_secrets(config))
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop.set)
    try:
        await service.start()
        logging.getLogger("aikey").info("Management service started on HTTPS port %s", service.https_port)
        await stop.wait()
    finally:
        await service.stop()


def main(argv=None):
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        if args.command == "init":
            initialize(args.config, args.state_dir, controller_host=args.controller,
                       device_ip=args.device_ip)
            print(json.dumps({"configuration": str(args.config.resolve()),
                              "state": str(args.state_dir.resolve()), "network_contacted": False}))
            return 0
        if args.command == "lab":
            from .lab import run_lab
            report = asyncio.run(run_lab())
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps(report, indent=2))
            return 0 if report["passed"] else 1
        config = load_config(args.config)
        if args.command == "inventory":
            from .camera_inventory import fetch_inventory, render_html
            output = args.output or args.api_key_file.with_name("camera-inventory-preflight.json")
            if (output.suffix != ".json" or output.parent.resolve() != args.api_key_file.parent.resolve()
                    or output.is_symlink()):
                raise ConfigError("Inventory report must be beside the private API key file")
            html_output = output.with_suffix(".html")
            if html_output.is_symlink():
                raise ConfigError("Inventory HTML report path must not be a symlink")
            protected = {path.resolve() for path in (args.config, args.api_key_file,
                         args.web_trust_file, args.web_cert_file)}
            if output.resolve() in protected or html_output.resolve() in protected:
                raise ConfigError("Inventory report cannot replace configuration, key or trust files")
            report = asyncio.run(fetch_inventory(config["controller"]["host"],
                api_key_file=args.api_key_file, trust_file=args.web_trust_file,
                cert_file=args.web_cert_file))
            atomic_private(output, json.dumps(report, indent=2, ensure_ascii=False) + "\n")
            atomic_private(html_output, render_html(report))
            print(json.dumps({"report": str(output), "html_report": str(html_output),
                              "protect_version": report["protect_version"],
                              "summary": report["summary"], "processing_enabled": False}))
            return 0
        if args.command == "provider":
            if args.name in {"openai", "anthropic"} and args.api_key_file is None:
                raise ConfigError("Hosted provider requires --api-key-file; do not pass a key in a command argument")
            if args.name == "openai-compatible" and not args.base_url:
                raise ConfigError("An OpenAI-compatible provider requires --base-url")
            inference = {"provider": args.name, "model": args.model,
                "base_url": args.base_url or {"openai": "https://api.openai.com/v1",
                                              "anthropic": "https://api.anthropic.com/v1",
                                              "ollama": "http://127.0.0.1:11434"}.get(args.name),
                "allow_remote": args.allow_remote or args.name in {"openai", "anthropic"},
                "allow_insecure_http": args.allow_insecure_http}
            if args.api_key_file:
                inference["api_key_file"] = str(args.api_key_file.expanduser().resolve())
            config["inference"] = inference
            checked = validate_config(config, base=args.config.resolve().parent)
            from .providers import validate_inference_config
            validate_inference_config(checked["inference"],
                lab=checked["runtime"]["mode"] == "lab", require_api_key=False)
            atomic_private(args.config, json.dumps(checked, indent=2) + "\n")
            print(json.dumps({"provider": args.name, "model": args.model,
                              "configuration_saved": True, "provider_contacted": False}))
            return 0
        report = readiness(config)
        if args.command == "check":
            print(json.dumps(report, indent=2))
            return 0 if report["ready_for_device_start"] else 2
        if args.command == "trust":
            from .tls import import_controller_trust
            if not config["controller"]["host"]:
                raise ConfigError("Set controller.host before importing trust")
            fingerprint = import_controller_trust(config["controller"]["host"],
                args.port or config["controller"]["control_port"], args.fingerprint,
                Path(config["controller"]["ca_file"]))
            print(json.dumps({"trusted_sha256": fingerprint, "http_requests_sent": 0}))
            return 0
        if not report["ready_for_device_start"]:
            raise ConfigError("Configuration is incomplete. Run 'local-aikey check' for missing items")
        asyncio.run(_run(config))
        return 0
    except (ConfigError, OSError, ValueError, RuntimeError) as exc:
        logging.getLogger("aikey").error("%s", exc)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
