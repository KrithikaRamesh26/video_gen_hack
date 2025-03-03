from flask import Flask, request, jsonify
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import uuid
import boto3
import os
# Load environment variables
load_dotenv()

app = Flask(__name__)

# Configure AWS credentials and S3 bucket
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")

S3_BUCKET_NAME = 'videosshifthealth'
S3_GOOD_INPUT_FOLDER = 'sourcevideosgood/'
S3_BAD_INPUT_FOLDER = 'sourcevideosbad/'
S3_GOOD_OUTPUT_FOLDER = 'outputvideosgood/'
S3_BAD_OUTPUT_FOLDER = 'outputvideosbad/'
S3_REGION = 'ap-southeast-1'  # e.g., 'us-east-1'

s3 = boto3.client('s3', aws_access_key_id=AWS_ACCESS_KEY_ID, aws_secret_access_key=AWS_SECRET_ACCESS_KEY, region_name=S3_REGION)

def get_random_video_from_s3(folder):
    response = s3.list_objects_v2(Bucket=S3_BUCKET_NAME, Prefix=folder)
    if 'Contents' not in response:
        return None
    video_files = [obj['Key'] for obj in response['Contents'] if obj['Key'].endswith('.mp4')]
    if not video_files:
        return None
    random_video_key = np.random.choice(video_files)
    local_video_path = f"downloaded_{uuid.uuid4()}.mp4"
    s3.download_file(S3_BUCKET_NAME, random_video_key, local_video_path)
    return local_video_path

def create_transparent_image(user_values, output_path="overlay.png"):
    width, height = 400, len(user_values) * 100
    image = Image.new("RGBA", (width, height), (255, 255, 255, 100))
    draw = ImageDraw.Draw(image)

    try:
        font = ImageFont.truetype("arial.ttf", 30)
    except IOError:
        font = ImageFont.load_default()

    y_offset = 30
    for label, value in user_values.items():
        text = f"{label}: {value}"
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width, text_height = bbox[2] - bbox[0], bbox[3] - bbox[1]
        text_x = (width - text_width) // 2
        draw.text((text_x, y_offset), text, fill=(0, 0, 0, 255), font=font)
        y_offset += 80

    image.save(output_path, "PNG")
    print(f"Overlay image saved as {output_path}")
    return output_path, width, height

def get_overlay_position(video_width, video_height, overlay_width, overlay_height):
    return video_width - overlay_width - 20, 20

def overlay_image_on_video(video_path, image_path, output_path):
    cap = cv2.VideoCapture(video_path)
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = int(cap.get(cv2.CAP_PROP_FPS))

    overlay = cv2.imread(image_path, cv2.IMREAD_UNCHANGED)
    overlay_h, overlay_w = overlay.shape[:2]

    x, y = get_overlay_position(frame_width, frame_height, overlay_w, overlay_h)

    out = cv2.VideoWriter(output_path, fourcc, fps, (frame_width, frame_height))

    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break

        if overlay.shape[2] == 4:
            alpha = overlay[:, :, 3] / 255.0
            overlay_rgb = overlay[:, :, :3]
        else:
            alpha = np.ones((overlay_h, overlay_w))
            overlay_rgb = overlay

        roi = frame[y:y+overlay_h, x:x+overlay_w]

        if roi.shape[0] != overlay_h or roi.shape[1] != overlay_w:
            break

        blended = cv2.addWeighted(roi, 1 - np.mean(alpha), overlay_rgb, np.mean(alpha), 0)
        frame[y:y+overlay_h, x:x+overlay_w] = blended

        out.write(frame)

    cap.release()
    out.release()
    print(f"Video with overlay saved as {output_path}")

@app.route('/create_output_video', methods=['POST'])
def create_output_video():
    unique_id = str(uuid.uuid4())
    good_video_path = get_random_video_from_s3(S3_GOOD_INPUT_FOLDER)
    bad_video_path = get_random_video_from_s3(S3_BAD_INPUT_FOLDER)
    
    if not good_video_path or not bad_video_path:
        return jsonify({"error": "No videos available in S3 folders"}), 400
    
    image_path = f"overlay_{unique_id}.png"
    good_video_output_path = f"good_video_{unique_id}.mp4"
    bad_video_output_path = f"bad_video_{unique_id}.mp4"
    good_s3_output_key = f"{S3_GOOD_OUTPUT_FOLDER}{good_video_output_path}"
    bad_s3_output_key = f"{S3_BAD_OUTPUT_FOLDER}{bad_video_output_path}"

    name = request.form.get('name', 'Default Name')
    age = request.form.get('age', 'Default Age')
    gender = request.form.get('gender', 'Default Gender')
    nationality = request.form.get('nationality', 'Default Nationality')

    health_vitals = {
        "Blood Pressure": request.form.get('blood_pressure', '120/80'),
        "Pulse": request.form.get('pulse', '70')
    }

    for key, value in request.form.items():
        if key.startswith('health_vitals_'):
            health_vitals[key.replace('health_vitals_', '')] = value

    user_values = {
        "Name": name,
        "Age": age,
        "Gender": gender,
        "Nationality": nationality,
        **health_vitals
    }

    create_transparent_image(user_values, image_path)
    overlay_image_on_video(good_video_path, image_path, good_video_output_path)
    overlay_image_on_video(bad_video_path, image_path, bad_video_output_path)

    s3.upload_file(good_video_output_path, S3_BUCKET_NAME, good_s3_output_key)
    s3.upload_file(bad_video_output_path, S3_BUCKET_NAME, bad_s3_output_key)

    os.remove(good_video_path)
    os.remove(bad_video_path)
    os.remove(image_path)
    os.remove(good_video_output_path)
    os.remove(bad_video_output_path)

    good_s3_url = f"https://{S3_BUCKET_NAME}.s3.{S3_REGION}.amazonaws.com/{good_s3_output_key}"
    bad_s3_url = f"https://{S3_BUCKET_NAME}.s3.{S3_REGION}.amazonaws.com/{bad_s3_output_key}"

    return jsonify({"good_video": good_s3_url, "bad_video": bad_s3_url})

if __name__ == '__main__':
    app.run(debug=True)