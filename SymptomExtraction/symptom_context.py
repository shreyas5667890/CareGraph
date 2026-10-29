"""
Context detection for extracted patient symptoms.

Detects:
- Present symptoms
- Negated symptoms
- Uncertain symptoms
- Historical symptoms
- Resolved symptoms
- Symptoms belonging to someone other than the patient

This is a rule-based prototype, not a clinically validated system.
"""

import re


# Split sentences into clauses so that a negation does not
# automatically affect symptoms appearing after "but" or "however".
CLAUSE_BOUNDARIES = re.compile(
    r"\b(?:but|however|although|whereas|nevertheless|yet)\b|[;]",
    re.IGNORECASE,
)

SENTENCE_BOUNDARIES = re.compile(r"[.!?\n]+")


# Expressions indicating that a symptom is not present.
NEGATION_PATTERNS = [
    r"\bdo not have\b",
    r"\bdon't have\b",
    r"\bdoes not have\b",
    r"\bdoesn't have\b",
    r"\bdid not have\b",
    r"\bdidn't have\b",
    r"\bnot experiencing\b",
    r"\bnot suffering from\b",
    r"\bdeny having\b",
    r"\bdenies having\b",
    r"\bdenied having\b",
    r"\bwithout\b",
    r"\bno\b",
    r"\bnot\b",
    r"\bnever experienced\b",
    r"\bnever had\b",
]


# Expressions indicating uncertainty about a symptom.
UNCERTAINTY_PATTERNS = [
    r"\bmight have\b",
    r"\bmay have\b",
    r"\bcould have\b",
    r"\bpossibly\b",
    r"\bperhaps\b",
    r"\bmaybe\b",
    r"\bi think\b",
    r"\bi suspect\b",
    r"\bnot sure if\b",
    r"\bnot sure whether\b",
    r"\bunsure whether\b",
    r"\bunsure if\b",
    r"\bpossible\b",
    r"\bseem(?:s)? to have\b",
]


# Expressions indicating that a symptom has resolved.
RESOLVED_PATTERNS = [
    r"\bno longer have\b",
    r"\bno longer experiencing\b",
    r"\bhas gone away\b",
    r"\bhave gone away\b",
    r"\bwent away\b",
    r"\bhas resolved\b",
    r"\bhave resolved\b",
    r"\bresolved\b",
    r"\bdisappeared\b",
    r"\bsubsided\b",
    r"\bgot better\b",
    r"\bhas stopped\b",
    r"\bhave stopped\b",
]


# Expressions indicating a symptom occurred in the past.
HISTORICAL_PATTERNS = [
    r"\bused to have\b",
    r"\bused to experience\b",
    r"\bpreviously had\b",
    r"\bpreviously experienced\b",
    r"\bhad\b",
    r"\bexperienced\b",
    r"\bsuffered from\b",
    r"\bin the past\b",
    r"\blast week\b",
    r"\blast month\b",
    r"\byesterday\b",
    r"\ba few days ago\b",
]


# Common references to someone other than the patient.
OTHER_PERSON_PATTERNS = [
    r"\bmy mother\b",
    r"\bmy father\b",
    r"\bmy sister\b",
    r"\bmy brother\b",
    r"\bmy wife\b",
    r"\bmy husband\b",
    r"\bmy son\b",
    r"\bmy daughter\b",
    r"\bmy child\b",
    r"\bmy friend\b",
    r"\bmy colleague\b",
    r"\bmy grandmother\b",
    r"\bmy grandfather\b",
    r"\bmy parents\b",
    r"\bhe\b",
    r"\bshe\b",
    r"\bthey\b",
    r"\bhim\b",
    r"\bher\b",
    r"\bthem\b",
]


def _matches_any(patterns, text):
    """Return True if any regular expression matches the text."""
    return any(
        re.search(pattern, text, re.IGNORECASE)
        for pattern in patterns
    )


def _find_sentence(text, start, end):
    """
    Find the sentence containing the extracted symptom.

    start and end are character offsets into the original text.
    """
    for match in SENTENCE_BOUNDARIES.finditer(text):
        if match.start() <= start < match.end():
            continue

        if match.start() >= end:
            return text[:match.start()], text[match.end():]

    # The symptom is in the final sentence.
    return text, ""


def _find_clause(text, start, end):
    """
    Find the clause containing the extracted symptom.

    For example:
        I don't have a fever, but I have a headache.

    The clause for 'headache' should not inherit the negation of fever.
    """
    clauses = []
    last_position = 0

    for match in CLAUSE_BOUNDARIES.finditer(text):
        clauses.append(
            (last_position, match.start(), text[last_position:match.start()])
        )
        last_position = match.end()

    clauses.append((last_position, len(text), text[last_position:]))

    for clause_start, clause_end, clause_text in clauses:
        if clause_start <= start and end <= clause_end:
            return clause_text.strip()

    return text.strip()


