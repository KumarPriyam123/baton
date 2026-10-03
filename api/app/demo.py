"""Demo-mode constants, shared by the API (GET /demo/users) and the seed.

The password is not a secret: it is shown on the login page when DEMO_MODE=true, and the API
refuses to start with DEMO_MODE in ENV=prod (app/config.py).
"""

DEMO_PASSWORD = "baton-demo"  # noqa: S105
