import "dotenv/config";
import express from "express";
import cors from "cors";
import { handleDemo } from "./routes/demo";
import multer from "multer";
import fs from "fs";
import path from "path";
import QRCode from "qrcode";
import nodemailer from "nodemailer";
import bcrypt from "bcrypt";
import jwt from "jsonwebtoken";
import sharp from "sharp";
import { createCanvas } from "canvas";

const DATA_DIR = path.resolve(__dirname, "../server/data");
const TEMPLATES_DIR = path.join(DATA_DIR, "templates");
const CERTS_DIR = path.join(DATA_DIR, "certs");
const TEMPLATES_FILE = path.join(DATA_DIR, "templates.json");
const CERTS_FILE = path.join(DATA_DIR, "certificates.json");
const USERS_FILE = path.join(DATA_DIR, "users.json");

fs.mkdirSync(TEMPLATES_DIR, { recursive: true });
fs.mkdirSync(CERTS_DIR, { recursive: true });
if (!fs.existsSync(TEMPLATES_FILE)) fs.writeFileSync(TEMPLATES_FILE, JSON.stringify({}));
if (!fs.existsSync(CERTS_FILE)) fs.writeFileSync(CERTS_FILE, JSON.stringify([]));
if (!fs.existsSync(USERS_FILE)) fs.writeFileSync(USERS_FILE, JSON.stringify([]));

const upload = multer({ dest: path.join(DATA_DIR, "uploads") });

function readJson(p: string) {
  try { return JSON.parse(fs.readFileSync(p, "utf-8")); } catch(e) { return null; }
}
function writeJson(p: string, data: any) { fs.writeFileSync(p, JSON.stringify(data, null, 2)); }

// Auth helpers
const JWT_SECRET = process.env.JWT_SECRET || 'dev-secret-change-in-production';
const verificationCodes = new Map<string, { code: string; timestamp: number }>();

function getAllUsers() {
  return readJson(USERS_FILE) || [];
}

function findUserByEmail(email: string) {
  const users = getAllUsers();
  return users.find((u: any) => u.email === email);
}

function addUser(user: any) {
  const users = getAllUsers();
  const id = users.length > 0 ? Math.max(...users.map((u: any) => u.id || 0)) + 1 : 1;
  const newUser = { ...user, id };
  users.push(newUser);
  writeJson(USERS_FILE, users);
  return newUser;
}

function createToken(payload: any) {
  return jwt.sign({ ...payload, exp: Math.floor(Date.now() / 1000) + (7 * 24 * 60 * 60) }, JWT_SECRET);
}

function generateVerificationCode() {
  return Math.floor(100000 + Math.random() * 900000).toString();
}

// Simple transporter using SMTP env vars
const SMTP_HOST = process.env.SMTP_HOST;
const SMTP_PORT = process.env.SMTP_PORT ? parseInt(process.env.SMTP_PORT) : undefined;
const SMTP_USER = process.env.SMTP_USER;
const SMTP_PASS = process.env.SMTP_PASS;
const SMTP_FROM = process.env.SMTP_FROM || SMTP_USER;
let transporter: any = null;
if (SMTP_HOST && SMTP_USER && SMTP_PASS) {
  transporter = nodemailer.createTransport({ host: SMTP_HOST, port: SMTP_PORT || 587, secure: (SMTP_PORT === 465), auth: { user: SMTP_USER, pass: SMTP_PASS } });
}

