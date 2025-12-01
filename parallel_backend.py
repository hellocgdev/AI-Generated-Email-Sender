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
    "LOGO": "Talrn_logo.png",
    "CID": "talrn_logo",
    "LIMIT": 150, "WINDOW": 3600
}

LEADERS_CONFIG = {
    "SERVER": "t.tryleadersfirst.com", "PORT": 465,
    "USER": "reach@t.tryleadersfirst.com", "PASS": "k2fLLmU3[.+?B1Hh",
    "NAME": "Leadersfirst", 
    "LOGO": "Talrn_logo.png", 
    "CID": "leaders_logo",
    "LIMIT": 150, "WINDOW": 3600
}

app = Flask(__name__)
CORS(app)
logging.basicConfig(level=logging.INFO)

# --- GLOBAL STATE ---
# Moved trackers here so the /stats route can access them
talrn_queue = []
leaders_queue = []
t_times = [] # Rate limit tracker for Talrn
l_times = [] # Rate limit tracker for Leaders
recent_logs = [] 

# Locks for thread safety
queue_lock = threading.Lock()
log_lock = threading.Lock()
running = True

def get_path(f): 
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), f)

def add_log(msg, type="info", brand="System"):
    timestamp = time.strftime("%H:%M:%S")
    log_entry = {"time": timestamp, "msg": msg, "type": type, "brand": brand}
    print(f"[{timestamp}] {brand}: {msg}")
    with log_lock:
        if len(recent_logs) > 100: recent_logs.pop(0)
        recent_logs.append(log_entry)

def check_rate_limit(sent_times, limit, window):
    now = time.time()
    # Remove timestamps older than the window
    while sent_times and sent_times[0] < (now - window):
        sent_times.pop(0)
    return len(sent_times) < limit

def process_email(task, config, tracker):
    subject_preview = task['subject'][:20] + "..." if len(task['subject']) > 20 else task['subject']
    
    try:
        # Rate Limit Check
        if not check_rate_limit(tracker, config['LIMIT'], config['WINDOW']):
            add_log(f"Rate limit hit ({config['LIMIT']}/hr). Pausing...", "warning", config['NAME'])
            return False

        # Email Construction
        msg = MIMEMultipart('related')
        msg['Subject'] = task['subject']
        msg['From'] = formataddr((config['NAME'], config['USER']))
        msg['To'] = task['recipient']
        if task.get('reply_to'): 
            msg.add_header('Reply-To', task['reply_to'])

        alt = MIMEMultipart('alternative')
        msg.attach(alt)
        
        # --- HTML Body Processing (FIXED) ---
        # We unconditionally replace BOTH potential logo placeholders with the CURRENT brand's CID.
        # This prevents errors if a user copies a Leaders template into Talrn or vice versa.
        body_html = task['body']
        current_cid = config["CID"]
        
        # Replace common placeholders
        body_html = body_html.replace('cid:talrn_logo', f'cid:{current_cid}')
        body_html = body_html.replace('cid:leaders_logo', f'cid:{current_cid}')
        
        alt.attach(MIMEText(body_html, 'html' if task['is_html'] else 'plain', 'utf-8'))

        # --- Logo Attachment ---
        logo_path = get_path(config['LOGO'])
        if os.path.exists(logo_path):
            try:
                with open(logo_path, 'rb') as f:
                    img = MIMEImage(f.read())
                    # The Content-ID must match what is in the HTML (brackets are required by standards)
                    img.add_header('Content-ID', f"<{current_cid}>")
                    img.add_header('Content-Disposition', 'inline', filename=config['LOGO'])
                    msg.attach(img)
            except Exception as e:
                add_log(f"Logo attachment error: {e}", "warning", config['NAME'])
        else:
            add_log(f"Logo file not found: {config['LOGO']}", "warning", config['NAME'])

        # --- Sending ---
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(config['SERVER'], config['PORT'], context=ctx) as s:
            s.login(config['USER'], config['PASS'])
            s.send_message(msg)
        
        # Track success
        tracker.append(time.time())
        add_log(f"Sent \"{subject_preview}\" to {task['recipient']}", "success", config['NAME'])
        return True

    except Exception as e:
        add_log(f"Failed sending to {task['recipient']}: {e}", "error", config['NAME'])
        # We return True here to discard the failed task so it doesn't block the queue forever.
        # In a more advanced system, you might implement a retry limit.
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
                # Rate limit hit, wait a bit before checking again
                time.sleep(10)
        else:
            # Empty queue, idle wait
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
    
    # --- Validation (FIXED) ---
    if not d or 'recipients' not in d or 'subject' not in d:
        return jsonify({"status": "Error", "msg": "Missing recipients or subject"}), 400

    brand = d.get('brand', 'Talrn')
    # Clean recipient list (remove empty strings/spaces)
    recipients = [r.strip() for r in d.get('recipients', '').split(',') if r.strip()]
    
    if not recipients:
        return jsonify({"status": "Error", "msg": "No valid recipients found"}), 400

    count = 0
    with queue_lock:
        for r in recipients:
            task = {
                'recipient': r, 
                'subject': d.get('subject'),
                'body': d.get('email_body'), 
                'is_html': d.get('is_html', True),
                'reply_to': d.get('reply_to'), 
                'brand': brand
            }
            
            if str(brand).lower() == 'leadersfirst': 
                leaders_queue.append(task)
            else: 
                talrn_queue.append(task)
            count += 1
    
    add_log(f"Queued {count} emails", "info", brand)
    return jsonify({"status": "Queued", "msg": f"Queued {count} emails"})

@app.route('/stats', methods=['GET'])
def stats():
    # --- Statistics Logic (FIXED) ---
    now = time.time()
    
    # Calculate emails sent in the sliding window (Last Hour)
    t_sent = len([t for t in t_times if t > (now - TALRN_CONFIG['WINDOW'])])
    l_sent = len([t for t in l_times if t > (now - LEADERS_CONFIG['WINDOW'])])

    with queue_lock:
        t_queue = len(talrn_queue)
        l_queue = len(leaders_queue)

    return jsonify({
        "status": "Running",
        "talrn": {
            "queue": t_queue,
            "sent_last_hour": t_sent,
            "limit": TALRN_CONFIG['LIMIT']
        },
        "leadersfirst": {
            "queue": l_queue,
            "sent_last_hour": l_sent,
            "limit": LEADERS_CONFIG['LIMIT']
        }
    })

if __name__ == '__main__':
    # Start Workers
    threading.Thread(target=worker, args=(talrn_queue, TALRN_CONFIG, t_times), daemon=True).start()
    threading.Thread(target=worker, args=(leaders_queue, LEADERS_CONFIG, l_times), daemon=True).start()
    
    add_log("System started. Workers ready.", "info", "System")
    print("🚀 Parallel Server running on http://localhost:5000")
    
    # Run Flask
    app.run(port=5000, threaded=True)