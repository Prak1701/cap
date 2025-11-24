import os
import os
import json
import csv
import io
import base64
import hashlib
import datetime
import random
import uuid
from functools import wraps
from flask import Flask, request, jsonify, send_file
from werkzeug.security import generate_password_hash, check_password_hash
import jwt
from dotenv import load_dotenv
import qrcode
from PIL import Image, ImageDraw, ImageFont

# Optional MongoDB
try:
    from pymongo import MongoClient
    from pymongo.errors import ServerSelectionTimeoutError
    MONGO_AVAILABLE = True
except Exception:
    MONGO_AVAILABLE = False

load_dotenv()

# Legacy JSON fallback
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
USERS_FILE = os.path.join(DATA_DIR, "users.json")
STUDENTS_FILE = os.path.join(DATA_DIR, "students.json")
PROOFS_FILE = os.path.join(DATA_DIR, "proofs.json")
VERIF_FILE = os.path.join(DATA_DIR, "verifications.json")
TEMPLATES_FILE = os.path.join(DATA_DIR, "templates.json")
CERTS_FILE = os.path.join(DATA_DIR, "certificates.json")
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")

CERTS_DIR = os.path.join(DATA_DIR, "certs")
TEMPLATES_DIR = os.path.join(DATA_DIR, "templates")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(CERTS_DIR, exist_ok=True)
os.makedirs(TEMPLATES_DIR, exist_ok=True)

# Initialize config with auto-generated secrets
def load_or_create_config():
    config = {}
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            config = json.load(f)

    # Auto-generate JWT_SECRET if not provided via environment or config
    if "jwt_secret" not in config:
        config["jwt_secret"] = uuid.uuid4().hex + uuid.uuid4().hex

    # Save config
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)

    return config

config = load_or_create_config()

# Get JWT_SECRET from environment or config
JWT_SECRET = os.getenv("JWT_SECRET") or config.get("jwt_secret")

# Get MONGO_URI from environment (no auto-generation for this)
MONGO_URI = os.getenv("MONGO_URI", "")

for path, default in [
    (USERS_FILE, []),
    (STUDENTS_FILE, []),
    (PROOFS_FILE, []),
    (VERIF_FILE, {}),
    (TEMPLATES_FILE, {}),
    (CERTS_FILE, []),
]:
    if not os.path.exists(path):
        with open(path, "w") as f:
            json.dump(default, f)

# Initialize Mongo client if URI provided and pymongo available
mongo_client = None
db = None
if MONGO_AVAILABLE and MONGO_URI:
    try:
        mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
        # trigger server selection
        mongo_client.server_info()
        db = mongo_client.get_database()
        print("Connected to MongoDB")
    except ServerSelectionTimeoutError:
        print("Could not connect to MongoDB, falling back to JSON storage")
        mongo_client = None
        db = None
else:
    if not MONGO_AVAILABLE:
        print("pymongo not installed; using JSON fallback")
    elif not MONGO_URI:
        print("MONGO_URI not set; using JSON fallback")

# Allowed university email domain (strict match)
ALLOWED_UNIVERSITY_DOMAIN = os.getenv("UNIVERSITY_ALLOWED_DOMAIN", "st.niituniversity.in").strip().lower()

def email_domain_ok(email: str) -> bool:
    if not email:
        return False
    email = email.strip().lower()
    if "@" not in email:
        return False
    _, domain = email.rsplit("@", 1)
    domain = domain.strip()
    if not domain:
        return False
    return domain == ALLOWED_UNIVERSITY_DOMAIN

app = Flask(__name__)

