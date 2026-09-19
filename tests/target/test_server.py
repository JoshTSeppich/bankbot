import httpx

from bankbot.target import create_app, start_server


def test_start_server_serves_the_login_page_on_a_free_port_and_stops() -> None:
    handle = start_server(create_app(), port=0)
    try:
        assert handle.base_url.startswith("http://127.0.0.1:")
        response = httpx.get(f"{handle.base_url}/login")
        assert response.status_code == 200
        assert "<title>Legacy Core Teller</title>" in response.text
    finally:
        handle.stop()
    try:
        httpx.get(f"{handle.base_url}/login", timeout=1.0)
    except httpx.ConnectError:
        return
    raise AssertionError("server still accepting connections after stop()")


def test_two_servers_can_run_side_by_side_on_different_ports() -> None:
    first = start_server(create_app(), port=0)
    second = start_server(create_app(), port=0)
    try:
        assert first.base_url != second.base_url
        assert httpx.get(f"{second.base_url}/login").status_code == 200
    finally:
        first.stop()
        second.stop()
