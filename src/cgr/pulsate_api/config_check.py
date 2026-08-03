"""Non-networked production configuration and durable-provider validation."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable

from .production_configuration import (
    ConfigurationSource,
    EnvironmentConfigurationSource,
    PulsateRuntimeConfiguration,
    load_production_configuration,
)
from .production_catalogue import ProductionCapabilityCatalogue
from .production_security import ProductionSecurityServices, create_production_security_services


def validate_configuration(
    source: ConfigurationSource,
    *,
    services_factory: Callable[[PulsateRuntimeConfiguration], ProductionSecurityServices] = create_production_security_services,
) -> dict[str, object]:
    configuration = load_production_configuration(source)
    services = services_factory(configuration)
    catalogue = ProductionCapabilityCatalogue(
        configuration.catalogue,
        trusted_configuration_root=(
            configuration.repositories.trusted_configuration_root
        ),
        event_logger=services.events,
    )
    try:
        services.start()
        catalogue.start()
        if not services.ready() or not catalogue.ready():
            raise RuntimeError("Production runtime services are unavailable.")
        catalogue_status = catalogue.diagnostic_snapshot()
        return {
            "status": "ready",
            "configuration_fingerprint": configuration.fingerprint,
            "catalogue": {
                "status": "ready",
                "identifier": catalogue_status["catalogue_identifier"],
                "version": catalogue_status["catalogue_version"],
                "fingerprint": catalogue_status["catalogue_fingerprint"],
                "entry_count": catalogue_status["entry_count"],
            },
            "components": {
                "configuration": "ready",
                "authentication": "ready",
                "authorization": "ready",
                "audit": "ready",
                "catalogue": "ready",
            },
        }
    finally:
        try:
            catalogue.close()
        finally:
            services.close()


def main(
    argv: list[str] | None = None,
    *,
    source: ConfigurationSource | None = None,
    services_factory: Callable[[PulsateRuntimeConfiguration], ProductionSecurityServices] = create_production_security_services,
) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if arguments:
        print(json.dumps({"status": "invalid", "category": "unexpected_arguments"}), file=sys.stderr)
        return 2
    try:
        result = validate_configuration(
            source or EnvironmentConfigurationSource(),
            services_factory=services_factory,
        )
    except Exception:
        print(
            json.dumps(
                {"status": "unavailable", "category": "configuration_validation_failed"},
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
