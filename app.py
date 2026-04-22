from dotenv import load_dotenv
load_dotenv()

import boto3
import requests as http_requests
from botocore.exceptions import ClientError
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session, make_response
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime
import io
import os
import secrets
import string
from functools import wraps
from PIL import Image
import pillow_heif
pillow_heif.register_heif_opener()
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, Image as RLImage
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-change-in-prod')

# Database — set DATABASE_URL to override (e.g. sqlite:///local.db for local dev)
_db_url = os.environ.get('DATABASE_URL')
if not _db_url:
    DB_USER = os.environ.get('DB_USER', 'postgres')
    DB_PASS = os.environ.get('DB_PASS', 'UX2G1Kl6MgTYb0Gqbmzv')
    DB_HOST = os.environ.get('DB_HOST', 'checklist.cluster-czacaowa4yxt.us-east-2.rds.amazonaws.com')
    DB_PORT = os.environ.get('DB_PORT', '5432')
    DB_NAME = os.environ.get('DB_NAME', 'postgres')
    _db_url = f'postgresql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}'

app.config['SQLALCHEMY_DATABASE_URI'] = _db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB

# S3 — set S3_BUCKET env var to enable cloud storage; falls back to local disk
S3_BUCKET = os.environ.get('S3_BUCKET')
S3_REGION = os.environ.get('AWS_DEFAULT_REGION', 'us-east-2')
_s3 = boto3.client('s3', region_name=S3_REGION) if S3_BUCKET else None

def upload_image(file_obj, filename):
    """Save an uploaded image. Uses S3 when configured, local disk otherwise."""
    if _s3 and S3_BUCKET:
        file_obj.seek(0)
        ext = filename.rsplit('.', 1)[-1].lower()
        content_type = 'image/jpeg' if ext in ('jpg', 'jpeg') else f'image/{ext}'
        _s3.upload_fileobj(file_obj, S3_BUCKET, filename,
                           ExtraArgs={'ContentType': content_type})
    else:
        file_obj.seek(0)
        with open(os.path.join(UPLOAD_FOLDER, filename), 'wb') as f:
            f.write(file_obj.read())

def image_url(filename):
    """Return the public URL for a stored image filename."""
    if not filename:
        return ''
    if _s3 and S3_BUCKET:
        return f'https://{S3_BUCKET}.s3.{S3_REGION}.amazonaws.com/{filename}'
    return f'/static/uploads/{filename}'

def image_bytes_for_pdf(filename):
    """Return a BytesIO of the image for PDF generation."""
    if _s3 and S3_BUCKET:
        buf = io.BytesIO()
        try:
            _s3.download_fileobj(S3_BUCKET, filename, buf)
            buf.seek(0)
            return buf
        except ClientError:
            return None
    path = os.path.join(UPLOAD_FOLDER, filename)
    if os.path.isfile(path):
        with open(path, 'rb') as f:
            return io.BytesIO(f.read())
    return None

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
app.jinja_env.globals['image_url'] = image_url

# Planning Center Online — set PCO_APP_ID and PCO_SECRET env vars to enable PCO login
# PCO_APP_ID:  the Application ID from https://api.planningcenteronline.com/oauth/applications
# PCO_SECRET:  the Secret (personal access token) from the same page
PCO_APP_ID = os.environ.get('PCO_APP_ID', '')
PCO_SECRET = os.environ.get('PCO_SECRET', '')
PCO_ENABLED = bool(PCO_APP_ID and PCO_SECRET)
app.jinja_env.globals['pco_enabled'] = PCO_ENABLED


def pco_find_person(email):
    """Look up a person in PCO People by email using the admin personal access token.
    Returns the PCO person dict on success, or None if not found / misconfigured."""
    if not PCO_ENABLED:
        return None
    try:
        resp = http_requests.get(
            'https://api.planningcenteronline.com/people/v2/people',
            params={'where[search_name_or_email]': email, 'per_page': 5},
            auth=(PCO_APP_ID, PCO_SECRET),
            timeout=6,
        )
        if resp.status_code != 200:
            return None
        for person in resp.json().get('data', []):
            attrs = person.get('attributes', {})
            pco_email = (attrs.get('primary_email') or '').lower()
            if pco_email == email.lower():
                return person
    except Exception:
        pass
    return None

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'heic', 'heif'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def normalize_image(file_obj):
    """Convert any image (including HEIC) to a JPEG BytesIO. Returns (BytesIO, 'jpg')."""
    file_obj.seek(0)
    img = Image.open(file_obj)
    if img.mode not in ('RGB', 'L'):
        img = img.convert('RGB')
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=88)
    buf.seek(0)
    return buf, 'jpg'

# ─────────────────────────────────────────
# Models
# ─────────────────────────────────────────

team_members = db.Table('team_members',
    db.Column('team_id', db.Integer, db.ForeignKey('teams.id'), primary_key=True),
    db.Column('user_id', db.Integer, db.ForeignKey('users.id'), primary_key=True)
)


