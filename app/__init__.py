"""BRIMSEL Kids English Chatbot.

Deterministic tutor engine + thin LLM adapter.

Design rule that governs this package: **code decides, the LLM only speaks.**
Mode, level, answer correctness and progression are computed here in pure,
testable functions. The LLM receives exactly one directive per turn and is
never allowed to change the child's level, score or mode.
"""

__version__ = "1.0.0"
