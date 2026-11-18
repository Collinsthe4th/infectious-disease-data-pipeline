"""Layer 4b - Validation and quarantine.

Implements the 12 rules of Table 3.4. Error-level failures go to
``ops.quarantine`` with the rule ID and reason; warning-level failures are
loaded and recorded in ``ops.data_quality_flag``.
"""
