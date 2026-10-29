import re
import csv
from pathlib import Path
from difflib import SequenceMatcher


BASE_DIR = Path(__file__).resolve().parent
VOCABULARY_FILE = BASE_DIR / "symptom_vocabulary.csv"
WEIGHTS_FILE = BASE_DIR / "Symptom_Weights.csv"

# Tune these thresholds after evaluating the normalizer.
MIN_FUZZY_SCORE = 0.88
MIN_SCORE_MARGIN = 0.08


# Common expressions mapped to dataset vocabulary.
# Add new aliases as you evaluate real examples.
SYNONYMS = {
    "stomach ache": "stomach_pain",
    "difficulty breathing": "breathlessness",
    "i have difficulty breathing": "breathlessness",
    "stomach hurts": "stomach_pain",
    "pain in stomach": "stomach_pain",
    "belly ache": "belly_pain",
    "pain in belly": "belly_pain",
    "pain in abdomen": "abdominal_pain",
    "abdominal ache": "abdominal_pain",
    "head ache": "headache",
    "high temperature": "high_fever",
    "temperature": "mild_fever",
    "feeling feverish": "mild_fever",
    "throwing up": "vomiting",
    "puking": "vomiting",
    "feeling sick": "nausea",
    "feeling dizzy": "dizziness",
    "lightheaded": "dizziness",
    "tiredness": "fatigue",
    "feeling tired": "fatigue",
    "feeling exhausted": "fatigue",
    "difficulty breathing": "breathlessness",
    "shortness of breath": "breathlessness",
    "hard to breathe": "breathlessness",
    "runny nose": "runny_nose",
    "blocked nose": "congestion",
    "stuffy nose": "congestion",
    "joint aches": "joint_pain",
    "muscle aches": "muscle_pain",
    "itchy skin": "itching",
    "skin itching": "itching",
    "loose motions": "diarrhoea",
    "loose stools": "diarrhoea",
    "difficulty passing stool": "constipation",
    "heart racing": "fast_heart_rate",
    "racing heartbeat": "fast_heart_rate",
    "heart pounding": "palpitations",
    "stomach hurts": "stomach_pain",
    "throwing up": "vomiting",
    "difficulty breathing": "breathlessness",
    "i have a headache": "headache",
    "head ache": "headache",
}


def clean_text(text: str) -> str:
    """Standardize text for matching."""
    text = str(text).lower().strip()
    text = text.replace("_", " ")
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def load_vocabulary() -> list[str]:
    """Load the canonical symptoms from the existing dataset."""
    if VOCABULARY_FILE.exists():
        with VOCABULARY_FILE.open(
            "r", encoding="utf-8-sig", newline=""
        ) as file:
            reader = csv.DictReader(file)
            symptoms = [
                row["symptom"].strip()
                for row in reader
                if row.get("symptom", "").strip()
            ]
    elif WEIGHTS_FILE.exists():
        with WEIGHTS_FILE.open(
            "r", encoding="utf-8-sig", newline=""
        ) as file:
            reader = csv.reader(file)
            symptoms = [
                row[0].strip()
                for row in reader
                if row and row[0].strip()
            ]
    else:
        raise FileNotFoundError(
            "Could not find symptom_vocabulary.csv or "
            "Symptom_Weights.csv in the project folder."
        )

    # Remove duplicates while preserving order.
    return list(dict.fromkeys(symptoms))


