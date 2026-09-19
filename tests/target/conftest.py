from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from bankbot.target import Faults, create_app

USERNAME = "teller"
PASSWORD = "teller-demo-password"


def sign_in(client: TestClient, prefix: str = "") -> None:
    response = client.post(f"{prefix}/login", data={"username": USERNAME, "password": PASSWORD})
    assert response.status_code == 303


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app(Faults()), follow_redirects=False) as client:
        yield client


@pytest.fixture
def signed_in(client: TestClient) -> TestClient:
    sign_in(client)
    return client
