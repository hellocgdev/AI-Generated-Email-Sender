import os
import time
import threading
import logging
import smtplib
import ssl
import json
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
from email.utils import formataddr
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS

# --- CONFIGURATION ---
TALRN_CONFIG = {
    "SERVER": "b.trytalrn.com", "PORT": 465,
    "USER": "hire@b.trytalrn.com", "PASS": "fMVl36h1g}KqAfR2",
    "NAME": "Talrn", 
    "LOGO": "Talrn logo.png",
    "CID": "talrn_logo",
    "LIMIT": 150, "WINDOW": 3600
}

LEADERS_CONFIG = {
    "SERVER": "t.tryleadersfirst.com", "PORT": 465,
    "USER": "reach@t.tryleadersfirst.com", "PASS": "k2fLLmU3[.+?B1Hh",
    "NAME": "Leadersfirst", 
    "LOGO": "leaderslogo.png", 
    "CID": "leaders_logo",
    "LIMIT": 150, "WINDOW": 3600
}

app = Flask(__name__)
CORS(app)
logging.basicConfig(level=logging.INFO)

# Global State
talrn_queue = []
leaders_queue = []
recent_logs = [] 
queue_lock = threading.Lock()
log_lock = threading.Lock()
running = True

def get_path(f): return os.path.join(os.path.dirname(os.path.abspath(__file__)), f)

def add_log(msg, type="info", brand="System"):
    timestamp = time.strftime("%H:%M:%S")
    log_entry = {"time": timestamp, "msg": msg, "type": type, "brand": brand}
    print(f"[{timestamp}] {brand}: {msg}")
    with log_lock:
        if len(recent_logs) > 100: recent_logs.pop(0)
        recent_logs.append(log_entry)

def check_rate_limit(sent_times, limit, window):
    now = time.time()
    while sent_times and sent_times[0] < (now - window):
        sent_times.pop(0)
    return len(sent_times) < limit

def process_email(task, config, tracker):
    subject_preview = task['subject'][:20] + "..." if len(task['subject']) > 20 else task['subject']
    
    try:
        if not check_rate_limit(tracker, config['LIMIT'], config['WINDOW']):
            add_log(f"Rate limit hit ({config['LIMIT']}/hr). Pausing...", "warning", config['NAME'])
            return False

        msg = MIMEMultipart('related')
        msg['Subject'] = task['subject']
        msg['From'] = formataddr((config['NAME'], config['USER']))
        msg['To'] = task['recipient']
        if task.get('reply_to'): msg.add_header('Reply-To', task['reply_to'])

        alt = MIMEMultipart('alternative')
        msg.attach(alt)
        
        # HTML Body processing
        body_html = task['body']
        if config['NAME'] == 'Leadersfirst':
             body_html = body_html.replace('cid:talrn_logo', f'cid:{config["CID"]}')
        elif config['NAME'] == 'Talrn':
             body_html = body_html.replace('cid:leaders_logo', f'cid:{config["CID"]}')
        
        alt.attach(MIMEText(body_html, 'html' if task['is_html'] else 'plain', 'utf-8'))

        # Logo Attachment
        logo_path = get_path(config['LOGO'])
        if os.path.exists(logo_path):
            try:
                with open(logo_path, 'rb') as f:
                    img = MIMEImage(f.read())
                    img.add_header('Content-ID', f"<{config['CID']}>")
                    img.add_header('Content-Disposition', 'inline', filename=config['LOGO'])
                    msg.attach(img)
            except Exception as e:
                add_log(f"Logo error: {e}", "warning", config['NAME'])
        else:
            add_log(f"Logo file missing: {config['LOGO']}", "warning", config['NAME'])

        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(config['SERVER'], config['PORT'], context=ctx) as s:
            s.login(config['USER'], config['PASS'])
            s.send_message(msg)
        
        tracker.append(time.time())
        # UPDATED LOG MESSAGE: Now includes Subject
        add_log(f"Sent \"{subject_preview}\" to {task['recipient']}", "success", config['NAME'])
        return True

    except Exception as e:
        add_log(f"Failed \"{subject_preview}\" to {task['recipient']}: {e}", "error", config['NAME'])
        return True

def worker(queue, config, tracker):
    print(f"🔧 Worker started for {config['NAME']}")
    while running:
        task = None
        with queue_lock:
            if queue: task = queue[0]
        
        if task:
            if process_email(task, config, tracker):
                with queue_lock: queue.pop(0)
            else:
                time.sleep(10)
        else:
            time.sleep(1)

# --- ROUTES ---
@app.route('/')
def home(): return send_file(get_path('email_hub.html'))

@app.route('/tool')
def tool(): return send_file(get_path('email_tool.html'))

@app.route('/get-new-logs')
def get_logs():
    with log_lock:
        return jsonify(recent_logs)

@app.route('/send-email', methods=['POST'])
def send():
    d = request.json
    brand = d.get('brand', 'Talrn')
    recipients = d.get('recipients', '').split(',')
    count = 0
    
    with queue_lock:
        for r in recipients:
            if r.strip():
                task = {
                    'recipient': r.strip(), 'subject': d.get('subject'),
                    'body': d.get('email_body'), 'is_html': d.get('is_html'),
                    'reply_to': d.get('reply_to'), 'brand': brand
                }
                if str(brand).lower() == 'leadersfirst': 
                    leaders_queue.append(task)
                else: 
                    talrn_queue.append(task)
                count += 1
    
    add_log(f"Queued {count} emails", "info", brand)
    return jsonify({"status": "Queued", "msg": f"Queued {count}"})

@app.route('/stats', methods=['GET'])
def stats(): return jsonify([])

if __name__ == '__main__':
    t_times, l_times = [], []
    threading.Thread(target=worker, args=(talrn_queue, TALRN_CONFIG, t_times), daemon=True).start()
    threading.Thread(target=worker, args=(leaders_queue, LEADERS_CONFIG, l_times), daemon=True).start()
    
    add_log("System started. Workers ready.", "info", "System")
    print("🚀 Parallel Server running on http://localhost:5000")
    app.run(port=5000, threaded=True)
