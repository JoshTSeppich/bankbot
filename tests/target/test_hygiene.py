import re
from pathlib import Path

from fastapi.testclient import TestClient

from tests.target.conftest import sign_in

TARGET_DIR = Path(__file__).resolve().parents[2] / "bankbot" / "target"
TESTS_DIR = Path(__file__).resolve().parent

ID_OR_TEST_ID = re.compile(r"\s(?:id|data-testid)=")
NINE_OR_MORE_DIGITS = re.compile(r"\d{9,}")


def every_page(client: TestClient) -> dict[str, str]:
    """Every distinct HTML page the app can render, for both variants, with the dialog armed."""
    pages: dict[str, str] = {}
    for prefix in ["", "/b"]:
        pages[f"{prefix}/login"] = client.get(f"{prefix}/login").text
        pages[f"{prefix}/login (error)"] = client.post(
            f"{prefix}/login", data={"username": "x", "password": "y"}
        ).text
        sign_in(client, prefix)
        client.post("/admin/faults", json={"unknown_dialog_at_step": 1})
        pages[f"{prefix}/members/search"] = client.get(f"{prefix}/members/search").text
        pages[f"{prefix}/members/search-form"] = client.get(f"{prefix}/members/search-form").text
        for member_id in ["M-100", "M-101", "M-102", "M-103", "M-999"]:
            pages[f"{prefix}/members/results {member_id}"] = client.post(
                f"{prefix}/members/results", data={"member_id": member_id}
            ).text
            pages[f"{prefix}/members/{member_id}"] = client.get(
                f"{prefix}/members/{member_id}"
            ).text
            pages[f"{prefix}/members/{member_id}/close"] = client.post(
                f"{prefix}/members/{member_id}/close"
            ).text
    assert any('role="dialog"' in html for html in pages.values())
    return pages


def test_no_element_carries_an_id_or_test_id_attribute(client: TestClient) -> None:
    for name, html in every_page(client).items():
        assert not ID_OR_TEST_ID.search(html), name


def test_no_nine_or_sixteen_digit_numbers_appear_in_any_page(client: TestClient) -> None:
    for name, html in every_page(client).items():
        assert not NINE_OR_MORE_DIGITS.search(html), name


def test_no_nine_or_sixteen_digit_numbers_appear_in_the_target_source_or_its_tests() -> None:
    for path in [*TARGET_DIR.rglob("*.py"), *TARGET_DIR.rglob("*.html"), *TESTS_DIR.rglob("*.py")]:
        assert not NINE_OR_MORE_DIGITS.search(path.read_text()), path


def test_every_page_carries_the_title_and_the_application_version_meta(
    client: TestClient,
) -> None:
    for name, html in every_page(client).items():
        assert "<title>Legacy Core Teller</title>" in html, name
        expected = "7.3.0" if name.startswith("/b") else "7.2.1"
        assert f'<meta name="application-version" content="{expected}">' in html, name


def test_every_page_lays_itself_out_with_tables(client: TestClient) -> None:
    for name, html in every_page(client).items():
        assert "<table" in html, name
