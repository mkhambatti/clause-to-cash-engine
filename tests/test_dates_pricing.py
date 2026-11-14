import datetime as dt

from clause_to_cash.dates import add_months, lifecycle, notice_deadline, prev_business_day, term_end, current_period
from clause_to_cash.pricing import annual_cost, cost, escalation_multiplier
from clause_to_cash.schema import Duration, Pricing, Terms, Tier


def test_month_end_clamping():
    assert add_months(dt.date(2024, 1, 31), 1) == dt.date(2024, 2, 29)
    assert add_months(dt.date(2023, 8, 31), 6) == dt.date(2024, 2, 29)
    assert add_months(dt.date(2024, 3, 15), -3) == dt.date(2023, 12, 15)


def test_term_end_is_day_before_anniversary():
    assert term_end(dt.date(2024, 3, 1), 36) == dt.date(2027, 2, 28)
    assert term_end(dt.date(2024, 1, 1), 12) == dt.date(2024, 12, 31)


def test_business_day_rolls_back_over_weekend():
    sat = dt.date(2026, 11, 14)
    assert sat.weekday() == 5 and prev_business_day(sat) == dt.date(2026, 11, 13)
    assert prev_business_day(dt.date(2026, 11, 15)) == dt.date(2026, 11, 13)


def _t(**kw):
    base = dict(effective_date=dt.date(2024, 1, 1), initial_term_months=24, auto_renew=True, renewal_term_months=12,
                nonrenewal_notice=Duration(amount=90, unit="days"))
    base.update(kw)
    return Terms(**base)


def test_notice_deadline_days_vs_months_differ():
    p = current_period(_t(), dt.date(2025, 6, 1))
    assert p and p.end == dt.date(2025, 12, 31)
    d_days = notice_deadline(_t(), p)
    d_months = notice_deadline(_t(nonrenewal_notice=Duration(amount=3, unit="months")), p)
    assert d_days == dt.date(2025, 10, 2)  # Dec 31 - 90d = Oct 2 (Thu)
    assert d_months == dt.date(2025, 9, 30)  # 3 calendar months back, clamped
    assert d_days != d_months


def test_lifecycle_open_locked_fixed_expired():
    t = _t()
    assert lifecycle(t, dt.date(2025, 6, 1)).status == "open"
    lk = lifecycle(t, dt.date(2025, 11, 1))
    assert lk.status == "locked" and lk.natural_exit == dt.date(2026, 12, 31)
    assert lifecycle(_t(auto_renew=False, renewal_term_months=None, nonrenewal_notice=None), dt.date(2025, 6, 1)).status == "fixed_term"
    assert lifecycle(_t(auto_renew=False, renewal_term_months=None, nonrenewal_notice=None), dt.date(2030, 1, 1)).status == "expired"
    assert lifecycle(Terms(), dt.date(2025, 1, 1)).status == "unknown"


def test_deadline_day_itself_is_still_open():
    t = _t()
    p = current_period(t, dt.date(2025, 6, 1))
    dl = notice_deadline(t, p)
    assert lifecycle(t, dl).status == "open"
    assert lifecycle(t, dl + dt.timedelta(days=1)).status == "locked"


GRAD = Pricing(unit="u", tier_mode="graduated", tiers=[Tier(lower=0, upper=100, unit_price=10), Tier(lower=100, upper=None, unit_price=8)])
ALL = Pricing(unit="u", tier_mode="all_units", tiers=[Tier(lower=0, upper=100, unit_price=10), Tier(lower=100, upper=None, unit_price=8)])


def test_graduated_vs_all_units():
    assert cost(GRAD, 150) == 100 * 10 + 50 * 8
    assert cost(ALL, 150) == 150 * 8
    assert cost(GRAD, 0) == 0


def test_all_units_cliff_cost_can_drop():
    assert cost(ALL, 99) == 990 and cost(ALL, 100) == 800  # buying MORE is cheaper
    assert cost(GRAD, 99) < cost(GRAD, 100)


def test_floor_and_escalator():
    t = Terms(pricing=GRAD, min_annual_commit_usd=5000.0, effective_date=dt.date(2020, 1, 1), annual_escalator_pct=3.0)
    assert annual_cost(t, 10) == 5000.0  # take-or-pay floor
    assert annual_cost(t, 10, floor_commit=False) == 100.0
    assert escalation_multiplier(t, dt.date(2023, 6, 1)) == (1.03) ** 3
