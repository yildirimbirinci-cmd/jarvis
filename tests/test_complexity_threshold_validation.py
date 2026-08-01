import pytest
from artmach_assistant.core.complexity_analyzer import ComplexityThresholds

@pytest.mark.parametrize("value", [True, 1.5, "3", 0])
def test_invalid_threshold_types_are_rejected(value):
    with pytest.raises(ValueError):
        ComplexityThresholds(cyclomatic_warning=value)
