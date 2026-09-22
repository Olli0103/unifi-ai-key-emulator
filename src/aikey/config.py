"""Configuration and persistent identity. Reading or initializing never uses the network."""

import copy
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import time
from urllib.parse import urlsplit


class ConfigError(ValueError):
    pass


def atomic_private(path: Path, content: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    try:
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(content.encode() if isinstance(content, str) else content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def defaults(state_dir: Path, mac: str) -> dict:
    return {
        "runtime": {"mode": "device", "bind": "127.0.0.1", "http_port": 8000,
                    "https_port": 8080, "enable_http": False, "state_dir": str(state_dir)},
        "device": {"mac": mac, "ip": "127.0.0.1", "name": "Local AI processor",
                   "model": "UP-AI-KEY", "sysid": "0xa5f0", "firmware_version": "2.2.8",
                   "management_username": "ui",
                   "management_password_file": str(state_dir / "management-password")},
        "controller": {"host": "", "control_port": 7442, "search_port": 7443,
                       "media_port": 7444, "ca_file": str(state_dir / "controller-ca.pem"),
                       "protect_version": "7.3.56", "verify_hostname": True},
        "inference": {"provider": "openai-compatible", "base_url": "http://127.0.0.1:11434/v1", "model": ""},
        "worker": {"max_queue": 8, "max_concurrency": 1, "timeout_s": 120,
                   "max_video_duration_ms": 120000,
                   "legacy_profile": "protect-7.2.105", "callback_mode": "enabled"},
        "embeddings": {"backend": "http", "base_url": "http://127.0.0.1:8081/v1",
                       "model": "intfloat/multilingual-e5-small"},
        "search": {"enabled": False, "profile": "e5-session-v1"},
        "discovery": {"enabled": False, "bind": "127.0.0.1", "port": 10001,
                      "multicast": False, "allowed_controller_ips": []},
        "database": {"enabled": False, "host": "127.0.0.1", "port": 5432,
                     "dbname": "unifi-protect", "user": "unifi-protect",
                     "password_file": str(state_dir / "database-password"),
                     "sslrootcert": str(state_dir / "device.crt")},
    }


def initialize(config_path: Path, state_dir: Path, *, controller_host: str | None = None,
               device_ip: str | None = None) -> dict:
    config_path, state_dir = config_path.resolve(), state_dir.resolve()
    if config_path.exists():
        raise ConfigError("Configuration already exists; initialization will not overwrite it")
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    identity_path = state_dir / "identity.json"
    if identity_path.exists():
        identity = json.loads(identity_path.read_text())
        mac = identity["mac"]
    else:
        raw = bytes([0x02]) + secrets.token_bytes(5)
        mac = raw.hex().upper()
        atomic_private(identity_path, json.dumps({"mac": mac}) + "\n")
    password_path = state_dir / "management-password"
    if not password_path.exists():
        atomic_private(password_path, secrets.token_urlsafe(32) + "\n")
    database_password_path = state_dir / "database-password"
    if not database_password_path.exists():
        atomic_private(database_password_path, password_path.read_bytes())
    config = defaults(state_dir, mac)
    if controller_host is not None:
        config["controller"]["host"] = controller_host
    if device_ip is not None:
        config["device"]["ip"] = device_ip
        config["runtime"]["bind"] = device_ip
    config = validate_config(config, base=config_path.parent)
    from .tls import ensure_identity_certificate
    ensure_identity_certificate(state_dir, mac)
    atomic_private(config_path, json.dumps(config, indent=2) + "\n")
    return config


def load_config(path: Path) -> dict:
    try:
        config = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise ConfigError("Cannot read valid JSON configuration") from exc
    return validate_config(config, base=path.resolve().parent)


def validate_factory_enrollment_deadline(value: int) -> int:
    """Accept an explicit bounded deadline; expired deadlines stay inactive."""
    if type(value) is not int or value < 0 or value > time.time() + 600:
        raise ConfigError("device.factory_enrollment_until must be an epoch second no more than 10 minutes ahead; 0 disables it")
    return value


def validate_config(value: dict, *, base: Path | None = None) -> dict:
    if not isinstance(value, dict):
        raise ConfigError("Configuration must be a JSON object")
    config = copy.deepcopy(value)
    for key in ("runtime", "device", "controller", "inference", "worker", "embeddings", "search"):
        if not isinstance(config.get(key), dict):
            raise ConfigError(f"Missing configuration section: {key}")
    boolean_fields = {
        "runtime": ("enable_http",), "inference": ("allow_remote", "allow_insecure_http"),
        "embeddings": ("allow_remote", "allow_insecure_http"),
        "worker": ("description_embeddings", "request_mp4_exports"),
        "search": ("enabled",), "database": ("enabled",),
        "discovery": ("enabled", "multicast"),
    }
    for section, fields in boolean_fields.items():
        options = config.get(section, {})
        if not isinstance(options, dict):
            raise ConfigError(f"{section} must be a configuration object")
        for field in fields:
            if field in options and type(options[field]) is not bool:
                raise ConfigError(f"{section}.{field} must be a JSON boolean")
    runtime, device, controller = config["runtime"], config["device"], config["controller"]
    if "max_video_duration_ms" in config["worker"]:
        duration = config["worker"]["max_video_duration_ms"]
        if type(duration) is not int or duration <= 0:
            raise ConfigError("worker.max_video_duration_ms must be a positive integer")
    if "test_scope" in config["worker"]:
        from .worker import WorkerError, validate_test_scope_config
        try:
            config["worker"]["test_scope"] = validate_test_scope_config(config["worker"]["test_scope"])
        except WorkerError as exc:
            raise ConfigError(str(exc)) from exc
    if runtime.get("mode") not in ("device", "lab"):
        raise ConfigError("runtime.mode must be device or lab")
    if "deployment" in runtime:
        label = runtime["deployment"]
        if (not isinstance(label, str) or not label.strip() or len(label) > 128
                or not label.isprintable()):
            raise ConfigError("runtime.deployment must be a nonempty printable string of at most 128 characters")
        runtime["deployment"] = label.strip()
    try:
        bind = ipaddress.ip_address(runtime["bind"])
        ipaddress.ip_address(device["ip"])
    except (ValueError, KeyError, TypeError) as exc:
        raise ConfigError("Bind and advertised device IP must be explicit IP addresses") from exc
    if runtime["mode"] == "lab" and not bind.is_loopback:
        raise ConfigError("Lab mode binds only to loopback")
    if runtime.get("enable_http", False) and runtime["mode"] != "lab":
        raise ConfigError("Plain HTTP management is restricted to the local lab")
    for section, keys in ((runtime, ("http_port", "https_port")),
                          (controller, ("control_port", "search_port", "media_port"))):
        for key in keys:
            if type(section.get(key)) is not int or not 1 <= section[key] <= 65535:
                raise ConfigError(f"{key} must be a TCP port between 1 and 65535")
    mac = re.sub(r"[:-]", "", str(device.get("mac", ""))).upper()
    if not re.fullmatch(r"[0-9A-F]{12}", mac) or int(mac[:2], 16) & 1:
        raise ConfigError("device.mac must be a unicast MAC address")
    device["mac"] = mac
    for key in ("name", "management_username"):
        if not isinstance(device.get(key), str) or not device[key]:
            raise ConfigError(f"device.{key} must be nonempty")
    factory_until = validate_factory_enrollment_deadline(device.get("factory_enrollment_until", 0))
    host = controller.get("host", "")
    if not isinstance(host, str) or any(c in host for c in "/?#@\r\n "):
        raise ConfigError("controller.host must be a hostname or IP, without scheme or path")
    if type(controller.get("verify_hostname", True)) is not bool:
        raise ConfigError("controller.verify_hostname must be a boolean")
    control_profile = controller.get("control_profile", "ucp4")
    if control_profile not in ("ucp4", "device-service"):
        raise ConfigError("controller.control_profile must be ucp4 or device-service")
    if control_profile == "device-service" or factory_until > time.time():
        fingerprint = controller.get("expected_fingerprint")
        if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", fingerprint.replace(":", "")):
            raise ConfigError("Device-service control or active factory enrollment requires an explicit controller SHA-256 pin")
    if not controller.get("verify_hostname", True):
        pin = controller.get("expected_fingerprint", "").replace(":", "")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", pin):
            raise ConfigError("Disabling hostname checks requires an explicit controller SHA-256 pin")
    if runtime["mode"] == "lab" and host:
        try:
            is_loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            is_loopback = host == "localhost"
        if not is_loopback:
            raise ConfigError("Lab controller must be loopback")
    base = base or Path.cwd()
    for section, key in ((runtime, "state_dir"), (device, "management_password_file"),
                         (controller, "ca_file")):
        raw = section.get(key)
        if not isinstance(raw, str) or not raw:
            raise ConfigError(f"{key} must be a file path")
        section[key] = str((base / Path(raw).expanduser()).resolve())
    if device.get("management_password"):
        raise ConfigError("Store management credentials in management_password_file")
    for section, key in ((config["inference"], "api_key_file"),
                         (config["embeddings"], "bearer_token_file")):
        if section.get(key):
            p = (base / Path(section[key]).expanduser()).resolve()
            section[key] = str(p)
    database = config.get("database", {})
    if not isinstance(database, dict):
        raise ConfigError("database must be a configuration object")
    for section, key in ((database, "password_file"), (database, "sslrootcert"),
                         (config["embeddings"], "model_path")):
        if section.get(key):
            section[key] = str((base / Path(section[key]).expanduser()).resolve())
    for section in (config["inference"], config["embeddings"]):
        if "base_url" in section:
            u = urlsplit(section["base_url"])
            if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password:
                raise ConfigError("Inference/embedding URLs must be HTTP(S), without credentials")
    if config["inference"].get("provider", "openai-compatible") not in {
            "openai", "ollama", "openai-compatible"}:
        raise ConfigError("Unknown vision provider")
    if config["inference"].get("api_key"):
        raise ConfigError("Use inference.api_key_file instead of an inline API key")
    # Initial configuration deliberately leaves the model empty. Validate the
    # remaining provider settings now without replacing that missing model.
    from .providers import ProviderError, validate_inference_config
    inference = dict(config["inference"])
    if inference.get("model") in (None, ""):
        inference["model"] = "configuration-not-yet-selected"
    try:
        validate_inference_config(inference, lab=runtime["mode"] == "lab", require_api_key=False)
    except ProviderError as exc:
        raise ConfigError(str(exc)) from None
    if config["embeddings"].get("backend", "http") == "http":
        from .search import EmbeddingError, EmbeddingService
        try:
            EmbeddingService(config["embeddings"]).identity
        except EmbeddingError as exc:
            raise ConfigError(str(exc)) from None
    config["controller_origins"] = []
    if host:
        authority = f"[{host}]" if ":" in host else host
        for port in dict.fromkeys((controller["media_port"], controller["control_port"],
                                  controller["search_port"])):
            config["controller_origins"].append(f"https://{authority}:{port}")
        config["controller_media_origin"] = config["controller_origins"][0]
    return config


def hydrate_secrets(config: dict) -> dict:
    config = copy.deepcopy(config)
    path = Path(config["device"]["management_password_file"])
    try:
        password = path.read_text().strip()
    except OSError as exc:
        raise ConfigError("Management credential file is not readable") from exc
    if len(password) < 16:
        raise ConfigError("Management credential must contain at least 16 characters")
    config["device"]["management_password"] = password
    key_path = config["inference"].get("api_key_file")
    if key_path:
        config["inference"]["api_key"] = Path(key_path).read_text().strip()
    return config


def readiness(config: dict) -> dict:
    """A local readiness report, with no network probing and no secret values."""
    state = Path(config["runtime"]["state_dir"])
    checks = {
        "controller_host": bool(config["controller"].get("host")),
        "controller_trust_file": Path(config["controller"]["ca_file"]).is_file(),
        "device_certificate": (state / "device.crt").is_file(),
        "device_private_key": (state / "device.key").is_file(),
        "management_credential_file": Path(config["device"]["management_password_file"]).is_file(),
        "vision_model_configured": bool(config["inference"].get("model")),
    }
    provider = config["inference"].get("provider", "openai-compatible")
    if provider == "openai":
        checks["vision_api_key_file"] = Path(config["inference"].get("api_key_file", "")).is_file()
    errors = {}
    try:
        hydrated = hydrate_secrets(config)
        checks["credentials_readable"] = True
    except (OSError, ValueError, UnicodeError):
        hydrated = None
        checks["credentials_readable"] = False
        errors["credentials_readable"] = "Required credential files are unreadable or invalid"
    if hydrated is not None:
        from .providers import ProviderError, validate_inference_config
        try:
            validate_inference_config(hydrated["inference"], lab=config["runtime"]["mode"] == "lab")
            checks["vision_configuration_valid"] = True
        except ProviderError as exc:
            checks["vision_configuration_valid"] = False
            errors["vision_configuration_valid"] = str(exc)
    else:
        checks["vision_configuration_valid"] = False
    return {"ready_for_device_start": all(checks.values()), "checks": checks, "errors": errors,
            "target": {"console": "UDM Pro Max", "protect": config["controller"].get("protect_version"),
                       "deployment": config["runtime"].get("deployment", "unspecified")},
            "inspected_controller": "7.2.105", "native_compatibility": "needs_evidence",
            "vision_provider": provider,
            "search_enabled": bool(config["search"].get("enabled")),
            "database_enabled": config.get("database", {}).get("enabled") is True,
            "discovery_enabled": config.get("discovery", {}).get("enabled") is True,
            "live_adoption": "not_tested", "native_search": "not_tested"}
