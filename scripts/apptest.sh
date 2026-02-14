#!/bin/zsh

source .venv/bin/activate
locust -f locustfile.py --host=http://0.0.0.0:8000