# CORS Configuration
@app.after_request
def after_request(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type,Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,PUT,DELETE,OPTIONS,PATCH"
    response.headers["Access-Control-Max-Age"] = "3600"
    return response

# Storage helpers (abstracted to allow Mongo or JSON)
def read_json(path):
    with open(path, "r") as f:
        return json.load(f)

def write_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

# Users
def get_all_users():
    if db is not None:
        return list(db.users.find({}, {"_id": 0}))
    return read_json(USERS_FILE)

def get_collection_for_role(role):
    """Get the MongoDB collection name for a role."""
    if db is not None:
        if role == "university":
            return db.universities
        elif role == "student":
            return db.students
        elif role == "employer":
            return db.employers
    return None

def find_user_by_email(email):
    if db is not None:
        # Search in all role-based collections
        for collection in [db.universities, db.students, db.employers]:
            user = collection.find_one({"email": email}, {"_id": 0})
            if user:
                return user
        return None
    users = get_all_users()
    return next((u for u in users if u["email"] == email), None)

def find_user_by_id(user_id):
    if db is not None:
        # Search in all role-based collections
        for collection in [db.universities, db.students, db.employers]:
            user = collection.find_one({"id": int(user_id)}, {"_id": 0})
            if user:
                return user
        return None
    users = get_all_users()
    return next((u for u in users if int(u.get("id", -1)) == int(user_id)), None)

def add_user(user):
    user.setdefault("verified", False)
    role = user.get("role", "student")

    if db is not None:
        collection = get_collection_for_role(role)
        if collection is not None:
            last = collection.find_one(sort=[("id", -1)])
            user["id"] = (last["id"] + 1) if last and "id" in last else 1
            collection.insert_one(user)
            _u = user.copy()
            _u.pop("password", None)
            return _u

    users = get_all_users()
    user["id"] = len(users) + 1
    users.append(user)
    write_json(USERS_FILE, users)
    _u = user.copy()
    _u.pop("password", None)
    return _u

# Students
def get_all_students():
    if db is not None:
        return list(db.students.find({}, {"_id": 0}))
    return read_json(STUDENTS_FILE)

def add_student_record(record):
    if db is not None:
        last = db.students.find_one(sort=[("id", -1)])
        record["id"] = (last["id"] + 1) if last and "id" in last else 1
        db.students.insert_one(record)
        return record
    students = get_all_students()
    # Find the maximum ID and add 1, or start at 1 if no students exist
    max_id = max([s.get("id", 0) for s in students], default=0)
    record["id"] = max_id + 1
    students.append(record)
    write_json(STUDENTS_FILE, students)
    return record

def find_student_by_id(student_id):
    if db is not None:
        return db.students.find_one({"id": student_id}, {"_id": 0})
    students = get_all_students()
    return next((s for s in students if s["id"] == student_id), None)

def find_student_by_email(email):
    """Find student by email (case-insensitive, checks in student data)."""
    if not email:
        return None
    email_lower = str(email).strip().lower()
    students = get_all_students()
    for student in students:
        student_data = student.get("data", {})
        student_email = extract_email_from_student_data(student_data)
        if student_email and student_email == email_lower:
            return student
    return None

def update_student_record(student_id, updated_record):
    """Update an existing student record."""
    if db is not None:
        result = db.students.update_one({"id": student_id}, {"$set": updated_record})
        if result.matched_count > 0:
            return db.students.find_one({"id": student_id}, {"_id": 0})
        return None
    students = get_all_students()
    for i, student in enumerate(students):
        if student["id"] == student_id:
            students[i].update(updated_record)
            write_json(STUDENTS_FILE, students)
            return students[i]
    return None

# Proofs
def get_all_proofs():
    if db is not None:
        return list(db.proofs.find({}, {"_id": 0}))
    return read_json(PROOFS_FILE)

def add_proof(proof):
    if db is not None:
        db.proofs.insert_one(proof)
        return proof
    proofs = get_all_proofs()
    proofs.append(proof)
    write_json(PROOFS_FILE, proofs)
    return proof

def get_latest_proof_for_student(student_id):
    if db is not None:
        cursor = db.proofs.find({"student_id": student_id}).sort([("timestamp", -1)])
        doc = cursor.limit(1)
        lst = list(doc)
        return lst[0] if lst else None
    proofs = get_all_proofs()
    for p in reversed(proofs):
        if p.get("student_id") == student_id:
            return p
    return None

# Templates and certificates helpers
def load_templates():
    return read_json(TEMPLATES_FILE)

def save_templates(data):
    write_json(TEMPLATES_FILE, data)

def load_certificates():
    return read_json(CERTS_FILE)

def save_certificates(data):
    write_json(CERTS_FILE, data)

def create_qr_image(payload: dict, size: int = 300) -> Image.Image:
    # payload may be dict or raw token; serialize accordingly
    data = json.dumps(payload) if isinstance(payload, dict) else str(payload)
    qr = qrcode.QRCode(box_size=10, border=2)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGBA")
    img = img.resize((size, size))
    return img

def generate_certificate_image(template_path: str, layout: dict, student: dict, cert_id: int) -> str:
    # Open template
    template = Image.open(template_path).convert("RGBA")
    draw = ImageDraw.Draw(template)

    # Prepare fonts (default and smaller for labels)
    try:
        font_path = os.path.join(os.path.dirname(__file__), "fonts", "DejaVuSans.ttf")
        font_large = ImageFont.truetype(font_path, layout.get("font_size", 36))
        font_small = ImageFont.truetype(font_path, layout.get("font_size_small", 24))
    except Exception:
        font_large = ImageFont.load_default()
        font_small = ImageFont.load_default()

    # Generate signed token for this certificate and student
    token_payload = {"student_id": student.get("id"), "cert_id": cert_id}
    token = make_token(token_payload)

    # Generate QR image containing the token
    qr_img = create_qr_image({"token": token}, size=layout.get("qr_size", 200))

    # Paste QR
    qr_pos = tuple(layout.get("qr_position", (template.width - qr_img.width - 50, template.height - qr_img.height - 50)))
    template.paste(qr_img, qr_pos, qr_img)

    # Get student data
    student_data = student.get("data", {})
    
    # Draw standard fields (name, cert number, date) if positions are specified
    if "name_position" in layout:
        name = student_data.get("name") or student_data.get("Name") or student.get("username") or ""
        name_pos = tuple(layout.get("name_position", (100, template.height - 200)))
        draw.text(name_pos, str(name), font=font_large, fill=(0, 0, 0))

    if "cert_no_position" in layout:
        cert_no = f"CERT-{cert_id:06d}"
        cert_pos = tuple(layout.get("cert_no_position", (100, template.height - 150)))
        draw.text(cert_pos, cert_no, font=font_large, fill=(0, 0, 0))

    if "date_position" in layout:
        date_str = datetime.datetime.utcnow().strftime("%Y-%m-%d")
        date_pos = tuple(layout.get("date_position", (100, template.height - 100)))
        draw.text(date_pos, date_str, font=font_large, fill=(0, 0, 0))

    # Auto-fill ALL CSV fields dynamically
    # Look for field positions in layout (format: "field_name_position" or "field_name")
    # Example: "email_position": [100, 300] or "degree_position": [100, 350]
    field_positions = {}
    for key, value in layout.items():
        if key.endswith("_position") and isinstance(value, list) and len(value) >= 2:
            field_name = key.replace("_position", "")
            field_positions[field_name] = tuple(value)
        elif key.endswith("_position") and isinstance(value, dict):
            # Support for field with label and value positions
            field_name = key.replace("_position", "")
            field_positions[field_name] = value

    # Draw all CSV fields that have positions defined in layout
    for field_name, position in field_positions.items():
        # Skip standard fields already handled above
        if field_name in ["name", "cert_no", "date", "qr"]:
            continue
            
        # Get field value from student data (case-insensitive search)
        field_value = None
        for key in student_data.keys():
            if key.lower().strip() == field_name.lower().strip():
                field_value = student_data[key]
                break
        
        if field_value is not None:
            # Handle position format: [x, y] or {"label": [x, y], "value": [x, y]}
            if isinstance(position, dict):
                # Draw label and value separately
                label_pos = tuple(position.get("label", [0, 0]))
                value_pos = tuple(position.get("value", [0, 0]))
                label_text = field_name.replace("_", " ").title() + ":"
                draw.text(label_pos, label_text, font=font_small, fill=(0, 0, 0))
                draw.text(value_pos, str(field_value), font=font_large, fill=(0, 0, 0))
            elif isinstance(position, (list, tuple)) and len(position) >= 2:
                # Draw value at position
                draw.text(tuple(position[:2]), str(field_value), font=font_large, fill=(0, 0, 0))

    # Save PNG
    cert_filename = f"cert_{cert_id}.png"
    cert_path = os.path.join(CERTS_DIR, cert_filename)
    # Ensure directory exists
    os.makedirs(os.path.dirname(cert_path), exist_ok=True)
    template.save(cert_path)

    # Also create PDF version
    try:
        img_rgb = Image.open(cert_path).convert("RGB")
        pdf_path = os.path.splitext(cert_path)[0] + ".pdf"
        img_rgb.save(pdf_path, "PDF", resolution=100.0)
        # Return relative path from DATA_DIR for consistent storage
        return os.path.relpath(pdf_path, DATA_DIR)
    except Exception as e:
        print(f"PDF generation failed: {e}, using PNG")
        # Return relative path from DATA_DIR
        return os.path.relpath(cert_path, DATA_DIR)

# Auth utils
def make_token(payload):
    payload = payload.copy()
    payload["exp"] = datetime.datetime.utcnow() + datetime.timedelta(days=7)
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")

def verify_token(token):
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=["HS256"]) 
    except Exception:
        return None

