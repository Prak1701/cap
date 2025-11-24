# Blockchain-Based Academic Credential Verification

## Overview
A React-based web application for verifying academic credentials using blockchain technology. The system provides a secure platform for universities to issue credentials, students to manage them, and employers to verify them.

## Project Structure

### Frontend (client/)
- React 18 with TypeScript
- Vite as the build tool and dev server
- React Router for navigation
- TailwindCSS for styling
- Radix UI components
- Three.js for 3D visualizations

### Backend (server/)
- Express.js server
- Runs as Vite middleware during development
- File-based storage (JSON files in server/data/)
- Multer for file uploads
- QR code generation using qrcode library
- Email support via nodemailer

### Key Features
- University dashboard for certificate management
- Student dashboard for viewing certificates
- Employer dashboard for verification
- QR code generation for certificates
- CSV batch upload for certificates
- Template management for certificate designs

## Architecture

### Development Setup
The project uses Vite with a custom Express plugin that runs the backend as middleware during development. This means:
- Single server on port 5000 serves both frontend and API
- Frontend requests go through Vite's dev server with HMR
- API requests to `/api/*` routes are handled by Express
- Backend code is in `server/index.ts` and imported as middleware

### Production Build
The project builds separately:
- `npm run build:client` - Builds React app to `dist/spa/`
- `npm run build:server` - Builds Express server to `dist/server/`
- `npm start` - Runs the production server

## Technologies

### Frontend
- React 18.3
- TypeScript 5.9
- Vite 7.1
- React Router 6.30
- TailwindCSS 3.4
- Radix UI components
- Tanstack Query for data fetching
- Three.js and React Three Fiber for 3D

### Backend
- Express 5.1
- Multer for file uploads
- QRCode for QR generation
- Nodemailer for email
- Zod for validation

## Environment Variables

### SMTP Configuration (Optional)
- `SMTP_HOST` - SMTP server hostname
- `SMTP_PORT` - SMTP port (default 587)
- `SMTP_USER` - SMTP username
- `SMTP_PASS` - SMTP password
- `SMTP_FROM` - From email address

### Other
- `PING_MESSAGE` - Custom ping response message

## Data Storage

The application uses file-based storage in the `server/data/` directory:
- `templates.json` - Certificate templates
- `certificates.json` - Issued certificates
- `templates/` - Uploaded template files
- `uploads/` - Temporary file uploads

## Recent Changes

### 2024-11-24: Authentication System Implementation
- Added complete authentication system to Express backend
- Implemented `/auth/login`, `/auth/register`, `/auth/send_verification`, and `/auth/verify_code` endpoints
- User data stored in `server/data/users.json`
- Password hashing with bcrypt
- JWT token authentication
- Email verification for university accounts
- Students: any email except @st.niituniversity.in
- Universities: require @st.niituniversity.in and email verification
- Employers: any email allowed

### 2024-11-24: Initial Replit Setup
- Configured Vite to run on port 5000 with host 0.0.0.0
- Added allowedHosts: true for Replit proxy compatibility
- Set up "Start application" workflow
- Updated .gitignore to preserve Replit configuration
- Verified application loads successfully

## User Preferences
None recorded yet.
