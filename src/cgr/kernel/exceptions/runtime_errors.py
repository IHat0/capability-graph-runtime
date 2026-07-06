"""Runtime exception types for the Capability Graph Runtime."""


class CGRRuntimeError(Exception):
    """Base class for all CGR runtime errors."""


class PluginNotFoundError(CGRRuntimeError):
    """Raised when a requested plugin is not registered."""


class CapabilityNotFoundError(CGRRuntimeError):
    """Raised when no registered plugin supports a capability."""


class PluginAlreadyRegisteredError(CGRRuntimeError):
    """Raised when a plugin identifier is already registered."""


class PluginExecutionError(CGRRuntimeError):
    """Reserved for future wrapping of plugin execution failures."""
