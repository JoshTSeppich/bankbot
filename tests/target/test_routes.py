import re

import pytest
from fastapi.testclient import TestClient

from bankbot.target import create_app
from tests.target.conftest import sign_in


def test_root_redirects_to_login_when_not_signed_in(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_root_redirects_to_member_search_when_signed_in(signed_in: TestClient) -> None:
    response = signed_in.get("/")
    assert response.status_code == 303
    assert response.headers["location"] == "/members/search"


def test_member_routes_redirect_to_login_when_not_signed_in(client: TestClient) -> None:
    for method, path in [
        ("GET", "/members/search"),
        ("GET", "/members/search-form"),
        ("GET", "/members/results?member_id=M-100"),
        ("POST", "/members/results"),
        ("GET", "/members/M-100"),
        ("POST", "/members/M-100/close"),
    ]:
        response = client.request(method, path, data={"member_id": "M-100"})
        assert response.status_code == 303, path
        assert response.headers["location"] == "/login", path


def test_login_page_has_labelled_username_and_password_fields_and_a_sign_in_button(
    client: TestClient,
) -> None:
    html = client.get("/login").text
    assert re.search(r"<label>Username\s*<input type=\"text\" name=\"username\"", html)
    assert re.search(r"<label>Password\s*<input type=\"password\" name=\"password\"", html)
    assert re.search(r"<button type=\"submit\">Sign in</button>", html)


def test_wrong_credentials_re_render_the_login_form_with_the_error_text(
    client: TestClient,
) -> None:
    response = client.post("/login", data={"username": "teller", "password": "nope"})
    assert response.status_code == 401
    assert "Invalid username or password" in response.text
    assert "Sign in" in response.text
    assert client.get("/members/search").status_code == 303


def test_successful_login_sets_a_cookie_and_redirects_to_member_search(
    client: TestClient,
) -> None:
    response = client.post(
        "/login", data={"username": "teller", "password": "teller-demo-password"}
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/members/search"
    assert "teller_session" in response.cookies
    assert client.get("/members/search").status_code == 200


def test_search_page_shows_who_is_signed_in(signed_in: TestClient) -> None:
    assert "Signed in as teller" in signed_in.get("/members/search").text


def test_search_form_lives_in_an_iframe_named_main(signed_in: TestClient) -> None:
    html = signed_in.get("/members/search").text
    assert '<iframe name="main" src="/members/search-form"' in html
    assert "<form" not in html


def test_search_form_is_table_laid_out_and_posts_to_the_top_window(
    signed_in: TestClient,
) -> None:
    html = signed_in.get("/members/search-form").text
    assert '<form method="post" action="/members/results" target="_top">' in html
    assert re.search(r"<table>\s*<tr>\s*<td><label>Member ID\s*<input", html)
    assert re.search(r'<input type="text" name="member_id"', html)
    assert '<button type="submit">Search</button>' in html


def test_search_by_post_renders_a_results_table_linking_to_the_member(
    signed_in: TestClient,
) -> None:
    html = signed_in.post("/members/results", data={"member_id": "M-100"}).text
    assert '<table class="results">' in html
    assert re.search(r"<th>Member ID</th>\s*<th>Name</th>\s*<th>Branch</th>\s*</tr>", html)
    assert '<a href="/members/M-100">Dana Whitfield</a>' in html
    assert "<th>Status</th>" not in html


def test_search_by_query_string_renders_the_same_results(signed_in: TestClient) -> None:
    html = signed_in.get("/members/results", params={"member_id": "M-101"}).text
    assert '<a href="/members/M-101">Priya Nandakumar</a>' in html


def test_unknown_member_renders_no_member_found_text_and_no_table(
    signed_in: TestClient,
) -> None:
    html = signed_in.post("/members/results", data={"member_id": "M-999"}).text
    assert "No member found for M-999" in html
    assert '<table class="results"' not in html


def test_member_detail_shows_the_name_heading_and_the_savings_balance_beside_its_row_header(
    signed_in: TestClient,
) -> None:
    html = signed_in.get("/members/M-100").text
    assert "<h1>Dana Whitfield</h1>" in html
    assert re.search(r'<th scope="row">Savings balance</th><td>\$4,242\.00</td>', html)
    assert re.search(r'<th scope="row">Checking balance</th><td>\$318\.55</td>', html)


def test_member_detail_has_a_close_account_button_posting_to_the_close_route(
    signed_in: TestClient,
) -> None:
    html = signed_in.get("/members/M-100").text
    assert '<form method="post" action="/members/M-100/close">' in html
    assert '<button type="submit">Close account</button>' in html


def test_closing_an_account_renders_account_closed_and_changes_nothing(
    signed_in: TestClient,
) -> None:
    assert "Account closed" in signed_in.post("/members/M-100/close").text
    assert "$4,242.00" in signed_in.get("/members/M-100").text


def test_detail_of_an_unknown_member_is_a_404_with_the_no_member_found_text(
    signed_in: TestClient,
) -> None:
    response = signed_in.get("/members/M-999")
    assert response.status_code == 404
    assert "No member found for M-999" in response.text


def test_every_seeded_member_is_reachable_and_m_999_is_not(signed_in: TestClient) -> None:
    for member_id in ["M-100", "M-101", "M-102", "M-103"]:
        assert signed_in.get(f"/members/{member_id}").status_code == 200
    assert signed_in.get("/members/M-999").status_code == 404


def test_credentials_come_from_the_environment_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BANKBOT_USERNAME", "auditor")
    monkeypatch.setenv("BANKBOT_PASSWORD", "auditor-demo-password")
    with TestClient(create_app(), follow_redirects=False) as client:
        denied = client.post(
            "/login", data={"username": "teller", "password": "teller-demo-password"}
        )
        assert denied.status_code == 401
        allowed = client.post(
            "/login", data={"username": "auditor", "password": "auditor-demo-password"}
        )
        assert allowed.status_code == 303
        assert "Signed in as auditor" in client.get("/members/search").text


def test_sign_in_helper_matches_the_default_credentials(client: TestClient) -> None:
    sign_in(client)
    assert client.get("/members/search").status_code == 200
