import re

from fastapi.testclient import TestClient

from tests.target.conftest import sign_in


def test_variant_b_renames_the_search_button_and_adds_a_status_column(
    client: TestClient,
) -> None:
    sign_in(client, prefix="/b")

    form = client.get("/b/members/search-form").text
    assert '<button type="submit">Find member</button>' in form
    assert ">Search<" not in form
    assert '<form method="post" action="/b/members/results" target="_top">' in form

    results = client.post("/b/members/results", data={"member_id": "M-100"}).text
    assert re.search(
        r"<th>Member ID</th>\s*<th>Name</th>\s*<th>Branch</th>\s*<th>Status</th>", results
    )
    assert '<a href="/b/members/M-100">Dana Whitfield</a>' in results
    assert "<td>Active</td>" in results


def test_variant_b_reports_its_own_application_version(client: TestClient) -> None:
    assert '<meta name="application-version" content="7.3.0">' in client.get("/b/login").text
    assert '<meta name="application-version" content="7.2.1">' in client.get("/login").text


def test_variant_b_routes_stay_under_the_b_prefix(client: TestClient) -> None:
    assert client.get("/b/").headers["location"] == "/b/login"
    assert client.get("/b/members/search").headers["location"] == "/b/login"
    sign_in(client, prefix="/b")
    assert client.get("/b/").headers["location"] == "/b/members/search"
    search = client.get("/b/members/search").text
    assert '<iframe name="main" src="/b/members/search-form"' in search
    detail = client.get("/b/members/M-100").text
    assert '<form method="post" action="/b/members/M-100/close">' in detail


def test_a_session_opened_on_one_variant_is_valid_on_the_other(client: TestClient) -> None:
    sign_in(client)
    assert client.get("/b/members/search").status_code == 200
