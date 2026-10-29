# MultiClinNER Symptom Extraction — Stages 1–4

This small project installs and tests the English MultiClinNER symptom NER model,
keeps one inferencer instance in memory per Python process, and measures model
initialization and per-input inference time.

## Requirements

- A supported Python version and compatible PyTorch/dependencies.
- Internet access for the first installation and model download.
- Several GB of free disk space may be useful for model/dependency caches.

Python 3.14 compatibility depends on the versions of PyTorch and the dependencies
resolved by the inference package. If installation fails, try a separate Python
3.12 or 3.13 environment rather than removing Python 3.14.

## Setup (Windows PowerShell)

```powershell
cd SymptomExtraction_Stages1-4
py -0p
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If Python 3.13 is not installed, install a supported Python version side-by-side,
or replace `-3.13` with your installed version if compatible.

## Run the five tests

```powershell
python test_symptoms.py
```

The first run can be slow because it installs/downloads model assets and loads
the model. Results are written to `symptom_test_results.json`.

## Important

- The model is loaded once per Python process and reused for all five test cases.
- Running the test script again starts a new process and loads the model again.
  The model files should normally be cached on disk, so they do not need to be
  downloaded again unless the cache is cleared or the model changes.
- This is symptom mention extraction, not a diagnosis.
- The model does not by itself reliably distinguish affirmed symptoms from
  negated statements such as "I do not have a fever".
- Do not use extraction results alone for medical decisions. Evaluate the model
  on representative inputs before connecting it to doctor recommendations.

## Model

`BSC-NLP4BIA/multiclinner-en-symptom-BiomedBERT`

Official model card:
https://huggingface.co/BSC-NLP4BIA/multiclinner-en-symptom-BiomedBERT
