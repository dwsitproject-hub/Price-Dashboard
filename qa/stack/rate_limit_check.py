"""The real sign-in rate limit (LOGIN_RATE_PER_MINUTE=10) on a freshly started, in-process API - the suites run with
the limit raised. Uses an unknown e-mail, so no account is touched or locked.

    backend/.venv/Scripts/python qa/stack/rate_limit_check.py
"""
import json
import os

import qa_env

qa_env.load()
os.environ["LOGIN_RATE_PER_MINUTE"] = "10"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

codes = []
with TestClient(app, base_url="http://testserver", headers={"Origin": "http://localhost:5181"}) as c:
    for i in range(12):
        r = c.post("/api/auth/login", json={"email": f"qa-rate-{i}@nowhere.invalid", "password": "wrong-password-1"})
        codes.append(r.status_code)
ok = codes[:10] == [401] * 10 and all(code == 429 for code in codes[10:])
print(json.dumps({"id": "PT-RATE-01", "codes": codes, "status": "SECURE" if ok else "VULNERABLE"}))
