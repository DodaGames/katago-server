#!/bin/zsh

source .venv/bin/activate
locust -f test/locust/mixed.py --host=http://0.0.0.0:8000