from hooks_report.config import (
    MIN_CC_VERSION,
    RECOMMENDED_CC_VERSION,
    parse_cc_version,
)


class TestParseCcVersion:
    def test_full_version_string(self):
        assert parse_cc_version("Claude Code v2.1.85") == (2, 1, 85)

    def test_bare_version(self):
        assert parse_cc_version("2.1.50") == (2, 1, 50)

    def test_garbage_returns_none(self):
        assert parse_cc_version("garbage") is None

    def test_empty_returns_none(self):
        assert parse_cc_version("") is None

    def test_version_with_extra_text(self):
        assert parse_cc_version("v2.1.85 (beta)") == (2, 1, 85)

    def test_version_ordering(self):
        v2_1_49 = parse_cc_version("2.1.49")
        v2_1_50 = parse_cc_version("2.1.50")
        v2_1_85 = parse_cc_version("2.1.85")
        v2_1_86 = parse_cc_version("2.1.86")
        v2_1_99 = parse_cc_version("2.1.99")
        v2_2_0 = parse_cc_version("2.2.0")
        v2_9_99 = parse_cc_version("2.9.99")
        v3_0_0 = parse_cc_version("3.0.0")

        assert v2_1_49 is not None and v2_1_50 is not None
        assert v2_1_85 is not None and v2_1_86 is not None
        assert v2_1_99 is not None and v2_2_0 is not None
        assert v2_9_99 is not None and v3_0_0 is not None

        assert v2_1_49 < v2_1_50
        assert v2_1_50 == v2_1_50
        assert v2_1_86 > v2_1_85
        assert v2_2_0 > v2_1_99
        assert v3_0_0 > v2_9_99


class TestVersionConstants:
    def test_min_is_parseable(self):
        assert parse_cc_version(MIN_CC_VERSION) is not None

    def test_recommended_is_parseable(self):
        assert parse_cc_version(RECOMMENDED_CC_VERSION) is not None

    def test_recommended_gte_min(self):
        rec = parse_cc_version(RECOMMENDED_CC_VERSION)
        minimum = parse_cc_version(MIN_CC_VERSION)
        assert rec is not None and minimum is not None
        assert rec >= minimum

    def test_constants_match_version_requirements_file(self):
        """Python constants must stay in sync with version-requirements."""
        import os
        req_path = os.path.join(os.path.dirname(__file__), "..", "version-requirements")
        versions = {}
        with open(req_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    versions[k.strip()] = v.strip()
        assert MIN_CC_VERSION == versions["MIN_CC_VERSION"]
        assert RECOMMENDED_CC_VERSION == versions["RECOMMENDED_CC_VERSION"]
