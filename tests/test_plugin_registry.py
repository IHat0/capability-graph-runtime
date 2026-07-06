import pytest

from cgr.kernel.contracts import Capability, CapabilityVersion
from cgr.kernel.registry import PluginRegistry
from cgr.plugins.examples import EchoPlugin


@pytest.fixture
def capability() -> Capability:
    return Capability(
        id="echo",
        name="Echo",
        version=CapabilityVersion(major=1, minor=0, patch=0),
    )


def test_register_and_get_plugin() -> None:
    registry = PluginRegistry()
    plugin = EchoPlugin()

    registry.register(plugin)

    assert registry.get("echo") is plugin
    assert "echo" in registry
    assert len(registry) == 1
    assert registry.all() == [plugin]
    assert registry.plugin_ids() == ["echo"]


def test_get_unknown_plugin_raises_key_error() -> None:
    with pytest.raises(KeyError):
        PluginRegistry().get("missing")


def test_find_by_capability(capability: Capability) -> None:
    registry = PluginRegistry()
    matching_plugin = EchoPlugin()
    matching_plugin._metadata = matching_plugin.metadata.model_copy(
        update={"capabilities": [capability]}
    )
    non_matching_plugin = EchoPlugin()
    non_matching_plugin._metadata = non_matching_plugin.metadata.model_copy(
        update={"id": "other", "capabilities": []}
    )
    registry.register(matching_plugin)
    registry.register(non_matching_plugin)

    assert registry.find_by_capability(capability) == [matching_plugin]


def test_unregister_plugin() -> None:
    registry = PluginRegistry()
    plugin = EchoPlugin()
    registry.register(plugin)

    registry.unregister("echo")

    assert "echo" not in registry
    assert len(registry) == 0


def test_unregister_unknown_plugin_is_a_no_op() -> None:
    registry = PluginRegistry()

    registry.unregister("missing")

    assert len(registry) == 0


def test_duplicate_registration_raises_value_error() -> None:
    registry = PluginRegistry()
    registry.register(EchoPlugin())

    with pytest.raises(ValueError, match="already registered"):
        registry.register(EchoPlugin())
