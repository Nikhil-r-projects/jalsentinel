from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import ml_model
import dl_model
from data_buffer import DataBuffer
from decision_engine import get_action
import time
import math
import os
import numpy as np
from hub_network import HubNetworkSimulation

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DASHBOARD_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "dashboard"))

app = Flask(__name__, static_folder=DASHBOARD_DIR, static_url_path="")
CORS(app)

@app.route('/', methods=['GET'])
@app.route('/dashboard', methods=['GET'])
def serve_dashboard():
    index_file = os.path.join(DASHBOARD_DIR, 'index.html')
    if os.path.exists(index_file):
        return send_from_directory(DASHBOARD_DIR, 'index.html')
    return jsonify({
        "status": "online",
        "service": "AquaIntel IoT & ML Server",
        "endpoints": ["/latest", "/sensor-data", "/actuator-command", "/node-status", "/gov-status"]
    }), 200

sensor_buffer = DataBuffer(size=10)
hub_sim = HubNetworkSimulation(num_hubs=25) # Initialize 25 simulated hubs

# ─── Node Connection Tracking ────────────────────────────────
node_registry = {
    "sensor_node": {"last_seen": 0, "ip": "", "readings": 0},
    "actuator_node": {"last_seen": 0, "ip": "", "readings": 0}
}

latest_state = {
    "ph": 0.0,
    "tds": 0.0,
    "turbidity": 0.0,
    "temperature": 0.0,
    "classification": "Waiting for data...",
    "anomaly": False,
    "spike_probability": 0.0,
    "treatment_action": "system_off",
    "timestamp": 0,
    "calibration_progress": -1,
    "baseline": {"ph": None, "tds": None, "turbidity": None, "temperature": None}
}

# ─── Calibration & Smoothing State ───────────────────────────
calibration_active = False
calibration_buffer = [] # Store 20 readings for baseline
baseline_values = {
    "ph": None,
    "tds": None,
    "turbidity": None,
    "temperature": None
}

# ─── EMA Smoothing Filter ─────────────────────────────────────
# Alpha controls smoothing strength. Lower = smoother but slower.
# 0.25 provides strong noise reduction while retaining real trends.
EMA_ALPHA = 0.25
_ema_state = {"ph": None, "tds": None, "turbidity": None, "temperature": None}

def apply_ema(key, new_val):
    """Return the EMA-smoothed value for the given sensor key."""
    if _ema_state[key] is None:
        _ema_state[key] = new_val
    else:
        _ema_state[key] = EMA_ALPHA * new_val + (1 - EMA_ALPHA) * _ema_state[key]
    return round(_ema_state[key], 4)

