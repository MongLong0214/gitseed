"""Deterministic security screening.

This layer owns the malicious verdict. It runs before any model is consulted and
never calls one, because the cost of a false negative and the cost of a false
positive are not symmetric and a model that has not been verified against a
known-clean sample cannot be trusted with either (ADR-0002).
"""