class Role(db.Model):
    __tablename__ = 'roles'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    team_id = db.Column(db.Integer, db.ForeignKey('teams.id'), nullable=True)
    team = db.relationship('Team', backref='roles')


class Team(db.Model):
    __tablename__ = 'teams'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text)
    color = db.Column(db.String(7), default='#111111')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    members = db.relationship('User', secondary='team_members', backref='teams')


class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), default='user')  # 'admin' or 'user'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password, method='pbkdf2:sha256')

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self):
        return self.role == 'admin'


class Checklist(db.Model):
    __tablename__ = 'checklists'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)
    assigned_role = db.Column(db.String(80))  # None/empty = visible to all roles
    team_id = db.Column(db.Integer, db.ForeignKey('teams.id'), nullable=True)
    items = db.relationship('ChecklistItem', backref='checklist', cascade='all, delete-orphan', order_by='ChecklistItem.order_index')
    completions = db.relationship('ChecklistCompletion', backref='checklist', cascade='all, delete-orphan')
    creator = db.relationship('User', foreign_keys=[created_by])
    team = db.relationship('Team', foreign_keys=[team_id])


class ChecklistItem(db.Model):
    __tablename__ = 'checklist_items'
    id = db.Column(db.Integer, primary_key=True)
    checklist_id = db.Column(db.Integer, db.ForeignKey('checklists.id'), nullable=False)
    title = db.Column(db.String(300), nullable=False)
    description = db.Column(db.Text)
    is_required = db.Column(db.Boolean, default=True)
    order_index = db.Column(db.Integer, default=0)
    visual_aid_photo = db.Column(db.String(256))


class ChecklistCompletion(db.Model):
    __tablename__ = 'checklist_completions'
    id = db.Column(db.Integer, primary_key=True)
    checklist_id = db.Column(db.Integer, db.ForeignKey('checklists.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime)
    signed_off = db.Column(db.Boolean, default=False)
    signature_name = db.Column(db.String(200))
    overall_notes = db.Column(db.Text)
    item_responses = db.relationship('ItemResponse', backref='completion', cascade='all, delete-orphan')
    user = db.relationship('User', foreign_keys=[user_id])

    @property
    def progress(self):
        if not self.item_responses:
            return 0
        checked = sum(1 for r in self.item_responses if r.is_checked)
        return int((checked / len(self.item_responses)) * 100)


class ItemResponse(db.Model):
    __tablename__ = 'item_responses'
    id = db.Column(db.Integer, primary_key=True)
    completion_id = db.Column(db.Integer, db.ForeignKey('checklist_completions.id'), nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey('checklist_items.id'), nullable=False)
    is_checked = db.Column(db.Boolean, default=False)
    notes = db.Column(db.Text)
    checked_at = db.Column(db.DateTime)
    photo_filename = db.Column(db.String(256))
    item = db.relationship('ChecklistItem', foreign_keys=[item_id])


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# ─────────────────────────────────────────
# Decorators
# ─────────────────────────────────────────

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            flash('Admin access required.', 'error')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated


# ─────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────

def _get_available_roles():
    """Return sorted list of role names from the roles table."""
    return [r.name for r in Role.query.order_by(Role.name).all()]


# ─────────────────────────────────────────
# Auth Routes
# ─────────────────────────────────────────

@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user)
            flash(f'Welcome back, {user.username}!', 'success')
            return redirect(url_for('dashboard'))
        flash('Invalid username or password.', 'error')
    active_tab = request.args.get('tab', 'login')
    return render_template('login.html', active_tab=active_tab)


@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')
        if not username or not email or not password:
            flash('All fields are required.', 'error')
            return render_template('login.html', show_register=True)
        if password != confirm:
            flash('Passwords do not match.', 'error')
            return render_template('login.html', show_register=True)
        if len(password) < 8:
            flash('Password must be at least 8 characters.', 'error')
            return render_template('login.html', show_register=True)
        if User.query.filter_by(username=username).first():
            flash('Username already taken.', 'error')
            return render_template('login.html', show_register=True)
        if User.query.filter_by(email=email).first():
            flash('Email already registered.', 'error')
            return render_template('login.html', show_register=True)
        user = User(username=username, email=email, role='user')
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        login_user(user)
        flash(f'Account created! Welcome, {user.username}.', 'success')
        return redirect(url_for('dashboard'))
    return render_template('login.html', show_register=True)


@app.route('/login/pco', methods=['POST'])
def login_pco():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if not PCO_ENABLED:
        flash('Planning Center login is not configured.', 'error')
        return redirect(url_for('login', tab='pco'))

    email = request.form.get('pco_email', '').strip().lower()
    if not email:
        flash('Please enter your Planning Center email.', 'error')
        return redirect(url_for('login', tab='pco'))

    person = pco_find_person(email)
    if not person:
        flash('No Planning Center account found for that email address.', 'error')
        return redirect(url_for('login', tab='pco'))

    attrs = person.get('attributes', {})
    first = attrs.get('first_name', '')
    last = attrs.get('last_name', '')
    full_name = f'{first} {last}'.strip()

    # Find existing local account by email, or create one
    user = User.query.filter_by(email=email).first()
    if not user:
        # Build a unique username from their PCO name
        base_username = full_name.lower().replace(' ', '.') if full_name else email.split('@')[0]
        base_username = ''.join(c for c in base_username if c.isalnum() or c == '.')[:40]
        username = base_username
        n = 1
        while User.query.filter_by(username=username).first():
            username = f'{base_username}{n}'
            n += 1
        user = User(username=username, email=email, role='user')
        user.set_password(secrets.token_hex(32))  # random — PCO users never need a local password
        db.session.add(user)
        db.session.commit()

    login_user(user)
    flash(f'Welcome, {user.username}!', 'success')
    return redirect(url_for('dashboard'))


@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))