def auth_required(role=None):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            auth = request.headers.get("Authorization", "")
            if not auth.startswith("Bearer "):
                return jsonify({"error": "Missing token"}), 401
            token = auth.split(" ", 1)[1]
            data = verify_token(token)
            if not data:
                return jsonify({"error": "Invalid token"}), 401
            user = find_user_by_id(data.get("id"))
            if not user:
                return jsonify({"error": "User not found"}), 401
            if role and user.get("role") != role:
                return jsonify({"error": "Forbidden"}), 403
            if user.get("role") == "university" and not user.get("verified"):
                return jsonify({"error": "University account not verified"}), 403
            request.user = user
            return fn(*args, **kwargs)
        return wrapper
    return decorator

# Verification helpers
def create_verification(email):
    """Generate a verification code and store it."""
    code = str(random.randint(100000, 999999))
    verifications = read_json(VERIF_FILE) if os.path.exists(VERIF_FILE) else {}
    verifications[email] = {"code": code, "timestamp": datetime.datetime.utcnow().isoformat()}
    write_json(VERIF_FILE, verifications)
    return code

def verify_code_for_email(email, code):
    """Verify if the code matches for the email."""
    verifications = read_json(VERIF_FILE) if os.path.exists(VERIF_FILE) else {}
    if email not in verifications:
        return False
    stored = verifications[email]
    if stored["code"] != str(code):
        return False
    # Mark as verified
    del verifications[email]
    write_json(VERIF_FILE, verifications)
    return True

def is_email_verified(email):
    """Check if email has been verified (i.e., code was accepted)."""
    verifications = read_json(VERIF_FILE) if os.path.exists(VERIF_FILE) else {}
    return email not in verifications

# Auth endpoints
@app.route("/auth/send_verification", methods=["POST"])
def send_verification():
    body = request.get_json() or {}
    email = body.get("email")
    if not email:
        return jsonify({"error": "Email required"}), 400
    if not email_domain_ok(email):
        return jsonify({"error": "Invalid university domain"}), 400
    code = create_verification(email)
    print(f"Verification code for {email}: {code}")
    return jsonify({"ok": True, "message": "Verification code generated and (in dev) returned in response.", "code": code})

