import cv2
import time
import os
import json
import socket
import re
import glob
import random
import RPi.GPIO as GPIO
import face_recognition
import numpy as np
import tflite_runtime.interpreter as tflite
import pyttsx3
from google import genai

# ADS1115 imports
import board
import busio
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn

# ================= CONFIG =================
API_KEY = "YOUR_API_KEY_HERE"
MODEL_NAME = "models/gemini-flash-latest"
PORT = 5000

DISTANCE_TRIGGER = 60
SAFETY_STOP = 20

PERSON_DETECTION_THRESHOLD = 0.65
FACE_MATCH_THRESHOLD = 0.45
PERSON_CONFIRM_FRAMES = 3

# ================= GEMINI =================
client_ai = genai.Client(api_key=API_KEY)

def clean_text(text):
    text = re.sub(r"\*+", "", text)
    text = re.sub(r"[^\x00-\x7F]", "", text)
    return text.replace("\n", " ").strip()

def gemini_emotion_chat(name, emotion):
    prompts = {
        "Happy":    "The patient named " + name + " looks happy today. Give a warm 2 sentence friendly greeting and a positive comment about their mood. Keep it natural and conversational.",
        "Sad":      "The patient named " + name + " looks sad today. Give a warm 2 sentence empathetic greeting that lifts their spirits. Keep it natural and caring.",
        "Angry":    "The patient named " + name + " looks angry or stressed today. Give a calm 2 sentence soothing greeting. Keep it natural and calming.",
        "Neutral":  "The patient named " + name + " looks calm and neutral today. Give a friendly 2 sentence greeting. Keep it natural.",
        "Fear":     "The patient named " + name + " looks anxious or fearful today. Give a reassuring 2 sentence greeting. Keep it natural and comforting.",
        "Disgust":  "The patient named " + name + " looks uncomfortable today. Give a gentle 2 sentence caring greeting. Keep it natural.",
        "Surprise": "The patient named " + name + " looks surprised today. Give a fun 2 sentence cheerful greeting. Keep it natural."
    }
    prompt = prompts.get(emotion, "Greet the patient named " + name + " warmly in 2 sentences.")

    try:
        res = client_ai.models.generate_content(model=MODEL_NAME, contents=prompt)
        return clean_text(res.text)
    except Exception:
        return "Hello " + name + ". It is great to see you today. I am here to help with your health check."

def gemini_health_report(name, temp, pulse, ecg, answers):
    answer_text = ""
    for q, a in answers.items():
        answer_text += q + ": " + a + "\n"

    prompt = (
        "Patient name: " + name + "\n"
        "Temperature: " + temp + "\n"
        "Pulse: " + pulse + "\n"
        "ECG: " + ecg + "\n\n"
        "Patient health questionnaire answers:\n" + answer_text + "\n"
        "Based on both sensor readings AND the questionnaire answers, give a short friendly 4 to 5 sentence health summary. "
        "Include wellness advice, diet suggestion, hydration advice, sleep advice, and exercise advice. "
        "Address the patient by name. "
        "Do NOT diagnose any disease. "
        "Keep it warm, encouraging and easy to understand."
    )

    try:
        res = client_ai.models.generate_content(model=MODEL_NAME, contents=prompt)
        return clean_text(res.text)
    except Exception:
        return "Hello " + name + ". Your readings look stable. Remember to stay hydrated, eat healthy, and get enough rest."

# ================= VOICE =================
engine = pyttsx3.init()
engine.setProperty("rate", 145)

def speak(text):
    text = clean_text(text)
    print("[VOICE]:", text)
    engine.say(text)
    engine.runAndWait()

# ================= SOCKET =================
server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.bind(("", PORT))
server.listen(1)
print("Waiting for laptop...")
conn, addr = server.accept()
print("Connected:", addr)
conn.setblocking(False)

# ================= GPIO SETUP =================
GPIO.setwarnings(False)
GPIO.setmode(GPIO.BCM)

# ================= ULTRASONIC =================
TRIG = 23
ECHO = 24
GPIO.setup(TRIG, GPIO.OUT)
GPIO.setup(ECHO, GPIO.IN)