# ─────────────────────────────────────────
# Dashboard
# ─────────────────────────────────────────

@app.route('/dashboard')
@login_required
def dashboard():
    if current_user.is_admin:
        checklists = Checklist.query.filter_by(is_active=True).order_by(Checklist.created_at.desc()).limit(8).all()
        in_progress = ChecklistCompletion.query.filter_by(signed_off=False).order_by(ChecklistCompletion.started_at.desc()).limit(8).all()
        completions = ChecklistCompletion.query.filter_by(signed_off=True).order_by(ChecklistCompletion.started_at.desc()).limit(8).all()
        total_checklists = Checklist.query.count()
        total_completions = ChecklistCompletion.query.filter_by(signed_off=True).count()
        total_in_progress = ChecklistCompletion.query.filter_by(signed_off=False).count()
        total_users = User.query.count()
        total_teams = Team.query.count()
        return render_template('admin_dashboard.html', checklists=checklists,
                               in_progress=in_progress,
                               completions=completions,
                               total_checklists=total_checklists,
                               total_completions=total_completions,
                               total_in_progress=total_in_progress,
                               total_users=total_users,
                               total_teams=total_teams)
    else:
        from sqlalchemy import or_
        user_team_ids = [t.id for t in current_user.teams]
        role_filter = or_(
            Checklist.assigned_role == None,
            Checklist.assigned_role == '',
            Checklist.assigned_role == current_user.role,
            Checklist.team_id.in_(user_team_ids) if user_team_ids else db.false()
        )
        checklists = Checklist.query.filter(
            Checklist.is_active == True,
            role_filter
        ).order_by(Checklist.created_at.desc()).all()
        # Group by team; no-team checklists go into 'Default'
        grouped = {}
        for cl in checklists:
            key = cl.team.name if cl.team else 'Default'
            grouped.setdefault(key, []).append(cl)
        grouped_checklists = sorted(grouped.items(), key=lambda x: (x[0] == 'Default', x[0].lower()))
        my_completions = ChecklistCompletion.query.filter_by(
            user_id=current_user.id
        ).order_by(ChecklistCompletion.started_at.desc()).limit(5).all()
        return render_template('user_dashboard.html', grouped_checklists=grouped_checklists,
                               my_completions=my_completions)


# ─────────────────────────────────────────
# Admin: Checklist Management
# ─────────────────────────────────────────

@app.route('/admin/checklists')
@login_required
@admin_required
def admin_checklists():
    checklists = Checklist.query.order_by(Checklist.created_at.desc()).all()
    return render_template('admin_checklists.html', checklists=checklists)