class SymptomNormalizer:
    

    def preprocess_phrase(self, phrase: str) -> str:
        """Remove common conversational framing around a symptom."""

        text = clean_text(phrase)

        prefixes = [
            "i have been experiencing",
            "i have been having",
            "i am experiencing",
            "i am having",
            "i feel like",
            "i have",
            "i am",
            "i feel",
            "experiencing",
            "suffering from",
            "complaining of",
            "my",
        ]

        suffixes = [
            "for several days",
            "for a few days",
            "for the past week",
            "since yesterday",
            "currently",
        ]

        # Repeat to remove nested framing, such as "I have a headache".
        changed = True
        while changed:
            changed = False

            for prefix in sorted(prefixes, key=len, reverse=True):
                if text.startswith(prefix + " "):
                    text = text[len(prefix):].strip()
                    changed = True
                    break

            if changed:
                continue

            # Remove leading articles.
            new_text = re.sub(r"^(a|an|the)\s+", "", text)
            if new_text != text:
                text = new_text.strip()
                changed = True
                continue

            for suffix in sorted(suffixes, key=len, reverse=True):
                if text.endswith(" " + suffix):
                    text = text[:-len(suffix)].strip()
                    changed = True
                    break

        return text.strip()



    def __init__(self):
        self.vocabulary = load_vocabulary()

        # Map cleaned terms to their original dataset spelling.
        self.canonical_by_cleaned = {
            clean_text(symptom): symptom
            for symptom in self.vocabulary
        }

        # Validate aliases against the actual vocabulary.
        self.aliases = {}
        for alias, canonical in SYNONYMS.items():
            cleaned_canonical = clean_text(canonical)

            if cleaned_canonical in self.canonical_by_cleaned:
                self.aliases[clean_text(alias)] = (
                    self.canonical_by_cleaned[cleaned_canonical]
                )

    def normalize(self, phrase: str) -> dict:
        """
        Return a normalized symptom or an explicit unmatched result.

        This function normalizes one phrase at a time.
        It does not extract symptoms from full patient text.
        """
        original = str(phrase).strip()
        cleaned = self.preprocess_phrase(original)

        if not cleaned:
            return {
                "original": original,
                "normalized": None,
                "method": "empty_input",
                "confidence": 0.0,
                "matched": False,
            }

        # 1. Exact canonical match.
        if cleaned in self.canonical_by_cleaned:
            return {
                "original": original,
                "normalized": self.canonical_by_cleaned[cleaned],
                "method": "exact",
                "confidence": 1.0,
                "matched": True,
            }

        # 2. Explicit synonym match.
        if cleaned in self.aliases:
            return {
                "original": original,
                "normalized": self.aliases[cleaned],
                "method": "synonym",
                "confidence": 0.95,
                "matched": True,
            }

        # 3. Conservative fuzzy matching for minor text differences.
        scores = []

        for candidate in self.vocabulary:
            candidate_cleaned = clean_text(candidate)
            score = SequenceMatcher(
                None, cleaned, candidate_cleaned
            ).ratio()

            scores.append((score, candidate))

        scores.sort(reverse=True)
        best_score, best_candidate = scores[0]

        second_score = scores[1][0] if len(scores) > 1 else 0.0
        margin = best_score - second_score

        if (
            best_score >= MIN_FUZZY_SCORE
            and margin >= MIN_SCORE_MARGIN
        ):
            return {
                "original": original,
                "normalized": best_candidate,
                "method": "fuzzy",
                "confidence": round(best_score, 3),
                "matched": True,
            }

        return {
            "original": original,
            "normalized": None,
            "method": "unmatched",
            "confidence": round(best_score, 3),
            "matched": False,
            "suggestion": best_candidate,
        }


if __name__ == "__main__":
    normalizer = SymptomNormalizer()

    examples = [
        "abdominal_pain",
        "Abdominal Pain",
        "stomach hurts",
        "throwing up",
        "difficulty breathing",
        "head ache",
        "unfamiliar symptom xyz",
    ]

    print(f"Loaded {len(normalizer.vocabulary)} symptoms.\n")

    for phrase in examples:
        result = normalizer.normalize(phrase)

        print(f"Input:      {result['original']}")
        print(f"Normalized: {result['normalized']}")
        print(f"Method:     {result['method']}")
        print(f"Confidence: {result['confidence']}")
        print(f"Matched:    {result['matched']}")

        if result.get("suggestion"):
            print(f"Suggestion: {result['suggestion']}")

        print("-" * 45)
