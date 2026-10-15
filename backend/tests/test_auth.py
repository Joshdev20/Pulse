"""Authentication and profile tests."""


def test_register_login_and_me(client):
    response = client.post(
        "/api/auth/register",
        json={
            "username": "ada",
            "email": "ada@example.com",
            "display_name": "Ada Lovelace",
            "password": "password123",
        },
    )
    assert response.status_code == 201, response.text
    token = response.json()["access_token"]

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["username"] == "ada"
    assert me.json()["display_name"] == "Ada Lovelace"
    assert me.json()["post_count"] == 0

    login = client.post("/api/auth/login", json={"username": "ada", "password": "password123"})
    assert login.status_code == 200
    assert login.json()["access_token"]

    by_email = client.post("/api/auth/login", json={"username": "ada@example.com", "password": "password123"})
    assert by_email.status_code == 200


def test_duplicate_username_is_rejected(client, make_user):
    make_user("ada")
    response = client.post(
        "/api/auth/register",
        json={
            "username": "ada",
            "email": "other@example.com",
            "display_name": "Someone",
            "password": "password123",
        },
    )
    assert response.status_code == 409


def test_wrong_password_is_rejected(client, make_user):
    make_user("ada")
    response = client.post("/api/auth/login", json={"username": "ada", "password": "nope"})
    assert response.status_code == 401


def test_me_requires_token(client):
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_profile_update(client, make_user):
    headers, _ = make_user("ada")
    response = client.patch(
        "/api/users/ada",
        headers=headers,
        json={"bio": "Counting on machines.", "location": "London"},
    )
    assert response.status_code == 200
    assert response.json()["bio"] == "Counting on machines."
    assert response.json()["location"] == "London"


def test_cannot_update_another_profile(client, make_user):
    _, _ = make_user("ada")
    headers, _ = make_user("grace")
    response = client.patch("/api/users/ada", headers=headers, json={"bio": "hacked"})
    assert response.status_code == 403


def test_unknown_user_returns_404(client):
    assert client.get("/api/users/ghost").status_code == 404
