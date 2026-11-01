"""Checks against the real gunicorn process that port 8081 is public and port 8080 is internal.

Needs a running container with public mode "off" (the default):

    docker exec -i daytree python - < tests/port_check.py

Prints "ALL OK" on success; any failed assertion exits non-zero.
"""
import http.client


def status(port, path="/", headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path, headers=headers or {})
    return conn.getresponse().status


assert status(8080) == 200
assert status(8081) == 404
assert status(8081, headers={"Host": "calendar.example.com"}) == 404
assert status(8081, headers={"Host": "calendar.example.com:8080"}) == 404      # Host header must not matter
assert status(8081, "/settings", {"Host": "localhost:8080"}) == 404
assert status(8080, headers={"Cf-Ray": "abc"}) == 404                          # mis-pointed tunnel
print("ALL OK")
