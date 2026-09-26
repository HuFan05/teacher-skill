from __future__ import annotations


THEOREM_ROLES = frozenset({"theorem", "lemma", "proposition", "corollary"})
DEFINITION_ROLES = frozenset({"definition"})
STATEMENT_ROLES = THEOREM_ROLES | DEFINITION_ROLES
ALGORITHM_ROLES = frozenset({"algorithm"})
RESULT_ROLES = frozenset({"results_table"})
FIGURE_ROLES = frozenset({"figure_caption"})
EQUATION_ROLES = frozenset({"method", "algorithm"}) | STATEMENT_ROLES
METHOD_ROLES = frozenset({"method", "algorithm", "figure_caption", "definition"})
CLAIM_ROLES = frozenset({"abstract", "body", "method", "results_table", "figure_caption"}) | THEOREM_ROLES
# Pages that may copy or mention a target but never state it originally.
DOWNGRADED_ROLES = frozenset({"proof", "related_work", "references", "contents", "abstract"})
NEVER_EXACT_ROLES = frozenset({"proof", "related_work", "references", "contents", "other", "unknown"})
LOCAL_WINDOW_ROLES = STATEMENT_ROLES | ALGORITHM_ROLES | RESULT_ROLES | FIGURE_ROLES | {"method"}
