import os
import joblib
import pandas as pd

base_dir = os.path.dirname(os.path.abspath(__file__))
model_path = os.path.join(base_dir, "..", "models", "pollution_classifier.pkl")
model = joblib.load(model_path)

# Label definitions
classes = {
    0: "Normal Water",
    1: "Packaging Residue",
    2: "Antibiotic Contamination",
    3: "Anomaly"
}

# Test samples: [ph, tds, turbidity, temperature]
test_samples = pd.DataFrame([
    {"name": "Sample 1 (Normal Water expected)", "ph": 7.0, "tds": 300.0, "turbidity": 25.0, "temperature": 27.0},
    {"name": "Sample 2 (Packaging Residue expected)", "ph": 6.4, "tds": 750.0, "turbidity": 190.0, "temperature": 28.5},
    {"name": "Sample 3 (Antibiotic Contamination expected)", "ph": 5.9, "tds": 480.0, "turbidity": 70.0, "temperature": 26.5},
    {"name": "Sample 4 (Anomaly expected)", "ph": 3.8, "tds": 1150.0, "turbidity": 420.0, "temperature": 34.0}
])

feature_cols = ['ph', 'tds', 'turbidity', 'temperature']
X_test = test_samples[feature_cols]
predictions = model.predict(X_test)
probabilities = model.predict_proba(X_test)

print("=" * 60)
print("     AquaIntel ML Model Inference Test")
print("=" * 60)
for idx, row in test_samples.iterrows():
    pred_class = predictions[idx]
    pred_label = classes.get(pred_class, "Unknown")
    confidence = probabilities[idx][pred_class] * 100
    print(f"\n{row['name']}:")
    print(f"  Inputs      : pH={row['ph']}, TDS={row['tds']}, Turbidity={row['turbidity']}, Temp={row['temperature']}C")
    print(f"  Prediction  : Class {pred_class} -> {pred_label} (Confidence: {confidence:.2f}%)")
print("\n" + "=" * 60)