@app.route('/sensor-data', methods=['POST'])
def receive_sensor_data():
    global latest_state, calibration_active, calibration_buffer, baseline_values
    
    data = request.json
    if not data or "node" not in data or data["node"] != "sensor_node":
        return jsonify({"error": "Invalid request"}), 400

    required = ["ph", "tds", "turbidity", "temperature"]
    if not all(k in data for k in required):
        return jsonify({"error": "Missing data"}), 400

    # Track sensor node connection
    node_registry["sensor_node"]["last_seen"] = time.time()
    node_registry["sensor_node"]["ip"] = request.remote_addr
    node_registry["sensor_node"]["readings"] += 1
    print(f"[Node] Sensor node connected from {request.remote_addr} (reading #{node_registry['sensor_node']['readings']})")

    # 1. Raw values
    raw_ph   = float(data["ph"])
    raw_tds  = float(data["tds"])
    raw_turb = float(data["turbidity"])
    raw_temp = float(data["temperature"])

    # 2. Apply EMA smoothing
    s_ph    = apply_ema("ph",           raw_ph)
    s_tds   = apply_ema("tds",          raw_tds)
    s_turb  = apply_ema("turbidity",    raw_turb)
    s_temp  = apply_ema("temperature",  raw_temp)

    # ─── Baseline Calibration Logic ──────────────────────────
    if calibration_active:
        calibration_buffer.append([raw_ph, raw_tds, raw_turb, raw_temp])
        print(f"[Calibration] Step {len(calibration_buffer)}/20...")
        
        if len(calibration_buffer) >= 20:
            avgs = np.mean(calibration_buffer, axis=0)
            baseline_values = {
                "ph": round(float(avgs[0]), 4),
                "tds": round(float(avgs[1]), 4),
                "turbidity": round(float(avgs[2]), 4),
                "temperature": round(float(avgs[3]), 4)
            }
            calibration_active = False
            calibration_buffer = []
            print(f"[Calibration] COMPLETED. Baseline: {baseline_values}")

    # ─── Deviation Detection ─────────────────────────────────
    deltas = {"ph": 0.0, "tds": 0.0, "turbidity": 0.0, "temperature": 0.0}
    contamination_alert = False
    
    if baseline_values["ph"] is not None:
        deltas["ph"] = round(s_ph - baseline_values["ph"], 4)
        deltas["tds"] = round(s_tds - baseline_values["tds"], 4)
        deltas["turbidity"] = round(s_turb - baseline_values["turbidity"], 4)
        deltas["temperature"] = round(s_temp - baseline_values["temperature"], 4)
        
        # Threshold logic for contamination spike
        # Contamination if: Significant shift in Turbidity OR TDS + pH deviation
        # Turbidity has a much higher error control range due to natural ambient light interference
        if (abs(deltas["turbidity"]) > 100.0) or \
           (abs(deltas["tds"]) > 250.0) or \
           (abs(deltas["ph"]) > 1.5):
            contamination_alert = True
            print(f"[ALERT] Contamination spike detected! D_Turb: {deltas['turbidity']}, D_TDS: {deltas['tds']}, D_pH: {deltas['ph']}")

    # 3. ML Inference (Upgrade 5: use deviations if baseline exists)
    ml_result = ml_model.predict(s_ph, s_tds, s_turb, s_temp, baseline=baseline_values)
    
    # 4. Update buffer and Deep Learning
    sensor_buffer.append([s_ph, s_tds, s_turb, s_temp])
    spike_prob = 0.0
    if sensor_buffer.is_full():
        spike_prob = dl_model.predict_spike(sensor_buffer.get_buffer())
        
    action = get_action(ml_result, spike_prob)
    
    # Logging for Demo Mode (Upgrade 7)
    print(f"[Sync] Raw:[{raw_ph}, {raw_tds}] | Filtered:[{s_ph}, {s_tds}] | Delta:[{deltas['ph']}, {deltas['tds']}] | Alert: {contamination_alert}")

    latest_state.update({
        "ph": s_ph,
        "tds": s_tds,
        "turbidity": s_turb,
        "temperature": s_temp,
        "raw_values": {"ph": raw_ph, "tds": raw_tds, "turbidity": raw_turb, "temperature": raw_temp},
        "baseline": baseline_values,
        "deltas": deltas,
        "contamination_alert": contamination_alert,
        "classification": str(ml_result["status"]),
        "anomaly": bool(ml_result["anomaly"]),
        "spike_probability": float(round(spike_prob, 4)),
        "treatment_action": str(action),
        "timestamp": float(time.time()),
        "calibration_progress": len(calibration_buffer) if calibration_active else -1
    })

    return jsonify({"message": "Data processed"}), 200

@app.route('/latest', methods=['GET'])
def get_latest():
    state = dict(latest_state)
    state['manual_override'] = manual_override['active']
    state['override_command'] = manual_override['command']
    return jsonify(state), 200

# ─── Manual Override for Demo/Sensor Failure ─────────────────
manual_override = {
    "active": False,
    "command": "system_off"
}

@app.route('/manual-override', methods=['POST'])
def set_manual_override():
    global manual_override
    data = request.json
    if not data or "command" not in data:
        return jsonify({"error": "Missing 'command'"}), 400
    
    cmd = data["command"]
    valid_commands = ["pump_on", "uv_led_on", "electrolysis_on", "pump_and_pretreat", "system_off", "auto"]
    
    if cmd not in valid_commands:
        return jsonify({"error": f"Invalid command. Valid: {valid_commands}"}), 400
    
    if cmd == "auto":
        manual_override["active"] = False
        manual_override["command"] = "system_off"
        print(f"[Override] Manual override DISABLED → returning to AI mode")
        return jsonify({"message": "Manual override disabled, returning to AI mode", "mode": "auto"}), 200
    else:
        manual_override["active"] = True
        manual_override["command"] = cmd
        print(f"[Override] Manual override ENABLED → command: {cmd}")
        return jsonify({"message": f"Manual override set to: {cmd}", "mode": "manual", "command": cmd}), 200