@app.route('/admin/checklists/new', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_checklist_new():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        description = request.form.get('description', '').strip()
        assigned_role = request.form.get('assigned_role', '').strip() or None
        team_id = request.form.get('team_id', '').strip() or None
        if not name:
            flash('Name is required.', 'error')
            return render_template('admin_checklist_form.html', checklist=None,
                                   roles=_get_available_roles(), teams=Team.query.order_by(Team.name).all())
        checklist = Checklist(
            name=name,
            description=description,
            created_by=current_user.id,
            assigned_role=assigned_role,
            team_id=int(team_id) if team_id else None
        )
        db.session.add(checklist)
        db.session.flush()

        # Items
        titles = request.form.getlist('item_title[]')
        descriptions = request.form.getlist('item_description[]')
        required_flags = request.form.getlist('item_required[]')
        photos = request.files.getlist('item_visual_aid_photo[]')
        for i, title in enumerate(titles):
            if title.strip():
                photo_filename = None
                if i < len(photos):
                    photo = photos[i]
                    if photo and photo.filename and allowed_file(photo.filename):
                        file_obj, ext = normalize_image(photo)
                        fname = secure_filename(f'va_cl{checklist.id}_pos{i}_{int(datetime.now().timestamp()*1000)}.{ext}')
                        upload_image(file_obj, fname)
                        photo_filename = fname
                item = ChecklistItem(
                    checklist_id=checklist.id,
                    title=title.strip(),
                    description=descriptions[i] if i < len(descriptions) else '',
                    is_required=(str(i) in required_flags),
                    order_index=i,
                    visual_aid_photo=photo_filename
                )
                db.session.add(item)
        db.session.commit()
        flash('Checklist created successfully!', 'success')
        return redirect(url_for('admin_checklists'))
    return render_template('admin_checklist_form.html', checklist=None,
                           roles=Role.query.order_by(Role.name).all(), teams=Team.query.order_by(Team.name).all())


@app.route('/admin/checklists/<int:checklist_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_checklist_edit(checklist_id):
    checklist = Checklist.query.get_or_404(checklist_id)
    if request.method == 'POST':
        checklist.name = request.form.get('name', '').strip()
        checklist.description = request.form.get('description', '').strip()
        checklist.is_active = 'is_active' in request.form
        checklist.assigned_role = request.form.get('assigned_role', '').strip() or None
        team_id = request.form.get('team_id', '').strip() or None
        checklist.team_id = int(team_id) if team_id else None

        # Remove old items - delete item_responses first due to FK constraint
        item_ids = [item.id for item in checklist.items]
        if item_ids:
            ItemResponse.query.filter(ItemResponse.item_id.in_(item_ids)).delete(synchronize_session='fetch')
        ChecklistItem.query.filter_by(checklist_id=checklist.id).delete()

        titles = request.form.getlist('item_title[]')
        descriptions = request.form.getlist('item_description[]')
        required_flags = request.form.getlist('item_required[]')
        photos = request.files.getlist('item_visual_aid_photo[]')
        existing_photos = request.form.getlist('item_visual_aid_photo_existing[]')
        for i, title in enumerate(titles):
            if title.strip():
                photo_filename = existing_photos[i] if i < len(existing_photos) else None
                if i < len(photos):
                    photo = photos[i]
                    if photo and photo.filename and allowed_file(photo.filename):
                        file_obj, ext = normalize_image(photo)
                        fname = secure_filename(f'va_cl{checklist.id}_pos{i}_{int(datetime.now().timestamp()*1000)}.{ext}')
                        upload_image(file_obj, fname)
                        photo_filename = fname
                item = ChecklistItem(
                    checklist_id=checklist.id,
                    title=title.strip(),
                    description=descriptions[i] if i < len(descriptions) else '',
                    is_required=(str(i) in required_flags),
                    order_index=i,
                    visual_aid_photo=photo_filename or None
                )
                db.session.add(item)
        db.session.commit()
        flash('Checklist updated!', 'success')
        return redirect(url_for('admin_checklists'))
    return render_template('admin_checklist_form.html', checklist=checklist,
                           roles=Role.query.order_by(Role.name).all(), teams=Team.query.order_by(Team.name).all())


@app.route('/admin/checklists/<int:checklist_id>/delete', methods=['POST'])
@login_required
@admin_required
def admin_checklist_delete(checklist_id):
    checklist = Checklist.query.get_or_404(checklist_id)
    # Pre-delete item_responses referencing this checklist's items to satisfy FK constraint
    item_ids = [item.id for item in checklist.items]
    if item_ids:
        ItemResponse.query.filter(ItemResponse.item_id.in_(item_ids)).delete(synchronize_session='fetch')
    db.session.delete(checklist)
    db.session.commit()
    flash('Checklist deleted.', 'info')
    return redirect(url_for('admin_checklists'))


@app.route('/admin/checklists/<int:checklist_id>/duplicate', methods=['POST'])
@login_required
@admin_required
def admin_checklist_duplicate(checklist_id):
    original = Checklist.query.get_or_404(checklist_id)
    copy = Checklist(
        name=f'Copy of {original.name}',
        description=original.description,
        created_by=current_user.id,
        is_active=False,
        assigned_role=original.assigned_role
    )
    db.session.add(copy)
    db.session.flush()  # get copy.id before committing
    for item in original.items:
        db.session.add(ChecklistItem(
            checklist_id=copy.id,
            title=item.title,
            description=item.description,
            is_required=item.is_required,
            order_index=item.order_index,
            visual_aid_photo=item.visual_aid_photo
        ))
    db.session.commit()
    flash(f'Checklist duplicated as "{copy.name}".', 'success')
    return redirect(url_for('admin_checklist_edit', checklist_id=copy.id))


@app.route('/admin/checklists/<int:checklist_id>/history')
@login_required
@admin_required
def admin_checklist_history(checklist_id):
    cl = Checklist.query.get_or_404(checklist_id)
    completions = ChecklistCompletion.query.filter_by(checklist_id=checklist_id)\
        .order_by(ChecklistCompletion.started_at.desc()).all()
    return render_template('admin_checklist_history.html', checklist=cl, completions=completions)


@app.route('/admin/checklists/<int:checklist_id>/export-pdf')
@login_required
@admin_required
def admin_checklist_export_pdf(checklist_id):
    cl = Checklist.query.get_or_404(checklist_id)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=0.85 * inch,
        rightMargin=0.85 * inch,
        topMargin=0.85 * inch,
        bottomMargin=0.85 * inch,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('title', parent=styles['Normal'],
                                 fontSize=18, fontName='Helvetica-Bold',
                                 spaceAfter=4, leading=22)
    desc_style  = ParagraphStyle('desc', parent=styles['Normal'],
                                 fontSize=10, textColor=colors.HexColor('#6b7280'),
                                 spaceAfter=2)
    meta_style  = ParagraphStyle('meta', parent=styles['Normal'],
                                 fontSize=9, textColor=colors.HexColor('#9ca3af'))
    item_title_style = ParagraphStyle('item_title', parent=styles['Normal'],
                                      fontSize=11, fontName='Helvetica-Bold',
                                      leading=14)
    item_desc_style  = ParagraphStyle('item_desc', parent=styles['Normal'],
                                      fontSize=9, textColor=colors.HexColor('#6b7280'),
                                      leading=12, spaceBefore=2)
    req_style = ParagraphStyle('req', parent=styles['Normal'],
                               fontSize=8, textColor=colors.HexColor('#ef4444'),
                               fontName='Helvetica-Bold')
    sig_label_style = ParagraphStyle('sig_label', parent=styles['Normal'],
                                     fontSize=9, textColor=colors.HexColor('#6b7280'))

    story = []

    # Header
    story.append(Paragraph(cl.name, title_style))
    if cl.description:
        story.append(Paragraph(cl.description, desc_style))
    story.append(Paragraph(
        f'{len(cl.items)} item{"s" if len(cl.items) != 1 else ""}  ·  '
        f'Exported {datetime.utcnow().strftime("%B %-d, %Y")}',
        meta_style
    ))
    story.append(Spacer(1, 0.25 * inch))
    story.append(HRFlowable(width='100%', thickness=1,
                             color=colors.HexColor('#e5e7eb'), spaceAfter=0.2 * inch))

    # Items
    usable_width = letter[0] - 1.7 * inch
    box_size = 14
    col_widths = [box_size + 8, usable_width - box_size - 8]

    max_img_width  = 2.5 * inch
    max_img_height = 2.0 * inch

    for item in cl.items:
        # Build right-side content
        right_content = [Paragraph(item.title, item_title_style)]
        if item.is_required:
            right_content.append(Paragraph('REQUIRED', req_style))
        if item.description:
            right_content.append(Paragraph(item.description, item_desc_style))
        if item.visual_aid_photo:
            img_data = image_bytes_for_pdf(item.visual_aid_photo)
            if img_data:
                img = RLImage(img_data, width=max_img_width, height=max_img_height,
                              kind='proportional')
                right_content.append(Spacer(1, 4))
                right_content.append(img)

        checkbox_cell = Table(
            [['']], colWidths=[box_size], rowHeights=[box_size]
        )
        checkbox_cell.setStyle(TableStyle([
            ('BOX', (0, 0), (-1, -1), 1.5, colors.HexColor('#374151')),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))

        row_table = Table(
            [[checkbox_cell, right_content]],
            colWidths=col_widths,
        )
        row_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
        ]))
        story.append(row_table)

    # Signature block
    story.append(Spacer(1, 0.3 * inch))
    story.append(HRFlowable(width='100%', thickness=1,
                             color=colors.HexColor('#e5e7eb'), spaceAfter=0.25 * inch))

    sig_col = (usable_width - 0.3 * inch) / 2
    sig_table = Table(
        [[
            [Paragraph('Completed by', sig_label_style), Spacer(1, 0.35 * inch),
             HRFlowable(width='100%', thickness=0.75, color=colors.HexColor('#374151'))],
            '',
            [Paragraph('Date', sig_label_style), Spacer(1, 0.35 * inch),
             HRFlowable(width='100%', thickness=0.75, color=colors.HexColor('#374151'))],
        ]],
        colWidths=[sig_col, 0.3 * inch, sig_col],
    )
    sig_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'BOTTOM'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    story.append(sig_table)

    doc.build(story)
    buf.seek(0)

    filename = f"{cl.name.replace(' ', '_')}.pdf"
    response = make_response(buf.read())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@app.route('/admin/completions')
