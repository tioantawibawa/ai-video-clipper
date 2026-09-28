from urllib.parse import parse_qs, urlsplit

from clipper.oauth import CALLBACK, OAuth


def call(app, path, query="", cookie=""):
    response = []
    body = app({"REQUEST_METHOD": "GET", "PATH_INFO": path, "QUERY_STRING": query,
                "HTTP_COOKIE": cookie}, lambda code, headers: response.extend([code, dict(headers)]))
    return response[0], response[1], b"".join(body)


def test_ticket_one_use_cookie_binding_replay(tmp_path):
    app = OAuth(tmp_path, "https://example.org", "key", "secret")
    ticket = app.issue("main")
    code, headers, _ = call(app, "/connect", "ticket=" + ticket)
    assert code == "302 Found"
    assert call(app, "/connect", "ticket=" + ticket)[0] == "403 Forbidden"
    state = parse_qs(urlsplit(headers["Location"]).query)["state"][0]
    query = "state=" + state + "&code=test-code"
    assert call(app, CALLBACK, query)[0] == "403 Forbidden"
    app.exchange = lambda **kw: dict(access_token="token-secret-value", refresh_token="refresh-secret-value", open_id="id", scope="user.info.basic,video.upload")
    cookie = "__Host-clipper_state=" + state
    code, _, body = call(app, CALLBACK, query, cookie)
    assert code == "200 OK" and b"token-secret-value" not in body
    assert call(app, CALLBACK, query, cookie)[0] == "403 Forbidden"


def test_missing_config_expired_ticket_and_error_redaction(tmp_path):
    app = OAuth(tmp_path, "https://example.org")
    assert call(app, "/healthz")[0] == "200 OK"
    assert call(app, CALLBACK)[0] == "503 Service Unavailable"
    app.key = app.secret = "test"
    token = app.issue("main")
    with app.connect() as db:
        db.execute("UPDATE grants SET expires=0")
    assert call(app, "/connect", "ticket=" + token)[0] == "403 Forbidden"
    state = app.issue("main", "state")
    def fail(**kw):
        raise ValueError("secret-code")
    app.exchange = fail
    code, _, body = call(app, CALLBACK, "state=" + state + "&code=secret-code", "__Host-clipper_state=" + state)
    assert code == "502 Bad Gateway" and b"secret-code" not in body
