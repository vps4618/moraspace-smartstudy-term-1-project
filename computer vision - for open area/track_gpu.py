from ultralytics import YOLO
import cv2
import os
import time
import requests          # NEW
import threading         # NEW

# Change this to your local http://127.0.0.1:8000/api/occupancy/sync when testing locally
API_URL = "https://smartstudy-backend-psi.vercel.app/api/occupancy/sync"
API_KEY = os.getenv("API_KEY")

def send_update(current_count):
    try:
        # We use max(0, current_count) to ensure we never send a negative number
        payload = {"area_id": "centra", "student_count": max(0, current_count)}
        requests.post(API_URL, json=payload, headers={"X-API-Key": API_KEY}, timeout=3)
    except Exception as e:
        print(f"Backend sync failed: {e}")

# Load YOLO model
model = YOLO("yolo11n.pt")

# Open camera
cap = cv2.VideoCapture(0)

# Camera resolution
width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

# Two vertical lines
line1_x = width // 3
line2_x = 2 * width // 3

# People count
count = 0

# Store the previous side of each tracked person
person_positions = {}

# FPS calculation
prev_time = time.time()

while True:

    ret, frame = cap.read()

    if not ret:
        break

    # YOLO tracking using RTX 4050
    results = model.track(
        frame,
        persist=True,
        classes=[0],       # Only detect people
        device=0,          # NVIDIA GPU
        verbose=False
    )

    annotated = results[0].plot()

    # Draw line 1
    cv2.line(
        annotated,
        (line1_x, 0),
        (line1_x, height),
        (255, 0, 0),
        2
    )

    # Draw line 2
    cv2.line(
        annotated,
        (line2_x, 0),
        (line2_x, height),
        (0, 0, 255),
        2
    )

    # Check whether tracking IDs exist
    if results[0].boxes.id is not None:

        boxes = results[0].boxes.xyxy.cpu().numpy()
        ids = results[0].boxes.id.cpu().numpy().astype(int)

        for box, person_id in zip(boxes, ids):

            x1, y1, x2, y2 = box

            # Person center
            center_x = int((x1 + x2) / 2)
            center_y = int((y1 + y2) / 2)

            # Determine which region the person is in
            if center_x < line1_x:
                current_side = "left"

            elif center_x > line2_x:
                current_side = "right"

            else:
                current_side = "middle"

            # First time seeing this person
            if person_id not in person_positions:

                person_positions[person_id] = current_side

            else:

                previous_side = person_positions[person_id]
                # LEFT -> RIGHT
                if previous_side == "left" and current_side == "right":

                    count += 1
                    print(f"ID {person_id}: LEFT -> RIGHT | Count = {count}")
                    
                    # NEW: Send the updated count to the backend
                    threading.Thread(target=send_update, args=(count,), daemon=True).start()

                # RIGHT -> LEFT
                elif previous_side == "right" and current_side == "left":

                    count -= 1
                    print(f"ID {person_id}: RIGHT -> LEFT | Count = {count}")
                    
                    # NEW: Send the updated count to the backend
                    threading.Thread(target=send_update, args=(count,), daemon=True).start()

                # Update position
                if current_side != "middle":
                    person_positions[person_id] = current_side

            # Draw center point
            cv2.circle(
                annotated,
                (center_x, center_y),
                5,
                (0, 255, 255),
                -1
            )

            # Draw tracking ID
            cv2.putText(
                annotated,
                f"ID {person_id}",
                (int(x1), int(y1) - 10),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (0, 255, 255),
                2
            )

    # Calculate FPS
    current_time = time.time()
    fps = 1 / (current_time - prev_time)
    prev_time = current_time

    # Display count
    cv2.putText(
        annotated,
        f"Count: {count}",
        (20, 50),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.2,
        (0, 255, 0),
        3
    )

    # Display FPS
    cv2.putText(
        annotated,
        f"FPS: {fps:.1f}",
        (20, 90),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.8,
        (0, 255, 255),
        2
    )

    # Display camera
    cv2.imshow("GPU People Tracker", annotated)

    # Press Q to quit
    if cv2.waitKey(1) & 0xFF == ord("q"):
        break

cap.release()
cv2.destroyAllWindows()