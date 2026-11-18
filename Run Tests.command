#!/bin/zsh
cd -- "${0:A:h}"
python3 run_tests.py
read -r '?Press Enter to close...'
