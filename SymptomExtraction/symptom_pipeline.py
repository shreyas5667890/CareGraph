"""
Integrated symptom analysis pipeline.

Pipeline:
1. Extract symptom mentions.
2. Detect symptom context.
3. Normalize extracted symptoms.
4. Determine which symptoms are eligible for the
   confirmed symptom list used by downstream components.
"""

from typing import Any

from symptom_extractor import extract_symptoms
from symptom_context import classify_symptom_context
from symptom_normalizer import SymptomNormalizer


# Load the vocabulary once and reuse the normalizer.
_normalizer = None


def get_normalizer():
    """Return a shared SymptomNormalizer instance."""
    global _normalizer

    if _normalizer is None:
        _normalizer = SymptomNormalizer()

    return _normalizer


def analyze_patient_text(text: str) -> dict[str, Any]:
    """
    Extract, contextualize, and normalize symptom mentions.

    Parameters
    ----------
    text : str
        Free-text description supplied by the user.

    Returns
    -------
    dict
        Structured symptom analysis.

    Note
    ----
    This is a prototype, not a diagnostic system.
    """

    if not isinstance(text, str):
        raise TypeError("Patient text must be a string.")

    if not text.strip():
        return {
            "original_text": text,
            "symptoms": [],
            "confirmed_symptoms": [],
            "uncertain_symptoms": [],
            "historical_symptoms": [],
            "resolved_symptoms": [],
            "negated_symptoms": [],
            "other_person_symptoms": [],
            "unmatched_symptoms": [],
        }

    # Step 1: Extract symptom mentions from the original text.
    extracted_symptoms = extract_symptoms(text)

    normalizer = get_normalizer()
    results = []

    # Step 2 and 3: Contextualize and normalize each mention.
    for extracted in extracted_symptoms:

        start = extracted["start"]
        end = extracted["end"]

        # Prefer the exact text span from the original input.
        mention = text[start:end]

        # Fallback in case the offsets do not yield usable text.
        if not mention.strip():
            mention = extracted.get("text_in_input", "")
        
        if not mention.strip():
            continue

        context = classify_symptom_context(
            text=text,
            start=start,
            end=end,
            symptom_text=mention,
        )

        normalized = normalizer.normalize(mention)

        result = {
            "extracted_text": mention,
            "start": start,
            "end": end,
            "label": extracted.get("label", "SYMPTOM"),
            "normalized_symptom": normalized["normalized"],
            "normalization_method": normalized["method"],
            "normalization_confidence": normalized["confidence"],
            "normalization_matched": normalized["matched"],
            "normalization_suggestion": normalized.get("suggestion"),
            "status": context["status"],
            "experiencer": context["experiencer"],
            "context_evidence": context["evidence"],
            "context_method": context["context_method"],
        }

        results.append(result)

    # Step 4: Separate symptoms by context.
    confirmed = [
        item for item in results
        if item["status"] == "present"
        and item["normalization_matched"]
    ]

    uncertain = [
        item for item in results
        if item["status"] == "uncertain"
    ]

    historical = [
        item for item in results
        if item["status"] == "historical"
    ]

    resolved = [
        item for item in results
        if item["status"] == "resolved"
    ]

    negated = [
        item for item in results
        if item["status"] == "negated"
    ]

    other_person = [
        item for item in results
        if item["status"] == "other_person"
    ]

    unmatched_normalizations = [
        item for item in results
        if not item["normalization_matched"]
    ]

    return {
        "original_text": text,
        "symptoms": results,
        "confirmed_symptoms": confirmed,
        "uncertain_symptoms": uncertain,
        "historical_symptoms": historical,
        "resolved_symptoms": resolved,
        "negated_symptoms": negated,
        "other_person_symptoms": other_person,
        "unmatched_normalizations": unmatched_normalizations,
    }


def print_analysis(result: dict[str, Any]) -> None:
    """Print symptom analysis in a readable format."""

    print("\n" + "=" * 65)
    print("PATIENT SYMPTOM ANALYSIS")
    print("=" * 65)

    print(f"\nInput: {result['original_text']}")

    if not result["symptoms"]:
        print("\nNo symptoms were extracted.")
        print("Note: This does not prove that no symptoms are present.")
        return

    print(f"\nTotal extracted mentions: {len(result['symptoms'])}")

    for index, symptom in enumerate(result["symptoms"], start=1):
        print(f"\n--- Symptom {index} ---")
        print(f"Extracted text: {symptom['extracted_text']}")
        print(f"Normalized: {symptom['normalized_symptom']}")
        print(f"Status: {symptom['status']}")
        print(f"Experiencer: {symptom['experiencer']}")
        print(f"Normalization method: {symptom['normalization_method']}")
        print(
            "Normalization confidence: "
            f"{symptom['normalization_confidence']}"
        )
        print(f"Context evidence: {symptom['context_evidence']}")

        if not symptom["normalization_matched"]:
            print(
                "Normalization suggestion: "
                f"{symptom['normalization_suggestion']}"
            )

    print("\n" + "-" * 65)
    print("SUMMARY")
    print("-" * 65)

    print(
        "Confirmed normalized symptoms:",
        [
            item["normalized_symptom"]
            for item in result["confirmed_symptoms"]
        ],
    )

    print(
        "Uncertain symptoms:",
        [
            item["extracted_text"]
            for item in result["uncertain_symptoms"]
        ],
    )

    print(
        "Negated symptoms:",
        [
            item["extracted_text"]
            for item in result["negated_symptoms"]
        ],
    )

    print(
        "Historical symptoms:",
        [
            item["extracted_text"]
            for item in result["historical_symptoms"]
        ],
    )

    print(
        "Resolved symptoms:",
        [
            item["extracted_text"]
            for item in result["resolved_symptoms"]
        ],
    )

    print(
        "Symptoms reported about others:",
        [
            item["extracted_text"]
            for item in result["other_person_symptoms"]
        ],
    )

    print(
        "Unmatched normalizations:",
        [
            item["extracted_text"]
            for item in result["unmatched_normalizations"]
        ],
    )

    print("\nNote: This output is not a diagnosis.")


if __name__ == "__main__":

    examples = [
        "I have a headache.",
        "I do not have a headache.",
        "I might have a headache.",
        "I don't have a fever, but I have a headache.",
        "My mother has a fever.",
        "I am not sure whether I have a headache.",
        "I had a headache yesterday, but I feel fine now.",
        "I have a headache and I do not have a fever.",
        "I no longer have a headache, but I still have nausea.",
        "My father has a headache, but I have a fever.",
        "I have had a headache since yesterday.",
    ]

    for example in examples:
        result = analyze_patient_text(example)
        print_analysis(result)