@app.route("/auth/verify_code", methods=["POST"])
def verify_code():
    body = request.get_json() or {}
    email = body.get("email")
    code = body.get("code")
    if not email or not code:
        return jsonify({"error": "Email and code required"}), 400
    if verify_code_for_email(email, str(code)):
        return jsonify({"ok": True})
    return jsonify({"error": "Invalid or expired code"}), 400

@app.route("/auth/register", methods=["POST"])
def register():
    body = request.get_json() or {}
    username = body.get("username")
    email = body.get("email")
    password = body.get("password")
    role = body.get("role", "student")
    if not username or not email or not password:
        return jsonify({"error": "Missing fields"}), 400
    if find_user_by_email(email):
        return jsonify({"error": "User exists"}), 400

    # Validate student email - reject university domain
    domain = (email or "").split("@").pop().lower()
    if role == "student" and domain == "st.niituniversity.in":
        return jsonify({"error": "Students cannot use university email domain. Use a personal email."}), 400

    verified = True
    if role == "university":
        verified = False
        if not email_domain_ok(email):
            return jsonify({"error": "University signups are restricted to the configured domain"}), 400
        if is_email_verified(email):
            verified = True

    user = {
        "username": username,
        "email": email,
        "password": generate_password_hash(password),
        "role": role,
        "verified": verified,
        "created_at": datetime.datetime.utcnow().isoformat(),
    }
    _u = add_user(user)
    token = make_token({"id": _u["id"], "email": email, "role": role, "verified": _u.get("verified", False)})
    return jsonify({"token": token, "user": {"id": _u["id"], "username": username, "email": email, "role": role, "verified": _u.get("verified", False)}})

@app.route("/auth/login", methods=["POST"])
def login():
    body = request.get_json() or {}
    email = body.get("email")
    password = body.get("password")

    user = find_user_by_email(email)
    if not user or not check_password_hash(user["password"], password):
        return jsonify({"error": "Invalid credentials"}), 401
    token = make_token({"id": user["id"], "email": user["email"], "role": user.get("role", "student"), "verified": user.get("verified", False)})
    return jsonify({"token": token, "user": {"id": user["id"], "username": user["username"], "email": user["email"], "role": user.get("role", "student"), "verified": user.get("verified", False)}})

# Admin: list pending university signups
@app.route("/admin/pending-users", methods=["GET"])
@auth_required(role="admin")
def admin_pending_users():
    users = get_all_users()
    pending = [u for u in users if u.get("role") == "university" and not u.get("verified")]
    return jsonify({"count": len(pending), "users": pending})

# Admin: approve a university account
@app.route("/admin/approve", methods=["POST"])
@auth_required(role="admin")
def admin_approve():
    body = request.get_json() or {}
    user_id = body.get("user_id")
    if not user_id:
        return jsonify({"error": "user_id required"}), 400
    if db:
        res = db.users.update_one({"id": int(user_id)}, {"$set": {"verified": True}})
        if res.matched_count == 0:
            return jsonify({"error": "user not found"}), 404
        return jsonify({"ok": True})
    users = get_all_users()
    found = False
    for u in users:
        if int(u.get("id", -1)) == int(user_id):
            u["verified"] = True
            found = True
            break
    if not found:
        return jsonify({"error": "user not found"}), 404
    write_json(USERS_FILE, users)
    return jsonify({"ok": True})

# Upload certificate template (university)
@app.route("/university/template/upload", methods=["POST"])
@auth_required(role="university")
def upload_template():
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400
    file = request.files["file"]

    # Use default layout if not provided
    # Layout supports auto-filling any CSV field by adding "{field_name}_position": [x, y]
    # Example: "email_position": [100, 400], "degree_position": [100, 450]
    # The system will automatically find and fill the field from CSV data
    default_layout = {
        "qr_position": [1000, 800],  # Bottom-right area
        "qr_size": 150,
        "font_size": 36,
        "font_size_small": 24,
        "name_position": [100, 200],
        "cert_no_position": [100, 260],
        "date_position": [100, 320]
        # Add more field positions like: "email_position": [100, 400], "degree_position": [100, 450], etc.
        # Any CSV column can be auto-filled by adding its position in the layout
    }
    layout_str = request.form.get("layout", None)
    if layout_str:
        try:
            custom_layout = json.loads(layout_str)
            layout = {**default_layout, **custom_layout}
        except Exception:
            layout = default_layout
    else:
        layout = default_layout

    # save file
    ext = os.path.splitext(file.filename)[1] or ".png"
    template_id = str(uuid.uuid4())
    filename = f"template_{template_id}{ext}"
    path = os.path.join(TEMPLATES_DIR, filename)
    file.save(path)

    templates = load_templates()
    templates[template_id] = {"filename": filename, "layout": layout, "uploaded_by": request.user.get("email"), "uploaded_at": datetime.datetime.utcnow().isoformat()}
    save_templates(templates)

    return jsonify({"ok": True, "template_id": template_id, "template": templates[template_id]})

# List templates (university)
@app.route("/university/templates", methods=["GET"])
@auth_required(role="university")
def university_templates():
    templates = load_templates()
    # return only templates uploaded by this university or all if admin
    user_email = request.user.get("email")
    filtered = {tid: meta for tid, meta in templates.items() if meta.get("uploaded_by") == user_email}
    return jsonify({"templates": filtered})