def _other_person_mentioned_before_symptom(clause, symptom_text):
    """
    Conservatively detect an explicitly mentioned other person
    before the symptom.

    This does not perform full coreference resolution.
    """
    symptom_position = clause.lower().find(symptom_text.lower())

    if symptom_position == -1:
        symptom_position = len(clause)

    preceding_text = clause[:symptom_position]

    # Look for a person reference near the beginning of the clause.
    return _matches_any(OTHER_PERSON_PATTERNS, preceding_text)


def classify_symptom_context(
    text,
    start,
    end,
    symptom_text=None,
):
    """
    Classify the context of one extracted symptom.

    Parameters
    ----------
    text : str
        Original patient text.

    start : int
        Start character offset of the extracted symptom.

    end : int
        End character offset of the extracted symptom.

    symptom_text : str, optional
        Exact symptom phrase. If omitted, it is obtained from text[start:end].

    Returns
    -------
    dict
        Context status, experiencer, and evidence.
    """
    if not isinstance(text, str):
        raise TypeError("text must be a string")

    if not (0 <= start < end <= len(text)):
        raise ValueError("Invalid symptom character offsets")

    if symptom_text is None:
        symptom_text = text[start:end]

    # Identify the sentence containing the symptom using its boundaries.
    left_boundary = max(
        (m.end() for m in SENTENCE_BOUNDARIES.finditer(text[:start])),
        default=0,
    )
    right_match = SENTENCE_BOUNDARIES.search(text, end)
    right_boundary = right_match.start() if right_match else len(text)

    sentence = text[left_boundary:right_boundary].strip()

    # Calculate the symptom's offsets inside the sentence.
    local_start = start - left_boundary
    local_end = end - left_boundary

    clause = _find_clause(sentence, local_start, local_end)

    # Context rules work on the clause containing the symptom.
    clause_lower = clause.lower()
    symptom_position = clause_lower.find(symptom_text.lower())

    if symptom_position == -1:
        symptom_position = len(clause)

    # The nearby preceding text is particularly important for negation
    # and uncertainty because those expressions generally precede symptoms.
    preceding_text = clause[:symptom_position]
    normalized_preceding = preceding_text.lower()

    # A person other than the patient is not the patient's symptom.
    if _other_person_mentioned_before_symptom(clause, symptom_text):
        status = "other_person"
        experiencer = "other"
        evidence = "Another person is mentioned before the symptom."

    # Resolved symptoms take precedence over ordinary negation.
    elif _matches_any(RESOLVED_PATTERNS, preceding_text):
        status = "resolved"
        experiencer = "patient"
        evidence = "A resolution expression appears before the symptom."

    # A symptom explicitly denied in the same clause.
    elif _matches_any(NEGATION_PATTERNS, preceding_text):
        status = "negated"
        experiencer = "patient"
        evidence = "A negation expression appears before the symptom."

    # Uncertain symptom presence.
    elif _matches_any(UNCERTAINTY_PATTERNS, preceding_text):
        status = "uncertain"
        experiencer = "patient"
        evidence = "An uncertainty expression appears before the symptom."

    # Historical symptoms.
    elif _matches_any(HISTORICAL_PATTERNS, clause_lower):
        # Do not label a symptom historical merely because the phrase
        # "had" appears elsewhere in the clause.
        historical_prefix = (
            r"\b(?:i|we|he|she|they|patient)\s+"
            r"(?:had|experienced|suffered from|previously had|"
            r"previously experienced|used to have|used to experience)\b"
        )

        if re.search(historical_prefix, preceding_text, re.IGNORECASE) or (
            re.search(
                r"\b(?:yesterday|last week|last month|in the past|"
                r"a few days ago)\b",
                clause_lower,
            )
        ):
            status = "historical"
            experiencer = "patient"
            evidence = "A historical expression appears in the clause."
        else:
            status = "present"
            experiencer = "patient"
            evidence = "No recognized context modifier was found."

    else:
        status = "present"
        experiencer = "patient"
        evidence = "No recognized context modifier was found."

    return {
        "extracted_text": symptom_text,
        "start": start,
        "end": end,
        "status": status,
        "experiencer": experiencer,
        "evidence": evidence,
        "context_method": "rule_based",
    }


if __name__ == "__main__":
    examples = [
        ("I have a headache.", "headache"),
        ("I do not have a headache.", "headache"),
        ("I might have a headache.", "headache"),
        ("I had a headache yesterday.", "headache"),
        ("I no longer have a headache.", "headache"),
        ("I don't have a fever, but I have a headache.", "headache"),
        ("My mother has a fever.", "fever"),
    ]

    for text, symptom in examples:
        start = text.lower().find(symptom.lower())
        end = start + len(symptom)

        result = classify_symptom_context(
            text=text,
            start=start,
            end=end,
            symptom_text=symptom,
        )

        print(f"\nInput: {text}")
        print(f"Result: {result}")