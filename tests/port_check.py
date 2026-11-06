"""Checks against the real gunicorn process that requests through Cloudflare are public and others are internal.

Needs a running container with nothing shared through the tunnel (the default):

    docker exec -i daytree python - < tests/port_check.py

Prints "ALL OK" on success; any failed assertion exits non-zero.
"""
import http.client


def status(port, path="/", headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path, headers=headers or {})
    return conn.getresponse().status


def listening(port):
    try:
        status(port)
        return True
    except OSError:
        return False


assert status(8080) == 200
assert status(8080, headers={"Cf-Ray": "abc"}) == 404
assert status(8080, headers={"Cf-Connecting-Ip": "203.0.113.9"}) == 404
assert status(8080, "/settings", {"Cf-Ray": "abc", "Host": "localhost:8080"}) == 404   # Host header must not matter
assert status(8080, "/ping", {"Cf-Ray": "abc"}) == 200
assert not listening(8081)                                                          # the old public port is gone
print("ALL OK")
