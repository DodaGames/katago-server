#!/bin/zsh

cd "$(dirname "$0")/.."
source .venv/bin/activate
cd src
locust -f ../tests/load/locust/mixed.py --host=http://0.0.0.0:8000