def get_distance():
    GPIO.output(TRIG, False)
    time.sleep(0.02)

    GPIO.output(TRIG, True)
    time.sleep(0.00001)
    GPIO.output(TRIG, False)

    timeout_start = time.time()
    while GPIO.input(ECHO) == 0:
        if time.time() - timeout_start > 0.05:
            return 999
    pulse_start = time.time()

    while GPIO.input(ECHO) == 1:
        if time.time() - pulse_start > 0.05:
            return 999
    pulse_end = time.time()

    pulse_duration = pulse_end - pulse_start
    distance = round(pulse_duration * 17150, 2)
    return distance

# ================= MOTORS =================
LEFT_DIR  = 17
LEFT_PWM  = 18
RIGHT_DIR = 22
RIGHT_PWM = 27

GPIO.setup(LEFT_DIR, GPIO.OUT)
GPIO.setup(RIGHT_DIR, GPIO.OUT)
GPIO.setup(LEFT_PWM, GPIO.OUT)
GPIO.setup(RIGHT_PWM, GPIO.OUT)

left_motor = GPIO.PWM(LEFT_PWM, 100)
right_motor = GPIO.PWM(RIGHT_PWM, 100)
left_motor.start(0)
right_motor.start(0)

def move_forward():
    GPIO.output(LEFT_DIR, 0)
    GPIO.output(RIGHT_DIR, 0)
    left_motor.ChangeDutyCycle(100)
    right_motor.ChangeDutyCycle(100)
    print("MOTOR: Forward")

def turn_left():
    GPIO.output(LEFT_DIR, 0)
    GPIO.output(RIGHT_DIR, 0)
    left_motor.ChangeDutyCycle(60)
    right_motor.ChangeDutyCycle(100)
    print("MOTOR: Left")

def turn_right():
    GPIO.output(LEFT_DIR, 0)
    GPIO.output(RIGHT_DIR, 0)
    left_motor.ChangeDutyCycle(100)
    right_motor.ChangeDutyCycle(60)
    print("MOTOR: Right")

def stop_motors():
    left_motor.ChangeDutyCycle(0)
    right_motor.ChangeDutyCycle(0)
    GPIO.output(LEFT_DIR, 0)
    GPIO.output(RIGHT_DIR, 0)
    print("MOTOR: Stop")

# ================= DS18B20 TEMPERATURE =================
def read_temperature():
    try:
        base_dir = "/sys/bus/w1/devices/"
        device_folders = glob.glob(base_dir + "28*")

        if not device_folders:
            return "Sensor not found"

        device_file = device_folders[0] + "/w1_slave"

        for _ in range(5):
            with open(device_file, "r") as f:
                lines = f.readlines()

            if lines[0].strip().endswith("YES"):
                temp_pos = lines[1].find("t=")
                if temp_pos != -1:
                    temp_string = lines[1][temp_pos + 2:]
                    temp_c = float(temp_string) / 1000.0
                    return str(round(temp_c, 2)) + " C"

            time.sleep(0.2)

        return "Temp read error"

    except Exception as e:
        return "Temp error: " + str(e)

# ================= DUMMY PULSE =================
def read_pulse():
    return str(75 + random.randint(-5, 5)) + " BPM"

# ================= ECG LEAD-OFF PINS =================
ECG_LO_PLUS = 5
ECG_LO_MINUS = 6