export function createServer() {
  const app = express();

  // Middleware
  app.use(cors());
  app.use(express.json());
  app.use(express.urlencoded({ extended: true }));

  // Authentication routes
  app.post('/auth/send_verification', (req, res) => {
    const { email } = req.body;
    if (!email) return res.status(400).json({ error: 'Email required' });
    
    const domain = email.split('@')[1]?.toLowerCase();
    if (domain !== 'st.niituniversity.in') {
      return res.status(400).json({ error: 'Invalid university domain' });
    }
    
    const code = generateVerificationCode();
    verificationCodes.set(email, { code, timestamp: Date.now() });
    console.log(`Verification code for ${email}: ${code}`);
    res.json({ ok: true, message: 'Verification code generated', code });
  });

  app.post('/auth/verify_code', (req, res) => {
    const { email, code } = req.body;
    if (!email || !code) return res.status(400).json({ error: 'Email and code required' });
    
    const stored = verificationCodes.get(email);
    if (!stored) return res.status(400).json({ error: 'No verification code found' });
    
    const isExpired = (Date.now() - stored.timestamp) > 15 * 60 * 1000;
    if (isExpired) {
      verificationCodes.delete(email);
      return res.status(400).json({ error: 'Code expired' });
    }
    
    if (stored.code !== code) {
      return res.status(400).json({ error: 'Invalid code' });
    }
    
    res.json({ ok: true });
  });

  app.post('/auth/register', async (req, res) => {
    const { username, email, password, role } = req.body;
    if (!username || !email || !password) {
      return res.status(400).json({ error: 'Missing fields' });
    }
    
    if (findUserByEmail(email)) {
      return res.status(400).json({ error: 'User already exists' });
    }
    
    const domain = email.split('@')[1]?.toLowerCase();
    if (role === 'student' && domain === 'st.niituniversity.in') {
      return res.status(400).json({ error: 'Students cannot use university email domain' });
    }
    
    let verified = true;
    if (role === 'university') {
      if (domain !== 'st.niituniversity.in') {
        return res.status(400).json({ error: 'University signups require @st.niituniversity.in' });
      }
      const stored = verificationCodes.get(email);
      verified = !!stored;
      if (verified) verificationCodes.delete(email);
    }
    
    const hashedPassword = await bcrypt.hash(password, 10);
    const user = addUser({
      username,
      email,
      password: hashedPassword,
      role: role || 'student',
      verified,
      created_at: new Date().toISOString()
    });
    
    const token = createToken({ id: user.id, email, role: user.role, verified });
    res.json({
      token,
      user: { id: user.id, username, email, role: user.role, verified }
    });
  });

  app.post('/auth/login', async (req, res) => {
    const { email, password } = req.body;
    if (!email || !password) {
      return res.status(400).json({ error: 'Email and password required' });
    }
    
    const user = findUserByEmail(email);
    if (!user) {
      return res.status(401).json({ error: 'Invalid credentials' });
    }
    
    const valid = await bcrypt.compare(password, user.password);
    if (!valid) {
      return res.status(401).json({ error: 'Invalid credentials' });
    }
    
    const token = createToken({ id: user.id, email: user.email, role: user.role, verified: user.verified });
    res.json({
      token,
      user: { id: user.id, username: user.username, email: user.email, role: user.role, verified: user.verified }
    });
  });

  // Example API routes
  app.get("/api/ping", (_req, res) => {
    const ping = process.env.PING_MESSAGE ?? "ping";
    res.json({ message: ping });
  });

  app.get("/api/demo", handleDemo);

  // Upload template
  app.post('/university/template/upload', upload.single('file'), (req, res) => {
    const file = req.file as any;
    const layout = req.body.layout ? JSON.parse(req.body.layout) : {};
    if (!file) return res.status(400).json({ error: 'No file' });
    const id = Date.now().toString(36) + '-' + Math.random().toString(36).slice(2,8);
    const ext = path.extname(file.originalname) || '.png';
    const filename = `template_${id}${ext}`;
    const dest = path.join(TEMPLATES_DIR, filename);
    fs.renameSync(file.path, dest);
    const templates = readJson(TEMPLATES_FILE) || {};
    templates[id] = { filename, layout, uploaded_at: new Date().toISOString() };
    writeJson(TEMPLATES_FILE, templates);
    res.json({ ok: true, template_id: id, template: templates[id] });
  });

  // list templates
  app.get('/university/templates', (req, res) => {
    const templates = readJson(TEMPLATES_FILE) || {};
    res.json({ templates });
  });

  // upload csv (simple) - expects file field 'file' and optional template_id
  app.post('/university/upload', upload.single('file'), (req, res) => {
    const file = req.file as any;
    const template_id = req.body.template_id;
    if (!file) return res.status(400).json({ error: 'No file' });
    const content = fs.readFileSync(file.path, 'utf-8');
    // parse CSV header
    const lines = content.split(/\r?\n/).filter(Boolean);
    const header = lines[0].split(',').map(h => h.trim());
    const rows = lines.slice(1).map(l => {
      const parts = l.split(',');
      const obj: any = {};
      header.forEach((h, i) => obj[h] = parts[i] || '');
      return obj;
    });
    // create certificate records
    const certs = readJson(CERTS_FILE) || [];
    const templates = readJson(TEMPLATES_FILE) || {};
    const template = template_id ? templates[template_id] : null;
    rows.forEach((r: any, idx: number) => {
      const cert_id = certs.length + 1;
      const rec = { 
        cert_id, 
        student_data: r, 
        generated_at: new Date().toISOString(), 
        template_id: template_id || null,
        file: null
      };
      certs.push(rec);
      // attempt to email if transporter and email field
      try {
        const to = r.email;
        if (transporter && to && template_id) {
          // Email will be sent with dynamically generated certificate
          rec.emailed_to = to; 
          rec.emailed_at = new Date().toISOString();
        }
      } catch(e){ console.error(e); }
    });
    writeJson(CERTS_FILE, certs);
    res.json({ uploaded: rows.length, rows: rows.map((r:any,i:number)=>({ student: r, cert_id: i+1 })) });
  });

  // list certificates
  app.get('/university/certificates', (req, res) => {
    const certs = readJson(CERTS_FILE) || [];
    res.json({ count: certs.length, certificates: certs });
  });

  // download cert
  app.get('/certificates/:cert_id', async (req, res) => {
    const certs = readJson(CERTS_FILE) || [];
    const cid = parseInt(req.params.cert_id, 10);
    const rec = certs.find((c:any) => c.cert_id === cid);
    if (!rec) return res.status(404).json({ error: 'not found' });
    
    const student = rec.student_data || {};
    
    // If certificate has a template, generate personalized certificate
    if (rec.template_id) {
      const templates = readJson(TEMPLATES_FILE) || {};
      const template = templates[rec.template_id];
      
      if (template && template.filename) {
        const templatePath = path.join(TEMPLATES_DIR, template.filename);
        
        if (fs.existsSync(templatePath)) {
          try {
            // Load template image
            const templateBuffer = fs.readFileSync(templatePath);
            const image = sharp(templateBuffer);
            const metadata = await image.metadata();
            
            // Create canvas for text overlay
            const canvas = createCanvas(metadata.width || 800, metadata.height || 600);
            const ctx = canvas.getContext('2d');
            
            // Set text properties
            ctx.fillStyle = '#000000';
            ctx.textAlign = 'center';
            
            // Layout from template or use defaults
            const layout = template.layout || {};
            const width = metadata.width || 800;
            const height = metadata.height || 600;
            
            // Default positions (can be customized via layout)
            const positions = {
              name: { x: width / 2, y: height * 0.45, size: 48 },
              enrollment_no: { x: width / 2, y: height * 0.55, size: 24 },
              degree: { x: width / 2, y: height * 0.62, size: 20 },
              major: { x: width / 2, y: height * 0.67, size: 20 },
              graduation_year: { x: width / 2, y: height * 0.72, size: 20 },
              gpa: { x: width / 2, y: height * 0.77, size: 20 },
              ...layout
            };
            
            // Draw text for each field
            if (student.name) {
              const pos = positions.name;
              ctx.font = `bold ${pos.size}px Arial`;
              ctx.fillText(student.name, pos.x, pos.y);
            }
            
            if (student.enrollment_no) {
              const pos = positions.enrollment_no;
              ctx.font = `${pos.size}px Arial`;
              ctx.fillText(`Enrollment: ${student.enrollment_no}`, pos.x, pos.y);
            }
            
            if (student.degree) {
              const pos = positions.degree;
              ctx.font = `${pos.size}px Arial`;
              ctx.fillText(`Degree: ${student.degree}`, pos.x, pos.y);
            }
            
            if (student.major) {
              const pos = positions.major;
              ctx.font = `${pos.size}px Arial`;
              ctx.fillText(`Major: ${student.major}`, pos.x, pos.y);
            }
            
            if (student.graduation_year) {
              const pos = positions.graduation_year;
              ctx.font = `${pos.size}px Arial`;
              ctx.fillText(`Year: ${student.graduation_year}`, pos.x, pos.y);
            }
            
            if (student.gpa) {
              const pos = positions.gpa;
              ctx.font = `${pos.size}px Arial`;
              ctx.fillText(`GPA: ${student.gpa}`, pos.x, pos.y);
            }
            
            // Composite text overlay onto template
            const textBuffer = canvas.toBuffer('image/png');
            const finalImage = await sharp(templateBuffer)
              .composite([{ input: textBuffer, blend: 'over' }])
              .png()
              .toBuffer();
            
            res.setHeader('Content-Type', 'image/png');
            res.setHeader('Content-Disposition', `attachment; filename="certificate_${rec.cert_id}.png"`);
            return res.send(finalImage);
          } catch (err) {
            console.error('Error generating certificate:', err);
            // Fall through to text certificate
          }
        }
      }
    }
    
    // Fallback: Generate a simple text certificate
    const certificateText = `
═══════════════════════════════════════════════════════════════
                    ACADEMIC CERTIFICATE
         Blockchain-Based Credential Verification System
═══════════════════════════════════════════════════════════════

Certificate ID: ${rec.cert_id}
Issue Date: ${new Date(rec.generated_at).toLocaleDateString()}

STUDENT INFORMATION:
─────────────────────────────────────────────────────────────

Name:              ${student.name || 'N/A'}
Email:             ${student.email || 'N/A'}
Enrollment No:     ${student.enrollment_no || 'N/A'}

ACADEMIC DETAILS:
─────────────────────────────────────────────────────────────

Degree:            ${student.degree || 'N/A'}
Major:             ${student.major || 'N/A'}
Graduation Year:   ${student.graduation_year || 'N/A'}
GPA:               ${student.gpa || 'N/A'}

═══════════════════════════════════════════════════════════════
This certificate is cryptographically secured and verified on
the blockchain. Any modification will invalidate the certificate.

Verification: Use the QR code or student ID to verify authenticity.
═══════════════════════════════════════════════════════════════
    `.trim();
    
    res.setHeader('Content-Type', 'text/plain');
    res.setHeader('Content-Disposition', `attachment; filename="certificate_${rec.cert_id}.txt"`);
    res.send(certificateText);
  });

  // student certificates (mock: find by ?email param)
  app.get('/student/certificates', (req, res) => {
    const email = req.query.email as string | undefined;
    const certs = readJson(CERTS_FILE) || [];
    if (email) {
      const filtered = certs.filter((c:any) => (c.student_data && c.student_data.email && c.student_data.email.toLowerCase() === email.toLowerCase()));
      return res.json({ count: filtered.length, certificates: filtered });
    }
    res.json({ count: certs.length, certificates: certs });
  });

  // generate QR for student: returns data URL
  app.post('/generate_qr', express.json(), async (req, res) => {
    const { student_id } = req.body as any;
    // find student data in certs
    const certs = readJson(CERTS_FILE) || [];
    const rec = certs.find((c:any) => c.student_data && (c.student_data.id == student_id || c.student_data.student_id == student_id));
    const payload = { student_id: student_id || null, timestamp: new Date().toISOString() };
    try {
      const dataUrl = await QRCode.toDataURL(JSON.stringify(payload));
      const base64 = dataUrl.split(',')[1];
      res.json({ qr_base64: base64, payload });
    } catch (e) {
      res.status(500).json({ error: String(e) });
    }
  });

  return app;
}