# University CSV upload (protected: university)
@app.route("/university/upload", methods=["POST"])
@auth_required(role="university")
def university_upload():
    # expects multipart form with file and optional template_id
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400
    file = request.files["file"]
    template_id = request.form.get("template_id")

    templates = load_templates()
    template_meta = templates.get(template_id) if template_id else None
    template_path = os.path.join(TEMPLATES_DIR, template_meta["filename"]) if template_meta else None
    layout = template_meta["layout"] if template_meta else {}

    # Get option to clear previous records before uploading
    clear_previous = request.form.get("clear_previous", "false").lower() == "true"
    university_email = request.user.get("email")
    
    # Clear previous records if requested
    if clear_previous:
        # Clear certificates issued by this university
        certs = load_certificates()
        certs = [c for c in certs if c.get("issued_by") != university_email]
        save_certificates(certs)
        
        # Clear students uploaded by this university
        students = get_all_students()
        students = [s for s in students if s.get("uploaded_by") != university_email]
        write_json(STUDENTS_FILE, students)
        
        # Clear proofs for students that no longer exist
        if db is not None:
            # For MongoDB, we'll keep proofs but they'll be orphaned (not a big issue)
            pass
        else:
            proofs = get_all_proofs()
            existing_student_ids = {s["id"] for s in students}
            proofs = [p for p in proofs if p.get("student_id") in existing_student_ids]
            write_json(PROOFS_FILE, proofs)
    
    stream = io.StringIO(file.stream.read().decode("utf-8"))
    reader = csv.DictReader(stream)
    new_rows = []
    certs = load_certificates()
    
    # Get option to handle duplicates: "update" (default) or "skip"
    duplicate_action = request.form.get("duplicate_action", "update").lower()

    for row in reader:
        row_record = {
            "data": row,
            "uploaded_by": request.user.get("email"),
            "created_at": datetime.datetime.utcnow().isoformat(),
        }
        
        # Check if student with same email already exists
        student_email = extract_email_from_student_data(row_record["data"])
        existing_student = find_student_by_email(student_email) if student_email else None
        
        if existing_student and duplicate_action == "skip":
            # Skip this student, don't create duplicate
            continue
        elif existing_student and duplicate_action == "update":
            # Update existing student record
            row_record["id"] = existing_student["id"]
            row_record["created_at"] = existing_student.get("created_at", datetime.datetime.utcnow().isoformat())
            stored = update_student_record(existing_student["id"], row_record)
            if not stored:
                stored = existing_student  # Fallback if update failed
        else:
            # Create new student record
            stored = add_student_record(row_record)
        # create proof (sha256 of JSON string)
        h = hashlib.sha256(json.dumps(stored["data"], sort_keys=True).encode("utf-8")).hexdigest()
        proof = {"student_id": stored["id"], "hash": h, "timestamp": datetime.datetime.utcnow().isoformat()}
        add_proof(proof)

        # Check if student already has a certificate (check ALL certificates, not just current batch)
        existing_cert = None
        # Always check for existing certificate by student_id, regardless of whether student was just created
        existing_cert = next((c for c in certs if c.get("student_id") == stored["id"]), None)
        
        # generate certificate for every student (with or without template)
        cert_info = None
        # Use existing cert_id if updating, otherwise create new one
        if existing_cert and duplicate_action == "update":
            cert_id = existing_cert.get("cert_id")
            # Update existing certificate instead of creating new one
        else:
            # Calculate cert_id based on maximum existing cert_id + 1, or start at 1 if no certificates exist
            max_cert_id = max([c.get("cert_id", 0) for c in certs], default=0)
            cert_id = max_cert_id + 1
        try:
            if template_path and os.path.exists(template_path):
                # Generate certificate image with template
                cert_path = generate_certificate_image(template_path, layout, stored, cert_id)
            else:
                # Generate simple certificate without template (just a record)
                cert_dir = os.path.join(DATA_DIR, "certs")
                os.makedirs(cert_dir, exist_ok=True)
                cert_path = os.path.join(cert_dir, f"certificate_{cert_id}.txt")
                with open(cert_path, "w") as f:
                    f.write(f"Certificate #{cert_id}\n")
                    f.write(f"Student: {stored.get('data', {}).get('name', 'Unknown')}\n")
                    f.write(f"ID: {stored.get('id')}\n")
                    f.write(f"Issued: {datetime.datetime.utcnow().isoformat()}\n")
                # Convert to relative path for consistent storage
                cert_path = os.path.relpath(cert_path, DATA_DIR)

            # Store file path relative to DATA_DIR for consistent access
            # generate_certificate_image now returns relative path, but handle both cases
            if os.path.isabs(cert_path):
                try:
                    file_rel_path = os.path.relpath(cert_path, DATA_DIR)
                except ValueError:
                    # If paths are on different drives (Windows), use absolute path
                    file_rel_path = cert_path
            else:
                file_rel_path = cert_path
            
            # Normalize path separators (handle Windows/Unix differences)
            file_rel_path = file_rel_path.replace("\\", "/")
            
            cert_record = {"cert_id": cert_id, "student_id": stored["id"], "file": file_rel_path, "generated_at": datetime.datetime.utcnow().isoformat(), "issued_by": request.user.get("email")}
            
            # Update existing certificate or append new one
            if existing_cert and duplicate_action == "update":
                # Update existing certificate
                cert_index = next((i for i, c in enumerate(certs) if c.get("cert_id") == cert_id), None)
                if cert_index is not None:
                    certs[cert_index] = cert_record
                else:
                    certs.append(cert_record)
            else:
                certs.append(cert_record)

            # attempt to email cert to student if email present
            student_email = stored.get("data", {}).get("email")
            cert_record["emailed_to"] = None
            if student_email and os.path.exists(os.path.join(DATA_DIR, cert_record["file"])):
                sent = send_email_with_attachment(student_email, "Your Certificate", "Please find your certificate attached.", os.path.join(DATA_DIR, cert_record["file"]))
                if sent:
                    cert_record["emailed_to"] = student_email
                    cert_record["emailed_at"] = datetime.datetime.utcnow().isoformat()
            # Don't save here - save once at the end of the loop
            cert_info = cert_record
        except Exception as e:
            print("certificate generation failed:", e)
            cert_info = {"error": str(e)}

        new_rows.append({"student": stored, "proof": proof, "certificate": cert_info, "was_duplicate": existing_student is not None})
    
    # Save certificates once at the end (more efficient and prevents issues)
    save_certificates(certs)
    
    # Calculate statistics
    initial_cert_count = len(load_certificates()) - len([r for r in new_rows if r.get("certificate") and not r.get("certificate", {}).get("error")])
    new_certs = sum(1 for row in new_rows if row.get("certificate") and not row.get("certificate", {}).get("error") and not row.get("was_duplicate"))
    updated_certs = sum(1 for row in new_rows if row.get("was_duplicate") and duplicate_action == "update")
    
    return jsonify({
        "uploaded": len(new_rows), 
        "rows": new_rows,
        "statistics": {
            "total_certificates_in_system": len(certs),
            "certificates_before_upload": initial_cert_count,
            "new_certificates_created": new_certs,
            "certificates_updated": updated_certs,
            "students_processed": len(new_rows),
            "duplicate_action_used": duplicate_action
        }
    })

