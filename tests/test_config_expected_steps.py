from hooks_report import config


def test_expected_steps_derived_from_step_timeouts():
    """EXPECTED_STEPS must be a subset of STEP_TIMEOUTS keys."""
    assert config.EXPECTED_STEPS.issubset(set(config.STEP_TIMEOUTS.keys()))


def test_expected_steps_excludes_skip_pattern():
    """No step matching SKIP_HOOKS_PATTERN should appear in EXPECTED_STEPS."""
    import re
    for step in config.EXPECTED_STEPS:
        assert not re.fullmatch(config.SKIP_HOOKS_PATTERN, step), (
            f"step '{step}' matches SKIP_HOOKS_PATTERN but is in EXPECTED_STEPS"
        )


def test_exit_code_labels_has_known_codes():
    assert 127 in config.EXIT_CODE_LABELS
    assert 124 in config.EXIT_CODE_LABELS
    assert 141 in config.EXIT_CODE_LABELS
