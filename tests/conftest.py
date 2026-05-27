"""Shared pytest fixtures for almc-shield tests."""
import os
import sys
from pathlib import Path

# Ensure package is importable from project root in tests
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
