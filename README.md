# Readiness — Day-of Planning Checklist System

A Flask-powered web application for managing and completing day-of planning checklists with admin configuration, user sign-offs, and a full audit trail.

---

## Features

| Feature | Details |
|---|---|
| **Admin Panel** | Create/edit checklists by day, manage users, view all completions |
| **User Portal** | Browse assigned checklists, check off items, add notes per item |
| **Sign-Off Flow** | Name signature required, all required items enforced before signing |
| **Audit Trail** | Every check, timestamp, and note stored in PostgreSQL |
| **Auto-Save** | Items auto-save via AJAX as users check them off |
| **Progress Tracking** | Real-time progress bar on the checklist form |

---

## Project Structure

```
readiness/
├── app.py                  # Main Flask app + models + routes
├── application.py          # Elastic Beanstalk WSGI entry point
├── requirements.txt
├── Procfile
├── .env.example            # Copy to .env with your values
├── .ebextensions/
│   ├── 01_flask.config     # EB Python configuration
│   └── 02_db_init.config   # Auto-create DB tables on deploy
├── static/
│   ├── css/style.css
│   └── js/app.js
└── templates/
    ├── base.html
    ├── login.html
    ├── admin_dashboard.html
    ├── admin_checklists.html
    ├── admin_checklist_form.html
    ├── admin_completions.html
    ├── admin_users.html
    ├── admin_user_form.html
    ├── user_dashboard.html
    ├── do_checklist.html
    └── view_completion.html
```

---

## Local Development Setup

### 1. Prerequisites
- Python 3.11+
- PostgreSQL (local or Docker)
- pip

### 2. Clone & Install

```bash
git clone <your-repo>
cd readiness

python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

pip install -r requirements.txt
```

### 3. Configure Environment

```bash
cp .env.example .env
# Edit .env with your local PostgreSQL credentials
```

### 4. Set Up Local PostgreSQL

```sql
CREATE DATABASE readiness_db;
CREATE USER readiness_user WITH PASSWORD 'yourpassword';
GRANT ALL PRIVILEGES ON DATABASE readiness_db TO readiness_user;
```

### 5. Run Locally

```bash
# Load env vars
export $(cat .env | xargs)

python app.py
```

Visit `http://localhost:5000`

**Default admin credentials:** `admin` / `Admin@1234`
> Change this immediately after first login by creating a new admin user.

---

## AWS Deployment

### Step 1: Create AWS RDS PostgreSQL

1. Open **RDS** in AWS Console → **Create database**
2. Engine: **PostgreSQL** (latest 16.x)
3. Template: **Free tier** (dev) or **Production**
4. Settings:
   - DB identifier: `readiness-db`
   - Master username: `readiness_user`
   - Master password: (generate & save)
5. Connectivity: **Same VPC as your EB environment**
6. Initial DB name: `readiness_db`
7. Click **Create database**

Note your **Endpoint** from the RDS console — this is your `DB_HOST`.

---

### Step 2: Install & Configure EB CLI

```bash
pip install awsebcli

# Configure AWS credentials
aws configure
# Enter: Access Key ID, Secret Access Key, Region (e.g. us-east-1), output format (json)
```

---

### Step 3: Initialize Elastic Beanstalk

```bash
cd readiness

eb init
# Follow prompts:
# - Select region (same as RDS)
# - Application name: readiness
# - Platform: Python 3.11
# - Do you wish to set up SSH? Yes (optional but recommended)
```

---

### Step 4: Create the EB Environment

```bash
eb create readiness-prod \
  --instance-type t3.small \
  --envvars SECRET_KEY=your-long-random-secret,DB_HOST=your-rds-endpoint.rds.amazonaws.com,DB_PORT=5432,DB_NAME=readiness_db,DB_USER=readiness_user,DB_PASS=your-db-password
```

Or set environment variables in the EB Console:
**Configuration → Software → Environment properties**

---

### Step 5: Configure Security Groups

Your RDS security group must allow inbound PostgreSQL (port 5432) from the EB EC2 security group.

1. Go to **EC2 → Security Groups**
2. Find your RDS security group
3. Add **Inbound Rule**:
   - Type: PostgreSQL
   - Port: 5432
   - Source: EB EC2 security group ID

---

### Step 6: Deploy

```bash
eb deploy
```

After deploy, the DB tables are auto-created by `.ebextensions/02_db_init.config`.

```bash
eb open   # Opens your app in the browser
eb logs   # View logs if something goes wrong
```

---

### Step 7: Post-Deployment Checklist

- [ ] Log in with `admin` / `Admin@1234`
- [ ] Create a real admin account, then delete the default one
- [ ] Create user accounts for your team
- [ ] Create your first checklist
- [ ] Set a strong `SECRET_KEY` environment variable

---

## Environment Variables Reference

| Variable | Description | Example |
|---|---|---|
| `SECRET_KEY` | Flask session secret (long random string) | `openssl rand -hex 32` |
| `DB_HOST` | RDS endpoint | `readiness.abc123.us-east-1.rds.amazonaws.com` |
| `DB_PORT` | PostgreSQL port | `5432` |
| `DB_NAME` | Database name | `readiness_db` |
| `DB_USER` | Database username | `readiness_user` |
| `DB_PASS` | Database password | your password |

---

## Database Schema

```
users               → id, username, email, password_hash, role, created_at
checklists          → id, name, description, scheduled_date, created_by, is_active
checklist_items     → id, checklist_id, title, description, is_required, order_index
checklist_completions → id, checklist_id, user_id, started_at, completed_at, signed_off, signature_name, overall_notes
item_responses      → id, completion_id, item_id, is_checked, notes, checked_at
```

---

## User Roles

| Role | Capabilities |
|---|---|
| **Admin** | Create/edit/delete checklists, view all completions, manage users |
| **User** | View & complete checklists, sign off, view own history |

---

## Updating the App

```bash
# Make your changes, then:
eb deploy
```

For database schema changes, modify the SQLAlchemy models in `app.py`. For production, consider using Flask-Migrate for migrations.

---

## Troubleshooting

| Issue | Solution |
|---|---|
| Can't connect to DB | Check RDS security group allows EB security group on port 5432 |
| 502 Bad Gateway | Check `eb logs` for Python/gunicorn errors |
| Tables don't exist | Run `eb ssh` then manually trigger `python -c "from app import app, db; app.app_context().push(); db.create_all()"` |
| Static files not loading | Verify `.ebextensions/01_flask.config` static file mapping |
