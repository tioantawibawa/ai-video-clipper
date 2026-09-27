#!/bin/sh
# Run inside Ubuntu with Python 3.11, or the project's Docker build environment.
set -eu
python -m pip install pip-tools==7.5.0
python -m piptools compile --generate-hashes --resolver=backtracking \
  --output-file=requirements.lock requirements.txt
echo 'Install with: pip install --require-hashes -r requirements.lock'
