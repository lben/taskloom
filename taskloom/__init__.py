"""Taskloom: build automations from connected blocks."""

__version__ = "0.5.0"

from .block import Block, fields, ports
from .table import Table

__all__ = ["Block", "Table", "fields", "ports"]
