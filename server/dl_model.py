import os
import numpy as np
import joblib

try:
    import tensorflow as tf
except ImportError:
    tf = None

model = None
scaler = None

def load_model():
    global model, scaler
    if tf is None:
        print("Note: TensorFlow is not installed. DL model (LSTM) disabled; ML models will run normally.")
        return

    base_dir = os.path.dirname(__file__)
    model_path = os.path.join(base_dir, "..", "models", "lstm_model.keras")
    scaler_path = os.path.join(base_dir, "..", "models", "scaler.pkl")
    
    if os.path.exists(model_path):
        tf.get_logger().setLevel('ERROR')
        try:
            model = tf.keras.models.load_model(model_path)
            print("DL Model (lstm_model.keras) loaded successfully.")
        except Exception as e:
            print(f"Warning: Could not load DL model: {e}")
    else:
        print(f"Warning: DL model not found at {model_path}.")
        
    if os.path.exists(scaler_path):
        try:
            scaler = joblib.load(scaler_path)
        except Exception as e:
            print(f"Warning: Could not load scaler: {e}")
    else:
        print(f"Warning: Scaler not found at {scaler_path}.")

def predict_spike(raw_buffer):
    """ raw_buffer has length 10, features list """
    if model is None or scaler is None:
        load_model()
        if model is None or scaler is None:
            return 0.0

    if len(raw_buffer) < 10:
        return 0.0
        
    # User's training mapped: 
    # data[['ph','tds','turbidity','temperature']].values
    # features = scaler.fit_transform(features)
    # Then y.append(features[i+sequence_length][2]) # Predict turbidity
    
    scaled_features = scaler.transform(raw_buffer)
    input_data = np.array([scaled_features[-10:]])
    prediction = model.predict(input_data, verbose=0)
    
    # Reversing transformation for the predicted turbidity value (index 2)
    # Our prediction is outputted functionally from loss=mse, we can inverse scale it
    # But usually a spike probability is requested. We use MSE scalar output here
    turbidity_pred_scaled = prediction[0][0]
    
    # We create a dummy array to inverse transform just the turbidity
    dummy = np.zeros((1, 4))
    dummy[0][2] = turbidity_pred_scaled
    pred_real = scaler.inverse_transform(dummy)[0][2]
    
    # Spike probability translation: turbidity > 100 is typically a spike in synthetic data
    # (Sigmoid output can be pseudo-extrapolated. We map it out of 200 NTU for probabilty approx)
    prob = float(pred_real / 200.0) 
    if prob < 0: prob = 0.0
    if prob > 1: prob = 1.0
    
    return float(prob)

def retrain_lstm():
    global model, scaler
    if tf is None:
        print("TensorFlow is not installed. Skipping LSTM retraining.")
        return False
    import pandas as pd
    from sklearn.preprocessing import MinMaxScaler
    
    base_dir = os.path.dirname(__file__)
    main_csv = os.path.join(base_dir, "..", "dataset", "pollution_data.csv")
    verified_csv = os.path.join(base_dir, "..", "dataset", "verified_data.csv")
    
    try:
        data = pd.read_csv(main_csv)
        if os.path.exists(verified_csv):
            new_data = pd.read_csv(verified_csv)
            data = pd.concat([data, new_data], ignore_index=True)
            
        features = data[['ph','tds','turbidity','temperature']].values
        
        new_scaler = MinMaxScaler()
        features = new_scaler.fit_transform(features)
        
        sequence_length = 10
        X = []
        y = []
        for i in range(len(features)-sequence_length):
            X.append(features[i:i+sequence_length])
            y.append(features[i+sequence_length][2]) # predict turbidity
            
        X = np.array(X)
        y = np.array(y)
        
        if model is None:
            load_model()
            
        # Fit 1 epoch dynamically adjusting weights without destroying previous history
        model.fit(X, y, epochs=1, batch_size=32, verbose=0)
        
        model_path = os.path.join(base_dir, "..", "models", "lstm_model.keras")
        scaler_path = os.path.join(base_dir, "..", "models", "scaler.pkl")
        
        model.save(model_path)
        joblib.dump(new_scaler, scaler_path)
        
        scaler = new_scaler
        print("DL Model retrained and hot-swapped successfully.")
        return True
    except Exception as e:
        print(f"Error retraining DL model: {e}")
        return False