@app.route('/manual-override', methods=['GET'])
def get_manual_override():
    return jsonify(manual_override), 200

@app.route('/set-baseline', methods=['POST'])
def trigger_baseline():
    global calibration_active, calibration_buffer, latest_state
    calibration_active = True
    calibration_buffer = []
    latest_state["calibration_progress"] = 0
    print("[Calibration] Triggered baseline collection (next 20 readings)")
    return jsonify({"message": "Calibration mode activated. Send 20 readings."}), 200

@app.route('/node-status', methods=['GET'])
def get_node_status():
    now = time.time()
    # Consider node online if seen in the last 15 seconds
    status = {
        "sensor_node": {
            "online": (now - node_registry["sensor_node"]["last_seen"]) < 15,
            "ip": node_registry["sensor_node"]["ip"],
            "readings": node_registry["sensor_node"]["readings"]
        },
        "actuator_node": {
            "online": (now - node_registry["actuator_node"]["last_seen"]) < 15,
            "ip": node_registry["actuator_node"]["ip"],
            "readings": node_registry["actuator_node"]["readings"]
        }
    }
    return jsonify(status), 200

@app.route('/actuator-command', methods=['GET'])
def actuator_command():
    # Track actuator node connection
    node_registry["actuator_node"]["last_seen"] = time.time()
    node_registry["actuator_node"]["ip"] = request.remote_addr
    node_registry["actuator_node"]["readings"] += 1
    
    if manual_override["active"]:
        return jsonify({"command": manual_override["command"], "mode": "manual"}), 200
    return jsonify({"command": latest_state["treatment_action"], "mode": "auto"}), 200

@app.route('/feedback', methods=['POST'])
def receive_feedback():
    data = request.json
    if not data or "label" not in data:
        return jsonify({"error": "Invalid request"}), 400
    
    required = ["ph", "tds", "turbidity", "temperature", "label"]
    if not all(k in data for k in required):
        return jsonify({"error": "Missing data"}), 400
        
    base_dir = os.path.dirname(os.path.abspath(__file__))
    csv_path = os.path.join(base_dir, "..", "dataset", "verified_data.csv")
    exists = os.path.exists(csv_path)
    
    with open(csv_path, 'a') as f:
        if not exists:
            f.write("ph,tds,turbidity,temperature,label\n")
        f.write(f"{data['ph']},{data['tds']},{data['turbidity']},{data['temperature']},{data['label']}\n")
    
    return jsonify({"message": "Feedback saved successfully"}), 200

@app.route('/retrain', methods=['POST'])
def retrain_models():
    ml_success = ml_model.retrain_classifier()
    dl_success = dl_model.retrain_lstm()
    
    if ml_success and dl_success:
        return jsonify({"message": "Models successfully retrained and hot-reloaded"}), 200
    else:
        return jsonify({"error": "Retraining failed"}), 500

@app.route('/gov-status', methods=['GET'])
def gov_status():
    status = hub_sim.get_gov_status(latest_state)
    return jsonify(status), 200

@app.route('/simulate-event', methods=['POST'])
def simulate_event():
    result = hub_sim.trigger_simulation()
    return jsonify(result), 200

@app.errorhandler(404)
def handle_404(e):
    req_path = request.path.lstrip('/')
    candidate = os.path.join(DASHBOARD_DIR, req_path)
    if req_path and os.path.isfile(candidate):
        return send_from_directory(DASHBOARD_DIR, req_path)
    return jsonify({
        "error": "404 Not Found",
        "requested_url": request.url,
        "message": f"Route '{request.path}' was not found on the AquaIntel server.",
        "valid_endpoints": {
            "Dashboard": "http://127.0.0.1:5000/ [GET]",
            "Latest Data": "http://127.0.0.1:5000/latest [GET]",
            "Sensor Ingest": "http://127.0.0.1:5000/sensor-data [POST]",
            "Actuator Poll": "http://127.0.0.1:5000/actuator-command [GET]",
            "Node Status": "http://127.0.0.1:5000/node-status [GET]",
            "Gov Network Status": "http://127.0.0.1:5000/gov-status [GET]",
            "Manual Override": "http://127.0.0.1:5000/manual-override [GET, POST]"
        }
    }), 404

if __name__ == '__main__':
    ml_model.load_models()
    dl_model.load_model()
    print("Server starting on port 5000...")
    app.run(host='0.0.0.0', port=5000, threaded=True)
