from symptom_normalizer import SymptomNormalizer

normalizer = SymptomNormalizer()

phrases = [
    "My stomach hurts",
    "I have a headache",
    "I feel like throwing up",
    "I have difficulty breathing",
]

for phrase in phrases:
    print(normalizer.normalize(phrase))