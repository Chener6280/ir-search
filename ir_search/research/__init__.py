"""Compatibility-only legacy workflow; feature expansion is paused.

New integrations compose get_data, search_materials and retrieve from ir_search.
"""

from .orchestrator import deep_research
from .schemas import ResearchRun

__all__ = ["ResearchRun", "deep_research"]
