#!/bin/bash
echo "Starting the Job Market Intelligence Loop...."

while true; do
    # Pulls and filters new jobs from RSS
    python3 pull_jobs.py

    # Syncs the latest batch to desc.json
    python3 pull_desc.py

    # Wait for the next 60-second cycle
    sleep 60
done

echo "Job Market Intelligence Loop Ended..."