"""
MultiClinNER symptom extraction wrapper.

The model is initialized lazily on the first call to get_extractor() and then
reused for subsequent calls in the same Python process.
"""
from __future__ import annotations

import time
from typing import Any

MODEL_NAME = "BSC-NLP4BIA/multiclinner-en-symptom-BiomedBERT"

_inferencer = None
_model_load_seconds = None


def get_extractor():
    """Load MultiClinNER once per Python process and return the shared instance."""
    global _inferencer, _model_load_seconds

    if _inferencer is None:
        from ner_nlp4bia.inference.pipeline import PipelineInferencer

        start = time.perf_counter()
        print(f"Loading MultiClinNER model: {MODEL_NAME}")
        _inferencer = PipelineInferencer(MODEL_NAME)
        _model_load_seconds = time.perf_counter() - start
        print(f"Model ready in {_model_load_seconds:.2f} seconds.")

    return _inferencer


def get_model_load_seconds():
    """Return the model initialization time, or None before the model is loaded."""
    return _model_load_seconds


def extract_symptoms(text: str) -> list[dict[str, Any]]:
    """
    Extract symptom mentions from English clinical text.

    Returns dictionaries with symptom text and character offsets in the
    original input. This is mention extraction, not diagnosis or negation
    detection.
    """
    if not isinstance(text, str):
        raise TypeError("text must be a string")

    if not text.strip():
        return []

    from ner_nlp4bia.data.corpus import Document

    inferencer = get_extractor()
    document = Document(filename="patient_input", text=text)
    result = inferencer.infer_document(document)

    symptoms = []
    for annotation in result.annotations:
        label = str(getattr(annotation, "label", ""))
        if label.upper() == "SYMPTOM":
            start = int(annotation.start)
            end = int(annotation.end)
            mention = str(annotation.text).strip()

            if mention:
                symptoms.append({
                    "symptom": mention,
                    "start": start,
                    "end": end,
                    "text_in_input": text[start:end],
                    "label": label,
                })

    return symptoms
