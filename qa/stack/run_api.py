"""Run the API against the QA database on 127.0.0.1:8710 (where the Vite dev server proxies /api).

Started through .claude/launch.json ("aastar-qa-api"); stop the development API first (same port).
"""
from __future__ import annotations

import qa_env

qa_env.load()

import uvicorn  # noqa: E402

uvicorn.run("app.main:app", host="127.0.0.1", port=8710, proxy_headers=True, forwarded_allow_ips="127.0.0.1")
