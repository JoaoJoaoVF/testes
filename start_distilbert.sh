#!/bin/bash
cd "$(dirname "$0")"
source venv/bin/activate
cd DistilBERT
python3 realtime_network_monitor.py --interactive
