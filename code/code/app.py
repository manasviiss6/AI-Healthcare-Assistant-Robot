from flask import Flask, render_template, request, jsonify
import socket
import threading
import json
import time

app = Flask(__name__)

PI_IP   = "192.168.137.101"
PI_PORT = 5000

robot_data = {
    "name":         "--",
    "emotion":      "--",
    "distance":     "--",
    "temp":         "--",
    "pulse":        "--",
    "ecg":          "--",
    "state":        "--",
    "sleep":        "--",
    "water":        "--",
    "pain":         "--",
    "appetite":     "--",
    "exercise":     "--",
    "stress":       "--",
    "health_report": "--"
}

client_socket = None

# ===== CONNECT TO PI WITH AUTO RECONNECT =====
def connect_to_pi():
    global client_socket

    while True:
        try:
            print("[INFO] Connecting to Raspberry Pi at", PI_IP, "port", PI_PORT)
            client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client_socket.connect((PI_IP, PI_PORT))
            print("[INFO] Connected to Raspberry Pi.")

            buffer = ""

            while True:
                try:
                    chunk = client_socket.recv(4096).decode("utf-8", errors="ignore")
                    if not chunk:
                        print("[WARN] Pi disconnected.")
                        break

                    buffer += chunk

                    # Parse all complete newline-terminated JSON lines
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            parsed = json.loads(line)
                            robot_data.update(parsed)
                            print("[DATA]", robot_data)
                        except json.JSONDecodeError as e:
                            print("[JSON ERROR]", e, "| Raw line:", line[:80])

                except Exception as e:
                    print("[RECV ERROR]", e)
                    break

        except Exception as e:
            print("[CONNECT ERROR]", e)

        print("[INFO] Retrying connection in 3 seconds...")
        time.sleep(3)


threading.Thread(target=connect_to_pi, daemon=True).start()


# ===== ROUTES =====

@app.route("/")
def home():
    return render_template("index.html")


@app.route("/get_data")
def get_data():
    return jsonify(robot_data)


@app.route("/send_command", methods=["POST"])
def send_command():
    cmd = request.json.get("command", "")
    if client_socket:
        try:
            client_socket.sendall((cmd + "\n").encode())
            print("[CMD SENT]", cmd)
        except Exception as e:
            print("[SEND ERROR]", e)
            return jsonify({"status": "error", "message": str(e)}), 500
    else:
        return jsonify({"status": "error", "message": "Not connected to Pi"}), 503
    return jsonify({"status": "sent"})


@app.route("/set_reminder", methods=["POST"])
def set_reminder():
    reminder_time = request.json.get("time", "")
    reminder_slot = request.json.get("slot", "")

    message = json.dumps({
        "reminder_time": reminder_time,
        "reminder_slot": reminder_slot
    })

    if client_socket:
        try:
            client_socket.sendall((message + "\n").encode())
            print("[REMINDER SET]", message)
        except Exception as e:
            print("[SEND ERROR]", e)
            return jsonify({"status": "error", "message": str(e)}), 500
    else:
        return jsonify({"status": "error", "message": "Not connected to Pi"}), 503

    return jsonify({"status": "reminder_set"})


if __name__ == "__main__":
    print("Starting MedBot Dashboard Server on http://0.0.0.0:8000")
    app.run(host="0.0.0.0", port=8000, debug=False)
