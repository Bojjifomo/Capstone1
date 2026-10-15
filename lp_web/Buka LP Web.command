#!/bin/bash
# Double-click this in Finder to start LP Web and open it in your browser.
cd "$(dirname "$0")" || exit 1
exec python3 server.py
