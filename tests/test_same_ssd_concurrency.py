from same_ssd_concurrency import choose_same_ssd_parallelism


def decide(current, mean, peak, **kwargs):
    return choose_same_ssd_parallelism(
        current=current,
        available_workers=16,
        pending_candidates=16,
        mean_util_pct=mean,
        peak_util_pct=peak,
        **kwargs,
    )


def test_missing_observation_holds_current_tier():
    result = decide(4, None, None)
    assert result["desired"] == 4
    assert result["reason"] == "no_valid_ssd_observation_hold"


def test_headroom_scales_only_one_tier_and_defaults_to_eight():
    assert decide(1, 9.7, 16.4)["desired"] == 4
    assert decide(4, 37.26, 64.0)["desired"] == 8
    assert decide(8, 59.74, 81.6)["desired"] == 8


def test_exclusive_opt_in_allows_twelve_but_never_sixteen():
    result = decide(8, 59.74, 81.6, allow_stable_cap=True)
    assert result["desired"] == 12
    assert result["stable_cap"] == 12


def test_warning_scales_down_one_tier():
    result = decide(12, 85.0, 95.0, allow_stable_cap=True)
    assert result["desired"] == 8
    assert result["reason"] == "ssd_warning_scale_down_one_tier"


def test_effective_is_bounded_by_fixed_top_five():
    result = choose_same_ssd_parallelism(
        current=8,
        available_workers=12,
        pending_candidates=5,
        mean_util_pct=40.0,
        peak_util_pct=60.0,
    )
    assert result["effective"] == 5
