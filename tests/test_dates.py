from career_copilot.dates import find_date_range


def test_year_range_and_open_end():
    found = find_date_range("Engineer | Acme Jan 2020 – Present")
    assert found is not None
    assert found.start.year == 2020
    assert found.start.month == 1
    assert found.start.day is None
    assert found.end is None
    assert found.start.raw == "Jan 2020"


def test_numeric_months_and_hyphen():
    found = find_date_range("01/2020 - 03/2022")
    assert found is not None
    assert (found.start.year, found.start.month) == (2020, 1)
    assert (found.end.year, found.end.month) == (2022, 3)


def test_portuguese_open_range():
    found = find_date_range("Investigador janeiro 2019 até atual")
    assert found is not None
    assert found.start.month == 1
    assert found.start.year == 2019
    assert found.end is None


def test_day_month_year():
    found = find_date_range("15 Mar 2020 – 2 Apr 2021")
    assert found is not None
    assert (found.start.day, found.start.month, found.start.year) == (15, 3, 2020)
    assert (found.end.day, found.end.month, found.end.year) == (2, 4, 2021)


def test_last_range_on_the_line_wins():
    found = find_date_range("Earlier 2010 - 2011 and later 2020 - 2022")
    assert found is not None
    assert found.start.year == 2020
    assert found.end is not None
    assert found.end.year == 2022


def test_page_ranges_and_citation_years_are_not_role_dates():
    assert find_date_range("pages 12-13") is None
    assert find_date_range("7(1) (2024): 213-224") is None
    assert find_date_range("CIDOC-CRM") is None
    assert find_date_range("monuments dated 3000-2500 BCE") is None
    assert find_date_range("range 1000-4000") is None
