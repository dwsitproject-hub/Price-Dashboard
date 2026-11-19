"""Create (or re-enable) a throwaway QA admin for browser checks, via the API as the bootstrap admin.

    backend/.venv/Scripts/python qa/stack/ui_user.py <email> <password>      # create
    backend/.venv/Scripts/python qa/stack/ui_user.py <email> --disable       # disable afterwards
"""
import os
import sys

import qa_env

qa_env.load()
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

email, arg = sys.argv[1], sys.argv[2]
with TestClient(app, base_url="http://testserver", headers={"Origin": "http://localhost:5181"}) as c:
    r = c.post("/api/auth/login", json={"email": os.environ["BOOTSTRAP_ADMIN_EMAIL"],
                                        "password": os.environ["BOOTSTRAP_ADMIN_PASSWORD"]})
    assert r.status_code == 200, r.text
    c.headers["X-CSRF-Token"] = c.cookies.get("aastar_csrf")
    users = c.get("/api/admin/users", params={"q": email}).json()["items"]
    if arg == "--disable":
        for u in users:
            print(c.patch(f"/api/admin/users/{u['id']}", json={"status": "disabled"}).status_code)
    else:
        r = c.post("/api/admin/users", json={"email": email, "display_name": "QA UI Admin", "role": "admin",
                                             "auth_provider": "local", "password": arg})
        print("created", r.status_code, r.json().get("error", {}).get("code", "ok"))