# send email helper
import smtplib
from email.message import EmailMessage

SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", "0")) if os.getenv("SMTP_PORT") else None
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USER)


def send_email_with_attachment(to_email: str, subject: str, body: str, attachment_path: str) -> bool:
    if not SMTP_HOST or not SMTP_USER or not SMTP_PASS:
        print("SMTP not configured; skipping email to", to_email)
        return False
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = SMTP_FROM
        msg["To"] = to_email
        msg.set_content(body)

        with open(attachment_path, "rb") as f:
            data = f.read()
            maintype = "application"
            subtype = "pdf" if attachment_path.lower().endswith('.pdf') else 'octet-stream'
            msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=os.path.basename(attachment_path))

        if SMTP_PORT and SMTP_PORT == 465:
            server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT)
        else:
            server = smtplib.SMTP(SMTP_HOST, SMTP_PORT or 587)
            server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.send_message(msg)
        server.quit()
        print(f"Email sent to {to_email}")
        return True
    except Exception as e:
        print("Failed to send email", e)
        return False

# Endpoint to list certificates for the university
@app.route("/university/certificates", methods=["GET"])
@auth_required(role="university")
def university_certificates():
    certs = load_certificates()
    # filter by issuer
    user_email = request.user.get("email")
    filtered = [c for c in certs if c.get("issued_by") == user_email]
    return jsonify({"count": len(filtered), "certificates": filtered})

# Endpoint to delete all records for the university
@app.route("/university/clear-all", methods=["POST"])
@auth_required(role="university")
def university_clear_all():
    """Delete all certificates, students, and related proofs for this university."""
    university_email = request.user.get("email")
    
    deleted_counts = {
        "certificates": 0,
        "students": 0,
        "proofs": 0
    }
    
    # Delete certificates issued by this university
    certs = load_certificates()
    initial_cert_count = len(certs)
    certs = [c for c in certs if c.get("issued_by") != university_email]
    deleted_counts["certificates"] = initial_cert_count - len(certs)
    save_certificates(certs)
    
    # Delete students uploaded by this university
    students = get_all_students()
    initial_student_count = len(students)
    deleted_student_ids = {s["id"] for s in students if s.get("uploaded_by") == university_email}
    students = [s for s in students if s.get("uploaded_by") != university_email]
    deleted_counts["students"] = initial_student_count - len(students)
    write_json(STUDENTS_FILE, students)
    
    # Delete proofs for deleted students
    if db is not None:
        # For MongoDB
        if deleted_student_ids:
            result = db.proofs.delete_many({"student_id": {"$in": list(deleted_student_ids)}})
            deleted_counts["proofs"] = result.deleted_count
    else:
        # For JSON file
        proofs = get_all_proofs()
        initial_proof_count = len(proofs)
        proofs = [p for p in proofs if p.get("student_id") not in deleted_student_ids]
        deleted_counts["proofs"] = initial_proof_count - len(proofs)
        write_json(PROOFS_FILE, proofs)
    
    return jsonify({
        "success": True,
        "message": "All records deleted successfully",
        "deleted": deleted_counts
    })

