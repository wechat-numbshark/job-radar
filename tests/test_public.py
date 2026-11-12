from config.settings import load_profile
from processors.filter import _check_japan_salary
from processors.scorer import score_system
from processors.analyzer import analyst_system

def test_example_is_fictional_and_used_by_both_prompts():
    profile = load_profile()
    assert "虚构" in profile["name"]
    assert profile["name"] in score_system()
    assert profile["name"] in analyst_system()

def test_salary_threshold_handles_both_jpy_formats():
    assert _check_japan_salary("JP", "JPY 7,000,000", "")[0] is False
    assert _check_japan_salary("JP", "7,000,000 JPY", "")[0] is False
    assert _check_japan_salary("JP", "900万円", "")[0] is True
