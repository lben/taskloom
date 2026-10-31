"""Taskloom: build automations from connected blocks."""

__version__ = "0.25.2"

from .block import Block, fields, ports
from .table import Table

__all__ = ["Block", "Table", "fields", "ports"]
