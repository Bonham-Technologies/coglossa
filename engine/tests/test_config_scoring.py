import pytest
import yaml

from coglossa.config import ConfigError, ScoringConfig, load_config, validate_config
from coglossa.schema import ErrorSeverity
from tests.conftest import BASE_CONFIG


def _write(tmp_path, data):
    path = tmp_path / "coglossa.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_scoring_defaults(make_config):
    cfg = make_config()
    assert cfg.scorer is None
    s = cfg.scoring
    assert s.thresholds.meaning == {"A": 86.0, "B": 84.0, "C": 70.0}  # calibrated, October 2026
    assert s.thresholds.style == 100.0
    assert s.weights_by_tier["C"].judge == 0.40
    assert s.mqm_penalties[ErrorSeverity.CRITICAL] == 10.0
    assert s.length_ratio_band == (0.5, 2.0)
    assert (s.xsim_floor, s.judge_penalty_scale, s.literary_roundtrip_factor) == (0.5, 10.0, 0.5)
    assert (s.qe_max_chars, s.sim_max_chars) == (1500, 800)


def test_partial_overrides_merge_over_defaults():
    s = ScoringConfig.model_validate(
        {
            "thresholds": {"meaning": {"C": 80}},
            "weights_by_tier": {
                "A": {"qe": 1, "xsim": 0, "roundtrip": 0, "judge": 0, "agreement": 0}
            },
        }
    )
    assert s.thresholds.meaning == {"A": 86.0, "B": 84.0, "C": 80.0}
    assert s.weights_by_tier["A"].qe == 1 and s.weights_by_tier["B"].qe == 0.30


def test_scorer_section(make_config):
    cfg = make_config(scorer={"url": "http://127.0.0.1:8080"})
    assert (cfg.scorer.kind, cfg.scorer.max_batch, cfg.scorer.timeout_s) == ("http", 64, 300.0)
    assert (cfg.scorer.max_concurrency, cfg.scorer.connect_timeout_s) == (2, 10.0)
    assert validate_config(cfg) == []


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"scorer": {"kind": "http"}}, "scorer.url is required"),
        (
            {
                "scoring": {
                    "weights_by_tier": {
                        "D": {"qe": 1, "xsim": 0, "roundtrip": 0, "judge": 0, "agreement": 0}
                    }
                }
            },
            "unknown tier 'D'",
        ),
        (
            {
                "scoring": {
                    "weights_by_tier": {
                        "A": {"qe": 0, "xsim": 0, "roundtrip": 0, "judge": 0, "agreement": 0}
                    }
                }
            },
            "weights for tier A sum to 0",
        ),
        ({"scoring": {"thresholds": {"meaning": {"Z": 50}}}}, "unknown tier 'Z'"),
        ({"scoring": {"thresholds": {"meaning": {"A": 150}}}}, "between 0 and 100"),
        ({"scoring": {"length_ratio_band": [2.0, 0.5]}}, "length_ratio_band"),
        ({"scoring": {"length_ratio_band": [0, 2.0]}}, "length_ratio_band"),
        ({"scoring": {"mqm_penalties": {"minor": -1}}}, "mqm_penalties"),
        ({"scoring": {"language_tiers_file": "/nope/tiers.yaml"}}, "language tiers file"),
    ],
)
def test_scoring_validation_errors(make_config, overrides, message):
    issues = validate_config(make_config(**overrides))
    assert any(message in i.message and i.level == "error" for i in issues), issues


def test_scorer_kind_none_needs_no_url(make_config):
    assert validate_config(make_config(scorer={"kind": "none"})) == []


def test_load_config_resolves_relative_tiers_file(tmp_path):
    (tmp_path / "my-tiers.yaml").write_text("default: B\n", encoding="utf-8")
    cfg, _ = load_config(
        _write(tmp_path, {**BASE_CONFIG, "scoring": {"language_tiers_file": "my-tiers.yaml"}})
    )
    assert cfg.scoring.language_tiers_file == tmp_path / "my-tiers.yaml"


def test_load_config_missing_tiers_file_is_error(tmp_path):
    with pytest.raises(ConfigError, match="language tiers file"):
        load_config(
            _write(tmp_path, {**BASE_CONFIG, "scoring": {"language_tiers_file": "absent.yaml"}})
        )


@pytest.mark.parametrize("bad", [{"max_concurrency": 0}, {"connect_timeout_s": 0}])
def test_scorer_limits_are_validated(make_config, bad):
    with pytest.raises(ValueError):
        make_config(scorer={"url": "http://127.0.0.1:8080", **bad})


def test_uniform_meaning_threshold():
    s = ScoringConfig.model_validate({"thresholds": {"meaning": 80, "style": 55}})
    assert s.thresholds.meaning == {"A": 80.0, "B": 80.0, "C": 80.0}
    assert s.thresholds.style == 55.0


def test_uniform_meaning_threshold_is_range_checked(make_config):
    cfg = make_config(scoring={"thresholds": {"meaning": 120}})
    assert any("thresholds.meaning.A" in i.message for i in validate_config(cfg))


def test_null_scoring_and_scorer_sections_mean_defaults(tmp_path):
    data = {**BASE_CONFIG, "scoring": None, "scorer": None}
    cfg, _ = load_config(_write(tmp_path, data))
    assert cfg.scorer is None
    assert cfg.scoring == ScoringConfig()
