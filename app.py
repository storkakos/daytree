"""Entry point for gunicorn ("app:app"). The code lives in the daytree package, split by area."""
from daytree.web import app, start

start()
