"""Run five smoke tests and measure model startup and per-input latency."""
from __future__ import annotations

import json
import time
from pathlib import Path

# Measure the time taken to import the wrapper separately from model loading.
import_start = time.perf_counter()
from symptom_extractor import extract_symptoms, get_extractor, get_model_load_seconds
wrapper_import_seconds = time.perf_counter() - import_start

TEST_CASES = [
    {
        "name": "Simple sentence",
        "input": "I have a headache.",
    },
    {
        "name": "Multiple symptoms",
        "input": "I have fever, cough, and a sore throat.",
    },
    {
        "name": "Informal description",
        "input": "My stomach hurts, and I feel like vomiting.",
    },
    {
        "name": "Longer paragraph",
        "input": (
            "For the past week, I have been feeling exhausted, "
            "experiencing headaches, and struggling to sleep."
        ),
    },
    {
        "name": "Negated symptom",
        "input": "I have a cough, but I do not have a fever.",
    },
]


def main():
    print("=" * 78)
    print("MULTICLINNER SYMPTOM EXTRACTION — STAGES 1–4")
    print("=" * 78)
    print(f"Wrapper import time: {wrapper_import_seconds * 1000:.2f} ms")
    print("The first model load may take longer because model files may need downloading.")
    print()

    # Explicitly initialize before the test loop so loading is only done once.
    load_start = time.perf_counter()
    get_extractor()
    measured_load_seconds = time.perf_counter() - load_start
    reported_load_seconds = get_model_load_seconds()

    print(f"\nModel initialization measured here: {measured_load_seconds:.2f} seconds")
    if reported_load_seconds is not None:
        print(f"Model initialization reported by wrapper: {reported_load_seconds:.2f} seconds")
    print("\nRunning test cases...")
    print("-" * 78)

    results = []
    inference_times = []

    for number, case in enumerate(TEST_CASES, start=1):
        start = time.perf_counter()
        try:
            symptoms = extract_symptoms(case["input"])
            elapsed = time.perf_counter() - start
            inference_times.append(elapsed)

            print(f"\nTEST {number}: {case['name']}")
            print(f"Input: {case['input']}")
            if symptoms:
                print("Extracted mentions:")
                for item in symptoms:
                    print(
                        f"  - {item['symptom']!r} "
                        f"(start={item['start']}, end={item['end']})"
                    )
            else:
                print("Extracted mentions: none")

            results.append({
                "test_case": case["name"],
                "input": case["input"],
                "status": "SUCCESS",
                "inference_seconds": round(elapsed, 4),
                "extracted_symptoms": symptoms,
                "note": (
                    "NER output only; verify negation/context separately."
                    if case["name"] == "Negated symptom" else None
                ),
            })
            print(f"Inference time: {elapsed * 1000:.2f} ms")

        except Exception as exc:
            elapsed = time.perf_counter() - start
            print(f"\nTEST {number}: {case['name']}")
            print(f"ERROR: {type(exc).__name__}: {exc}")
            results.append({
                "test_case": case["name"],
                "input": case["input"],
                "status": "FAILED",
                "inference_seconds": round(elapsed, 4),
                "extracted_symptoms": [],
                "error": f"{type(exc).__name__}: {exc}",
            })

    output = {
        "model": "BSC-NLP4BIA/multiclinner-en-symptom-BiomedBERT",
        "wrapper_import_seconds": round(wrapper_import_seconds, 4),
        "model_initialization_seconds": round(measured_load_seconds, 4),
        "test_count": len(TEST_CASES),
        "success_count": sum(r["status"] == "SUCCESS" for r in results),
        "failed_count": sum(r["status"] == "FAILED" for r in results),
        "mean_inference_seconds": (
            round(sum(inference_times) / len(inference_times), 4)
            if inference_times else None
        ),
        "tests": results,
    }

    output_path = Path(__file__).with_name("symptom_test_results.json")
    output_path.write_text(
        json.dumps(output, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print("\n" + "=" * 78)
    print("SUMMARY")
    print("=" * 78)
    print(f"Tests passed (execution): {output['success_count']}/{len(TEST_CASES)}")
    print(f"Tests failed (execution): {output['failed_count']}/{len(TEST_CASES)}")
    if output["mean_inference_seconds"] is not None:
        print(f"Mean inference time: {output['mean_inference_seconds'] * 1000:.2f} ms")
    print(f"Detailed results: {output_path}")
    print("\nNote: SUCCESS means the model ran without an exception, not that every")
    print("symptom was detected correctly. Review the predictions manually.")


if __name__ == "__main__":
    main()
