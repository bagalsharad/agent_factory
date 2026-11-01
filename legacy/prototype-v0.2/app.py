#!/usr/bin/env python3
"""Run the local Agent Factory with: python3 app.py"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8787, reload=False)
