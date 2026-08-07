from functools import partial

from artmach_assistant.core.runtime_instrumentation import _callable_identity_metadata


def sample_action() -> None:
    return None


class Example:
    def method(self) -> None:
        return None


def test_callable_identity_resolves_function_and_bound_method(tmp_path) -> None:
    function_meta = _callable_identity_metadata(sample_action, tmp_path)
    assert function_meta["action_symbol"].endswith("sample_action")
    assert str(function_meta["action_path"]).endswith(
        "tests/test_runtime_action_identity.py"
    )

    method_meta = _callable_identity_metadata(Example().method, tmp_path)
    assert method_meta["action_symbol"].endswith("Example.method")


def test_callable_identity_unwraps_partial(tmp_path) -> None:
    meta = _callable_identity_metadata(partial(sample_action), tmp_path)
    assert meta["action_symbol"].endswith("sample_action")