# Download certificate file
@app.route("/certificates/<int:cert_id>", methods=["GET"])
def download_certificate(cert_id):
    certs = load_certificates()
    rec = next((c for c in certs if int(c.get("cert_id")) == int(cert_id)), None)
    if not rec:
        return jsonify({"error": "certificate not found"}), 404
    
    file_path = rec.get("file")
    if not file_path:
        return jsonify({"error": "file path not found in certificate record"}), 404
    
    # Normalize path separators (handle Windows/Unix differences)
    file_path = file_path.replace("\\", "/")
    
    # Handle both absolute and relative paths
    if os.path.isabs(file_path):
        path = file_path
    else:
        # Join with DATA_DIR and normalize
        path = os.path.join(DATA_DIR, file_path)
    
    # Normalize path to handle any path issues (resolve .., ., etc.)
    path = os.path.normpath(os.path.abspath(path))
    
    # Security check: ensure path is within DATA_DIR
    data_dir_abs = os.path.abspath(DATA_DIR)
    # Normalize both paths for comparison (handle case-insensitive on Windows)
    path_normalized = os.path.normpath(path).lower() if os.name == 'nt' else os.path.normpath(path)
    data_dir_normalized = os.path.normpath(data_dir_abs).lower() if os.name == 'nt' else os.path.normpath(data_dir_abs)
    
    if not path_normalized.startswith(data_dir_normalized):
        return jsonify({"error": "invalid file path", "file_path": file_path}), 400
    
    if not os.path.exists(path):
        # Try alternative paths in case of path issues
        # Check for both .txt and .pdf versions, and both naming conventions
        cert_id = rec.get("cert_id")
        alt_paths = [
            path,  # Original path
            os.path.join(DATA_DIR, file_path),  # Explicit DATA_DIR join
            os.path.join(CERTS_DIR, os.path.basename(file_path)),  # Just filename in CERTS_DIR
            # Try alternative naming: cert_X.pdf if stored as certificate_X.txt
            os.path.join(CERTS_DIR, f"cert_{cert_id}.pdf") if cert_id else None,
            os.path.join(CERTS_DIR, f"cert_{cert_id}.png") if cert_id else None,
            os.path.join(CERTS_DIR, f"certificate_{cert_id}.txt") if cert_id else None,
            os.path.join(CERTS_DIR, f"certificate_{cert_id}.pdf") if cert_id else None,
            file_path if os.path.isabs(file_path) else None
        ]
        
        found_path = None
        for alt_path in alt_paths:
            if alt_path and os.path.exists(alt_path):
                found_path = os.path.abspath(alt_path)
                # Update the database record with the correct path
                cert_index = next((i for i, c in enumerate(certs) if c.get("cert_id") == cert_id), None)
                if cert_index is not None:
                    correct_rel_path = os.path.relpath(found_path, DATA_DIR).replace("\\", "/")
                    certs[cert_index]["file"] = correct_rel_path
                    save_certificates(certs)
                break
        
        if not found_path:
            # List what files actually exist in the certs directory for debugging
            existing_files = []
            try:
                if os.path.exists(CERTS_DIR):
                    existing_files = [f for f in os.listdir(CERTS_DIR) if str(cert_id) in f][:10]  # Limit to 10 for response size
            except Exception:
                pass
            
            return jsonify({
                "error": "file missing", 
                "expected_path": path,
                "stored_path": file_path,
                "data_dir": DATA_DIR,
                "certs_dir": CERTS_DIR,
                "cert_id": cert_id,
                "tried_paths": [p for p in alt_paths if p],
                "existing_files_in_certs": existing_files
            }), 404
        
        path = found_path
    
    return send_file(path, as_attachment=True)

# Helper function to extract email from student data (handles various column name formats)
def extract_email_from_student_data(student_data):
    """Extract email from student data dict, handling various column name formats."""
    if not student_data:
        return None
    
    # Try common email field name variations (case-insensitive)
    email_keywords = ["email", "e-mail", "mail", "email_address", "emailaddress"]
    for key in student_data.keys():
        key_lower = key.lower().strip()
        if any(keyword in key_lower for keyword in email_keywords):
            email_value = str(student_data[key]).strip()
            if email_value and "@" in email_value:  # Basic email validation
                return email_value.lower()
    
    # Fallback: check if any value looks like an email
    for value in student_data.values():
        value_str = str(value).strip().lower()
        if "@" in value_str and "." in value_str.split("@")[-1]:
            return value_str
    
    return None

# Student: list their certificates
@app.route("/student/certificates", methods=["GET"])
@auth_required(role="student")
def student_certificates():
    user = request.user
    email_param = request.args.get("email", "").strip().lower()
    user_email = (email_param or user.get("email", "")).strip().lower()

    if not user_email:
        return jsonify({"count": 0, "certificates": [], "error": "No email provided"}), 400

    certs = load_certificates()
    my = []

    # Search by email in student data (matching CSV entry)
    # Handle different email column name variations (email, Email, EMAIL, e-mail, etc.)
    for c in certs:
        cert_student = find_student_by_id(c.get("student_id"))
        if cert_student:
            student_data = cert_student.get("data", {})
            student_email = extract_email_from_student_data(student_data)
            
            # Compare emails (case-insensitive, whitespace-stripped)
            if student_email and student_email == user_email:
                # Include student_data and student_id in the certificate response for frontend display
                cert_with_data = c.copy()
                cert_with_data["student_data"] = student_data
                cert_with_data["student_record_id"] = cert_student.get("id")  # Include the actual student record ID
                my.append(cert_with_data)

    # Fallback: search by user ID (if certificates were linked directly)
    if not my:
        my = [c for c in certs if int(c.get("student_id")) == int(user.get("id"))]

    return jsonify({"count": len(my), "certificates": my})

