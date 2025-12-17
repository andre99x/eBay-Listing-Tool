"""ListGiant package initialization.

This file ensures the `app` directory is treated as a package on all platforms,
preventing import edge cases when running `uvicorn app.main:app --reload` on
Windows or Linux.
"""