@login_required
@admin_required
def admin_completions():
    completions = ChecklistCompletion.query.order_by(
        ChecklistCompletion.started_at.desc()
    ).all()
    return render_template('admin_completions.html', completions=completions)


@app.route('/admin/completions/<int:completion_id>')
@login_required
@admin_required
def admin_completion_detail(completion_id):
    completion = ChecklistCompletion.query.get_or_404(completion_id)
    return render_template('view_completion.html', completion=completion)


@app.route('/admin/users')
@login_required
@admin_required
def admin_users():
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template('admin_users.html', users=users)


@app.route('/admin/users/<int:user_id>/reset-password', methods=['POST'])
@login_required
@admin_required
def admin_user_reset_password(user_id):
    user = User.query.get_or_404(user_id)
    alphabet = string.ascii_letters + string.digits + '!@#$%'
    temp_password = ''.join(secrets.choice(alphabet) for _ in range(12))
    user.set_password(temp_password)
    db.session.commit()
    flash(f'Password for {user.username} reset to: {temp_password}', 'info')
    return redirect(url_for('admin_users'))


@app.route('/admin/users/<int:user_id>/set-role', methods=['POST'])
@login_required
@admin_required
def admin_user_set_role(user_id):
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash('You cannot change your own role.', 'error')
        return redirect(url_for('admin_users'))
    new_role = request.form.get('role')
    if new_role not in ('admin', 'user'):
        flash('Invalid role.', 'error')
        return redirect(url_for('admin_users'))
    user.role = new_role
    db.session.commit()
    flash(f'{user.username} is now {new_role}.', 'success')
    return redirect(url_for('admin_users'))


