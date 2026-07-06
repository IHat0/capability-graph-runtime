import pytest

from cgr.kernel.contracts import ExecutionContext, ExecutionRequest, PluginState
from cgr.kernel.loader import PluginLoader, PluginLoadError
from cgr.kernel.runtime import KernelRuntime
from cgr.plugins.examples import EchoPlugin
from cgr.shared.events import EventType

ECHO_IMPORT_PATH = "cgr.plugins.examples.echo_plugin:EchoPlugin"


def test_load_returns_echo_plugin_with_metadata() -> None:
    plugin = PluginLoader().load(ECHO_IMPORT_PATH)

    assert isinstance(plugin, EchoPlugin)
    assert plugin.metadata.id == "echo"


@pytest.mark.parametrize(
    "import_path",
    ["missing_separator", "module:Class:extra", ":Class", "module:"],
)
def test_load_rejects_invalid_import_path_format(import_path: str) -> None:
    with pytest.raises(
        PluginLoadError,
        match="Plugin import path must be in format",
    ):
        PluginLoader().load(import_path)


def test_load_reports_missing_module() -> None:
    with pytest.raises(
        PluginLoadError,
        match="Could not import plugin module 'cgr.missing_module'",
    ):
        PluginLoader().load("cgr.missing_module:MissingPlugin")


def test_load_reports_missing_class() -> None:
    with pytest.raises(
        PluginLoadError,
        match=(
            "Plugin class 'MissingPlugin' was not found in module "
            "'cgr.plugins.examples.echo_plugin'"
        ),
    ):
        PluginLoader().load(
            "cgr.plugins.examples.echo_plugin:MissingPlugin"
        )


def test_load_rejects_object_that_is_not_plugin() -> None:
    with pytest.raises(
        PluginLoadError,
        match="Loaded object 'builtins:dict' is not a Plugin",
    ):
        PluginLoader().load("builtins:dict")


def test_load_reports_class_that_cannot_be_instantiated() -> None:
    with pytest.raises(
        PluginLoadError,
        match="Could not instantiate plugin 'builtins:memoryview'",
    ):
        PluginLoader().load("builtins:memoryview")


def test_load_many_loads_plugins_in_order() -> None:
    plugins = PluginLoader().load_many([ECHO_IMPORT_PATH, ECHO_IMPORT_PATH])

    assert [plugin.metadata.id for plugin in plugins] == ["echo", "echo"]
    assert plugins[0] is not plugins[1]


def test_load_many_raises_when_any_plugin_fails() -> None:
    with pytest.raises(PluginLoadError):
        PluginLoader().load_many(
            [ECHO_IMPORT_PATH, "cgr.missing_module:MissingPlugin"]
        )


def test_runtime_load_plugins_registers_initializes_and_executes_echo() -> None:
    runtime = KernelRuntime()

    runtime.load_plugins([ECHO_IMPORT_PATH])

    plugin = runtime.registry.get("echo")
    assert isinstance(plugin, EchoPlugin)
    assert plugin.state == PluginState.RUNNING
    request = ExecutionRequest[dict[str, str]](
        capability=plugin.metadata.capabilities[0],
        context=ExecutionContext(),
        payload={"message": "loaded"},
    )
    result = runtime.execute_capability(request)
    assert result.output == {"message": "loaded"}


def test_runtime_load_plugins_emits_plugin_registered_event() -> None:
    runtime = KernelRuntime()

    runtime.load_plugins([ECHO_IMPORT_PATH])

    events = runtime.event_bus.history_by_type(EventType.PLUGIN_REGISTERED)
    assert len(events) == 1
    assert events[0].payload["plugin_id"] == "echo"
