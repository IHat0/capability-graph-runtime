"""Optional pristine SWE-agent/OpenAI-compatible repair provider.

This package is intentionally not imported by :mod:`cgr.quantum_repair`.
"""

from .config import SWEAgentProviderConfig, load_provider_config
from .provider import SWEAgentOpenAICompatibleRepairProvider

__all__ = [
    "SWEAgentOpenAICompatibleRepairProvider",
    "SWEAgentProviderConfig",
    "load_provider_config",
]
