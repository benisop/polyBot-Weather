#!/bin/bash
# Run PolyBot dashboard

source venv/bin/activate 2>/dev/null || true
streamlit run polybot/dashboard/app.py --server.port 8501