# University: resend email for a certificate
@app.route("/certificates/<int:cert_id>/resend", methods=["POST"])
@auth_required(role="university")
def resend_certificate_email(cert_id):
    certs = load_certificates()
    rec = next((c for c in certs if int(c.get("cert_id")) == int(cert_id)), None)
    if not rec:
        return jsonify({"error": "certificate not found"}), 404
    student = find_student_by_id(rec.get("student_id"))
    if not student:
        return jsonify({"error": "student not found"}), 404
    student_email = student.get("data", {}).get("email")
    path = os.path.join(DATA_DIR, rec.get("file"))
    sent = False
    if student_email and os.path.exists(path):
        sent = send_email_with_attachment(student_email, "Your certificate", "Please find your certificate attached.", path)
        if sent:
            rec["emailed_to"] = student_email
            rec["emailed_at"] = datetime.datetime.utcnow().isoformat()
            save_certificates(certs)
    return jsonify({"ok": True, "sent": sent})

# Blockchain mock: add proof for a single record
@app.route("/blockchain/add", methods=["POST"])
@auth_required()
def blockchain_add():
    body = request.get_json() or {}
    student_id = body.get("student_id")
    student = find_student_by_id(student_id)
    if not student:
        return jsonify({"error": "Student not found"}), 404
    h = hashlib.sha256(json.dumps(student["data"], sort_keys=True).encode("utf-8")).hexdigest()
    proof = {"student_id": student_id, "hash": h, "timestamp": datetime.datetime.utcnow().isoformat(), "added_by": request.user.get("email")}
    add_proof(proof)
    return jsonify({"proof": proof})

# Verify proof
@app.route("/blockchain/verify", methods=["POST"])
def blockchain_verify():
    body = request.get_json() or {}
    student_id = body.get("student_id")
    student = find_student_by_id(student_id)
    if not student:
        return jsonify({"error": "Student not found"}), 404
    expected = hashlib.sha256(json.dumps(student["data"], sort_keys=True).encode("utf-8")).hexdigest()
    proof = get_latest_proof_for_student(student_id)
    valid = proof and proof.get("hash") == expected
    return jsonify({"valid": bool(valid), "proof": proof, "expected": expected})

# Verify a signed token from QR
@app.route("/verify_token", methods=["POST"])
def verify_token_endpoint():
    body = request.get_json() or {}
    token = body.get("token") or request.args.get("token")
    if not token:
        return jsonify({"error": "token required"}), 400
    payload = verify_token(token)
    if not payload:
        return jsonify({"error": "invalid or expired token"}), 400
    cert_id = payload.get("cert_id")
    student_id = payload.get("student_id")
    # locate certificate and check proof
    certs = load_certificates()
    cert = next((c for c in certs if int(c.get("cert_id")) == int(cert_id)), None)
    student = find_student_by_id(student_id)
    if not student:
        return jsonify({"error": "student not found"}), 404
    proof = get_latest_proof_for_student(student_id)
    expected = hashlib.sha256(json.dumps(student.get("data", {}), sort_keys=True).encode("utf-8")).hexdigest()
    valid = bool(proof and proof.get("hash") == expected)
    return jsonify({"valid": valid, "proof": proof, "expected": expected, "certificate": cert})

# Generate QR for a student (returns base64 PNG)
@app.route("/generate_qr", methods=["POST"])
def generate_qr():
    body = request.get_json() or {}
    student_id = body.get("student_id")
    student = find_student_by_id(student_id)
    if not student:
        return jsonify({"error": "Student not found"}), 404
    payload = {"student_id": student_id, "timestamp": datetime.datetime.utcnow().isoformat()}
    data = json.dumps(payload)
    qr = qrcode.QRCode(box_size=10, border=2)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffered = io.BytesIO()
    img.save(buffered, format="PNG")
    img_b64 = base64.b64encode(buffered.getvalue()).decode("utf-8")
    return jsonify({"qr_base64": img_b64, "payload": payload})

# Employer search
@app.route("/employer/search", methods=["GET"])
def employer_search():
    q = request.args.get("q", "").lower()
    students = get_all_students()
    matches = [s for s in students if q in json.dumps(s.get("data", {})).lower() or q in str(s.get("id", ""))]
    results = []
    for s in matches:
        expected = hashlib.sha256(json.dumps(s["data"], sort_keys=True).encode("utf-8")).hexdigest()
        proof = get_latest_proof_for_student(s["id"])
        valid = proof and proof.get("hash") == expected
        results.append({"student": s, "verified": bool(valid), "proof": proof})
    return jsonify({"count": len(results), "results": results})

# Simple endpoint to fetch records (protected: university)
@app.route("/university/records", methods=["GET"])
@auth_required(role="university")
def university_records():
    students = get_all_students()
    return jsonify({"count": len(students), "students": students})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