GPIO.setup(ECG_LO_PLUS, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
GPIO.setup(ECG_LO_MINUS, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)

# ================= ADS1115 ECG SETUP =================
i2c = busio.I2C(board.SCL, board.SDA)
ads = ADS.ADS1115(i2c)
ads.gain = 1
ecg_channel = AnalogIn(ads, 0)

def read_ecg():
    try:
        lo_plus_val = GPIO.input(ECG_LO_PLUS)
        lo_minus_val = GPIO.input(ECG_LO_MINUS)
        print("LO+:", lo_plus_val, "LO-:", lo_minus_val)

        if lo_plus_val == 1 or lo_minus_val == 1:
            return "Leads not attached - check electrodes"

        print("Recording ECG for 5 seconds...")
        samples = []

        for _ in range(500):
            samples.append(ecg_channel.value)
            time.sleep(0.01)

        arr = np.array(samples, dtype=np.float32)
        arr -= np.mean(arr)

        threshold = np.std(arr) * 0.6
        peaks = []

        for i in range(1, len(arr) - 1):
            if arr[i] > threshold and arr[i] > arr[i - 1] and arr[i] > arr[i + 1]:
                if not peaks or (i - peaks[-1]) > 30:
                    peaks.append(i)

        if len(peaks) < 3:
            return "Weak ECG signal - check electrode placement"

        intervals = np.diff(peaks)
        cv_val = np.std(intervals) / np.mean(intervals)

        if cv_val < 0.15:
            return "Normal Sinus Rhythm"
        elif cv_val < 0.30:
            return "Slightly Irregular"
        else:
            return "Irregular - consult doctor"

    except Exception as e:
        return "Error: " + str(e)

# ================= PERSON MODEL =================
person_model = tflite.Interpreter(model_path="person_detect.tflite")
person_model.allocate_tensors()
p_in = person_model.get_input_details()
p_out = person_model.get_output_details()
p_h = p_in[0]["shape"][1]
p_w = p_in[0]["shape"][2]

# ================= LOAD FACES =================
known_encodings = []
known_names = []

for person in os.listdir("known_faces"):
    folder = os.path.join("known_faces", person)
    if os.path.isdir(folder):
        for img in os.listdir(folder):
            image = face_recognition.load_image_file(os.path.join(folder, img))
            enc = face_recognition.face_encodings(image)
            if enc:
                known_encodings.append(enc[0])
                known_names.append(person)

print("Loaded", len(known_names), "known faces:", known_names)
print("Face system ready.")

# ================= EMOTION MODEL =================
emotion_model = tflite.Interpreter(model_path="emotion_model.tflite")
emotion_model.allocate_tensors()
e_in = emotion_model.get_input_details()
e_out = emotion_model.get_output_details()
emotion_labels = ["Angry", "Disgust", "Fear", "Happy", "Neutral", "Sad", "Surprise"]

def detect_emotion(face_img):
    gray = cv2.cvtColor(face_img, cv2.COLOR_BGR2GRAY)
    resized = cv2.resize(gray, (48, 48))
    input_data = resized.reshape(1, 48, 48, 1).astype(np.float32) / 255.0
    emotion_model.set_tensor(e_in[0]["index"], input_data)
    emotion_model.invoke()
    output = emotion_model.get_tensor(e_out[0]["index"])
    return emotion_labels[np.argmax(output)]

# ================= HEALTH QUESTIONS =================
HEALTH_QUESTIONS = [
    ("sleep",    "How many hours of sleep did you get last night? Please say a number.",        "Sleep Hours"),
    ("water",    "How many glasses of water have you had today? Please say a number.",          "Water Intake"),
    ("pain",     "Are you feeling any pain or discomfort today? Please say yes or no.",         "Pain/Discomfort"),
    ("appetite", "How is your appetite today? Please say good, poor, or normal.",               "Appetite"),
    ("exercise", "Did you do any physical activity or exercise today? Please say yes or no.",   "Exercise Today"),
    ("stress",   "On a scale of one to ten, how stressed are you feeling today? Say a number.", "Stress Level"),
]

# ================= STATE MACHINE =================
state = "STARTUP"
greeted = False
health_stage = 0
temp = "--"
pulse = "--"
ecg = "--"
name = "--"
emotion = "--"
health_report = "--"

health_answers = {}
question_index = 0
unknown_attempts = 0
MAX_UNKNOWN = 3
person_detect_count = 0

# ================= CAMERA =================
cap = cv2.VideoCapture(0, cv2.CAP_V4L2)
if not cap.isOpened():
    print("Camera 0 failed, trying index 1...")
    cap = cv2.VideoCapture(1, cv2.CAP_V4L2)

if cap.isOpened():
    print("Camera opened successfully.")
else:
    print("ERROR: Could not open camera.")

speak("Healthcare robot is now active. I am searching for someone I recognise. Please come closer.")

# ================= MAIN LOOP =================
try:
    while True:
        ret, frame = cap.read()
        if not ret:
            print("Frame read failed, retrying...")
            continue

        distance = get_distance()
        print("STATE:", state, "| Distance:", distance)

        packet = json.dumps({
            "distance": distance,
            "temp": temp,
            "pulse": pulse,
            "ecg": ecg,
            "name": name,
            "emotion": emotion,
            "state": state,
            "sleep": health_answers.get("sleep", "--"),
            "water": health_answers.get("water", "--"),
            "pain": health_answers.get("pain", "--"),
            "appetite": health_answers.get("appetite", "--"),
            "exercise": health_answers.get("exercise", "--"),
            "stress": health_answers.get("stress", "--"),
            "health_report": health_report
        }) + "\n"

        try:
            conn.sendall(packet.encode())
        except Exception:
            pass

        command = ""
        try:
            data = conn.recv(1024).decode("utf-8", errors="ignore")
            if data:
                command = data.strip().lower()
                print("COMMAND RECEIVED:", repr(command))
        except BlockingIOError:
            pass
        except Exception as e:
            print("RECV ERROR:", e)

        if state == "STARTUP":
            state = "SEARCH"

        elif state == "SEARCH":
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            resized_p = cv2.resize(rgb, (p_w, p_h))
            input_data = np.expand_dims(resized_p, axis=0).astype(np.uint8)

            person_model.set_tensor(p_in[0]["index"], input_data)
            person_model.invoke()

            boxes = person_model.get_tensor(p_out[0]["index"])[0]
            classes = person_model.get_tensor(p_out[1]["index"])[0]
            scores = person_model.get_tensor(p_out[2]["index"])[0]

            best_idx = -1
            best_score = 0.0

            for i in range(len(scores)):
                if int(classes[i]) == 0 and scores[i] > PERSON_DETECTION_THRESHOLD:
                    if scores[i] > best_score:
                        best_score = scores[i]
                        best_idx = i

            if best_idx != -1:
                i = best_idx
                ymin, xmin, ymax, xmax = boxes[i]
                h, w, _ = frame.shape
                xmin = int(xmin * w)
                xmax = int(xmax * w)
                ymin = int(ymin * h)
                ymax = int(ymax * h)

                cv2.rectangle(frame, (xmin, ymin), (xmax, ymax), (0, 255, 0), 2)
                cv2.putText(frame, f"Person {best_score:.2f}", (xmin, max(ymin - 10, 20)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

                person_detect_count += 1
                print("Person detected. Score:", best_score, "| Count:", person_detect_count)

                if person_detect_count >= PERSON_CONFIRM_FRAMES:
                    if distance <= DISTANCE_TRIGGER:
                        stop_motors()
                        speak("I can see someone. Let me check if I recognise you.")
                        state = "INTERACT"
                        greeted = False
                        unknown_attempts = 0
                        person_detect_count = 0
                    else:
                        person_center = (xmin + xmax) // 2
                        frame_center = w // 2
                        margin = 70

                        if distance <= SAFETY_STOP:
                            stop_motors()
                        elif person_center < frame_center - margin:
                            turn_left()
                        elif person_center > frame_center + margin:
                            turn_right()
                        else:
                            move_forward()
                else:
                    stop_motors()
            else:
                person_detect_count = 0
                stop_motors()

        elif state == "INTERACT":
            stop_motors()

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            locations = face_recognition.face_locations(rgb)
            encodings = face_recognition.face_encodings(rgb, locations)

            print("Faces detected:", len(locations))

            if encodings and not greeted:
                face_encoding = encodings[0]

                if len(known_encodings) == 0:
                    print("No known faces stored.")
                    speak("No registered patient data is available.")
                    state = "SEARCH"
                    greeted = False
                else:
                    face_distances = face_recognition.face_distance(known_encodings, face_encoding)
                    best_match_index = np.argmin(face_distances)
                    best_distance = face_distances[best_match_index]

                    print("Best face distance:", best_distance)

                    if best_distance < FACE_MATCH_THRESHOLD:
                        name = known_names[best_match_index]
                        emotion = detect_emotion(frame)
                        health_answers = {}
                        question_index = 0
                        health_report = "--"
                        temp = ecg = pulse = "--"

                        print("Recognised:", name, "| Emotion:", emotion)

                        greeting = gemini_emotion_chat(name, emotion)
                        speak(greeting)
                        time.sleep(0.5)
                        speak("Whenever you are ready, say yes buddy to start your health check.")
                        greeted = True
                        state = "WAIT_CONFIRM"
                    else:
                        unknown_attempts += 1
                        print("Unknown face. Attempt:", unknown_attempts, "| Distance:", best_distance)

                        unknown_responses = [
                            "Sorry, I do not recognise you. I am only authorised to assist registered patients.",
                            "I am afraid I cannot identify you. Please register with the healthcare system first.",
                            "Hmm, your face is not in my database. Please contact the administrator to register.",
                        ]
                        speak(unknown_responses[(unknown_attempts - 1) % len(unknown_responses)])

                        if unknown_attempts >= MAX_UNKNOWN:
                            speak("I was unable to recognise anyone. Resuming search.")
                            state = "SEARCH"
                            greeted = False

        elif state == "WAIT_CONFIRM":
            stop_motors()
            print("WAIT_CONFIRM command =", repr(command))
            if "yes buddy" in command:
                speak("Perfect. Let us begin your health check. I will first take some sensor readings and then ask you a few quick health questions.")
                health_stage = 1
                question_index = 0
                state = "HEALTH_CHECK"

        elif state == "HEALTH_CHECK":
            stop_motors()

            if health_stage == 1:
                speak("First, please place the temperature sensor properly and say check temperature when ready.")
                health_stage = 2

            elif health_stage == 2 and "check temperature" in command:
                speak("Taking temperature reading now. Please hold still.")
                temp = read_temperature()
                print("Temperature:", temp)
                speak("Got it. Temperature recorded as " + str(temp) + ".")
                health_stage = 3

            elif health_stage == 3:
                speak("Now please attach the pulse sensor and say check pulse when ready.")
                health_stage = 4

            elif health_stage == 4 and "check pulse" in command:
                speak("Measuring your pulse.")
                pulse = read_pulse()
                print("Pulse:", pulse)
                speak("Pulse recorded as " + str(pulse) + ".")
                health_stage = 5

            elif health_stage == 5:
                speak("Now please attach the ECG electrodes and say check ecg when ready.")
                health_stage = 6

            elif health_stage == 6 and "check ecg" in command:
                speak("Recording your ECG. Please stay still and breathe normally.")
                ecg = read_ecg()
                print("ECG:", ecg)
                speak("ECG recorded successfully. Result is " + str(ecg) + ". Now I have a few quick health questions for you. Please answer each one clearly.")
                health_stage = 7
                question_index = 0

            elif health_stage == 7:
                if question_index < len(HEALTH_QUESTIONS):
                    q_key, q_text, q_label = HEALTH_QUESTIONS[question_index]
                    speak(q_text)
                    health_stage = 8
                else:
                    speak("Thank you for answering all my questions, " + name + ". Let me analyse your health data now.")
                    time.sleep(1)

                    health_report = gemini_health_report(
                        name,
                        temp,
                        pulse,
                        ecg,
                        {
                            HEALTH_QUESTIONS[i][2]: health_answers.get(HEALTH_QUESTIONS[i][0], "not answered")
                            for i in range(len(HEALTH_QUESTIONS))
                        }
                    )

                    speak(health_report)
                    speak("Your health session is complete. Take care, " + name + ". Have a wonderful day.")
                    state = "DONE"

            elif health_stage == 8:
                if command:
                    q_key, q_text, q_label = HEALTH_QUESTIONS[question_index]
                    health_answers[q_key] = command
                    print("Q:", q_text, "| A:", command)
                    speak("Thank you.")
                    question_index += 1
                    health_stage = 7

        elif state == "DONE":
            stop_motors()
            time.sleep(10)
            speak("Healthcare robot resuming search. Looking for the next patient.")
            state = "SEARCH"
            greeted = False
            health_answers = {}
            question_index = 0
            name = "--"
            emotion = "--"
            temp = "--"
            pulse = "--"
            ecg = "--"
            health_report = "--"

        cv2.imshow("Healthcare Robot", frame)

        if cv2.waitKey(1) == 27:
            break

except KeyboardInterrupt:
    print("Program stopped by user.")

finally:
    stop_motors()
    cap.release()
    GPIO.cleanup()
    cv2.destroyAllWindows()