@app.route('/admin/users/<int:user_id>/delete', methods=['POST'])
@login_required
@admin_required
def admin_user_delete(user_id):
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash('You cannot delete your own account.', 'error')
        return redirect(url_for('admin_users'))
    # Delete completions + their item responses
    completions = ChecklistCompletion.query.filter_by(user_id=user.id).all()
    for comp in completions:
        ItemResponse.query.filter_by(completion_id=comp.id).delete()
        db.session.delete(comp)
    db.session.delete(user)
    db.session.commit()
    flash(f'User {user.username} deleted.', 'info')
    return redirect(url_for('admin_users'))


@app.route('/account/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        current_pw = request.form.get('current_password', '')
        new_pw = request.form.get('new_password', '')
        confirm_pw = request.form.get('confirm_password', '')
        if not current_user.check_password(current_pw):
            flash('Current password is incorrect.', 'error')
        elif len(new_pw) < 8:
            flash('New password must be at least 8 characters.', 'error')
        elif new_pw != confirm_pw:
            flash('New passwords do not match.', 'error')
        else:
            current_user.set_password(new_pw)
            db.session.commit()
            flash('Password updated successfully.', 'success')
            return redirect(url_for('dashboard'))
    return render_template('change_password.html')


@app.route('/admin/users/new', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_user_new():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        role = request.form.get('role', 'user')
        if User.query.filter_by(username=username).first():
            flash('Username already exists.', 'error')
        elif User.query.filter_by(email=email).first():
            flash('Email already exists.', 'error')
        else:
            user = User(username=username, email=email, role=role)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            flash(f'User {username} created!', 'success')
            return redirect(url_for('admin_users'))
    return render_template('admin_user_form.html')


# ─────────────────────────────────────────
# User: Checklist Completion
# ─────────────────────────────────────────

@app.route('/checklist/<int:checklist_id>/start', methods=['POST'])
@login_required
def start_checklist(checklist_id):
    checklist = Checklist.query.get_or_404(checklist_id)
    # Check for existing in-progress completion
    existing = ChecklistCompletion.query.filter_by(
        checklist_id=checklist_id,
        user_id=current_user.id,
        signed_off=False
    ).first()
    if existing:
        return redirect(url_for('do_checklist', completion_id=existing.id))

    completion = ChecklistCompletion(
        checklist_id=checklist_id,
        user_id=current_user.id
    )
    db.session.add(completion)
    db.session.flush()

    for item in checklist.items:
        response = ItemResponse(completion_id=completion.id, item_id=item.id)
        db.session.add(response)
    db.session.commit()
    return redirect(url_for('do_checklist', completion_id=completion.id))


@app.route('/checklist/completion/<int:completion_id>', methods=['GET', 'POST'])
@login_required
def do_checklist(completion_id):
    completion = ChecklistCompletion.query.get_or_404(completion_id)
    if completion.user_id != current_user.id and not current_user.is_admin:
        flash('Access denied.', 'error')
        return redirect(url_for('dashboard'))
    if completion.signed_off:
        return redirect(url_for('view_completion', completion_id=completion_id))

    if request.method == 'POST':
        action = request.form.get('action')
        # Save item states
        for response in completion.item_responses:
            checked = request.form.get(f'item_{response.item_id}') == 'on'
            notes = request.form.get(f'notes_{response.item_id}', '').strip()
            if checked and not response.is_checked:
                response.checked_at = datetime.utcnow()
            response.is_checked = checked
            response.notes = notes
        completion.overall_notes = request.form.get('overall_notes', '').strip()

        if action == 'sign_off':
            sig_name = request.form.get('signature_name', '').strip()
            if not sig_name:
                flash('Please enter your name to sign off.', 'error')
                db.session.commit()
                return redirect(url_for('do_checklist', completion_id=completion_id))
            # Check all required items
            required_unchecked = []
            for response in completion.item_responses:
                if response.item.is_required and not response.is_checked:
                    required_unchecked.append(response.item.title)
            if required_unchecked:
                flash(f'Please complete all required items before signing off. Missing: {", ".join(required_unchecked[:3])}', 'error')
                db.session.commit()
                return redirect(url_for('do_checklist', completion_id=completion_id))
            completion.signed_off = True
            completion.completed_at = datetime.utcnow()
            completion.signature_name = sig_name
            db.session.commit()
            flash('Checklist signed off successfully!', 'success')
            return redirect(url_for('view_completion', completion_id=completion_id))
        else:
            db.session.commit()
            flash('Progress saved.', 'success')
    return render_template('do_checklist.html', completion=completion)


@app.route('/checklist/completion/<int:completion_id>/cancel', methods=['POST'])
@login_required
def cancel_checklist(completion_id):
    completion = ChecklistCompletion.query.get_or_404(completion_id)
    if completion.user_id != current_user.id and not current_user.is_admin:
        flash('Access denied.', 'error')
        return redirect(url_for('dashboard'))
    if completion.signed_off:
        flash('Cannot cancel a signed-off checklist.', 'error')
        return redirect(url_for('view_completion', completion_id=completion_id))
    ItemResponse.query.filter_by(completion_id=completion_id).delete()
    db.session.delete(completion)
    db.session.commit()
    flash('Checklist cancelled.', 'info')
    return redirect(url_for('dashboard'))


@app.route('/checklist/completion/<int:completion_id>/view')
@login_required
def view_completion(completion_id):
    completion = ChecklistCompletion.query.get_or_404(completion_id)
    if completion.user_id != current_user.id and not current_user.is_admin:
        flash('Access denied.', 'error')
        return redirect(url_for('dashboard'))
    return render_template('view_completion.html', completion=completion)


# ─────────────────────────────────────────
# Admin: Roles
# ─────────────────────────────────────────

@app.route('/admin/roles')
@login_required
@admin_required
def admin_roles():
    roles = Role.query.order_by(Role.name).all()
    teams = Team.query.order_by(Team.name).all()
    return render_template('admin_roles.html', roles=roles, teams=teams)


@app.route('/admin/roles/new', methods=['POST'])
@login_required
@admin_required
def admin_role_new():
    name = request.form.get('name', '').strip()
    if not name:
        flash('Role name is required.', 'error')
        return redirect(url_for('admin_roles'))
    if Role.query.filter_by(name=name).first():
        flash(f'Role "{name}" already exists.', 'error')
        return redirect(url_for('admin_roles'))
    db.session.add(Role(name=name))
    db.session.commit()
    flash(f'Role "{name}" created.', 'success')
    return redirect(url_for('admin_roles'))


@app.route('/admin/roles/<int:role_id>/set-team', methods=['POST'])
@login_required
@admin_required
def admin_role_set_team(role_id):
    role = Role.query.get_or_404(role_id)
    team_id = request.form.get('team_id', '').strip() or None
    role.team_id = int(team_id) if team_id else None
    db.session.commit()
    return redirect(url_for('admin_roles'))


@app.route('/admin/roles/<int:role_id>/delete', methods=['POST'])
@login_required
@admin_required
def admin_role_delete(role_id):
    role = Role.query.get_or_404(role_id)
    db.session.delete(role)
    db.session.commit()
    flash(f'Role "{role.name}" deleted.', 'info')
    return redirect(url_for('admin_roles'))


# ─────────────────────────────────────────
# Admin: Teams
# ─────────────────────────────────────────

@app.route('/admin/teams')
@login_required
@admin_required
def admin_teams():
    teams = Team.query.order_by(Team.name).all()
    return render_template('admin_teams.html', teams=teams)


@app.route('/admin/teams/new', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_team_new():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        description = request.form.get('description', '').strip()
        member_ids = request.form.getlist('member_ids')
        if not name:
            flash('Team name is required.', 'error')
            return render_template('admin_team_form.html', team=None,
                                   all_users=User.query.order_by(User.username).all())
        color = request.form.get('color', '#111111').strip() or '#111111'
        team = Team(name=name, description=description or None, color=color)
        for uid in member_ids:
            user = User.query.get(int(uid))
            if user:
                team.members.append(user)
        db.session.add(team)
        db.session.commit()
        flash(f'Team "{name}" created.', 'success')
        return redirect(url_for('admin_teams'))
    return render_template('admin_team_form.html', team=None,
                           all_users=User.query.order_by(User.username).all())


@app.route('/admin/teams/<int:team_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_team_edit(team_id):
    team = Team.query.get_or_404(team_id)
    if request.method == 'POST':
        team.name = request.form.get('name', '').strip()
        team.description = request.form.get('description', '').strip() or None
        team.color = request.form.get('color', '#111111').strip() or '#111111'
        member_ids = request.form.getlist('member_ids')
        team.members = []
        for uid in member_ids:
            user = User.query.get(int(uid))
            if user:
                team.members.append(user)
        db.session.commit()
        flash(f'Team "{team.name}" updated.', 'success')
        return redirect(url_for('admin_teams'))
    return render_template('admin_team_form.html', team=team,
                           all_users=User.query.order_by(User.username).all())


@app.route('/admin/teams/<int:team_id>/view')
@login_required
@admin_required
def admin_team_view(team_id):
    team = Team.query.get_or_404(team_id)
    return render_template('admin_team_detail.html', team=team)


@app.route('/admin/users/<int:user_id>/teams', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_user_teams(user_id):
    user = User.query.get_or_404(user_id)
    if request.method == 'POST':
        team_ids = request.form.getlist('team_ids')
        user.teams = []
        for tid in team_ids:
            team = Team.query.get(int(tid))
            if team:
                user.teams.append(team)
        db.session.commit()
        flash(f'Teams updated for {user.username}.', 'success')
        return redirect(url_for('admin_users'))
    return render_template('admin_user_teams.html', user=user,
                           all_teams=Team.query.order_by(Team.name).all())


@app.route('/admin/teams/<int:team_id>/delete', methods=['POST'])
@login_required
@admin_required
def admin_team_delete(team_id):
    team = Team.query.get_or_404(team_id)
    # Unlink checklists
    Checklist.query.filter_by(team_id=team.id).update({'team_id': None})
    db.session.delete(team)
    db.session.commit()
    flash(f'Team "{team.name}" deleted.', 'info')
    return redirect(url_for('admin_teams'))


# ─────────────────────────────────────────
# API: Auto-save item via AJAX
# ─────────────────────────────────────────

@app.route('/api/completion/<int:completion_id>/item/<int:item_id>', methods=['POST'])
@login_required
def api_save_item(completion_id, item_id):
    completion = ChecklistCompletion.query.get_or_404(completion_id)
    if completion.user_id != current_user.id:
        return jsonify({'error': 'Forbidden'}), 403
    if completion.signed_off:
        return jsonify({'error': 'Already signed off'}), 400
    data = request.get_json()
    response = ItemResponse.query.filter_by(
        completion_id=completion_id, item_id=item_id
    ).first()
    if response:
        response.is_checked = data.get('checked', False)
        response.notes = data.get('notes', '')
        if response.is_checked and not response.checked_at:
            response.checked_at = datetime.utcnow()
        db.session.commit()
        # Recalculate progress
        all_resp = ItemResponse.query.filter_by(completion_id=completion_id).all()
        checked_count = sum(1 for r in all_resp if r.is_checked)
        progress = int((checked_count / len(all_resp)) * 100) if all_resp else 0
        return jsonify({'success': True, 'progress': progress})
    return jsonify({'error': 'Not found'}), 404


# ─────────────────────────────────────────
# Init DB & Seed Admin
# ─────────────────────────────────────────

def init_db():
    db.create_all()
    # PostgreSQL-only migrations (db.create_all won't handle existing tables)
    if db.engine.dialect.name == 'postgresql':
        with db.engine.connect() as conn:
            conn.execute(db.text(
                "ALTER TABLE item_responses ADD COLUMN IF NOT EXISTS photo_filename VARCHAR(256)"
            ))
            conn.execute(db.text(
                "ALTER TABLE checklist_items ADD COLUMN IF NOT EXISTS visual_aid_photo VARCHAR(256)"
            ))
            conn.execute(db.text(
                "ALTER TABLE checklists ADD COLUMN IF NOT EXISTS assigned_role VARCHAR(80)"
            ))
            conn.execute(db.text(
                "ALTER TABLE checklists DROP COLUMN IF EXISTS scheduled_date"
            ))
            conn.execute(db.text(
                "ALTER TABLE checklists ADD COLUMN IF NOT EXISTS team_id INTEGER REFERENCES teams(id)"
            ))
            conn.execute(db.text(
                "ALTER TABLE roles ADD COLUMN IF NOT EXISTS team_id INTEGER REFERENCES teams(id)"
            ))
            conn.execute(db.text(
                "ALTER TABLE teams ADD COLUMN IF NOT EXISTS color VARCHAR(7) DEFAULT '#111111'"
            ))
            conn.commit()
    admin = User.query.filter_by(username='admin').first()
    if not admin:
        admin = User(username='admin', email='admin@readiness.local', role='admin')
        admin.set_password('Admin@1234')
        db.session.add(admin)
        db.session.commit()
        print('Default admin created: admin / Admin@1234')


with app.app_context():
    init_db()

if __name__ == '__main__':
    app.run(debug=True, port=int(os.environ.get('PORT', 5000)))
