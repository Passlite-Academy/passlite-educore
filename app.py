from flask import Flask, render_template, request, redirect, url_for, session, flash, make_response, send_file, current_app
import sqlite3
import os
import base64
from io import BytesIO
from xhtml2pdf import pisa
import requests

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'your_fallback_secret_key_here')

PAYSTACK_SECRET_KEY = os.environ.get('PAYSTACK_SECRET_KEY', 'your_paystack_secret_key_here')

def get_db_connection():
    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            school_name TEXT,
            address TEXT,
            motto TEXT,
            email TEXT,
            phone TEXT,
            logo TEXT,
            vacation_date TEXT,
            resumption_date TEXT,
            academic_session TEXT,
            current_term TEXT,
            school_type TEXT DEFAULT 'Junior',
            head_teacher_signature TEXT,
            teacher_signature TEXT
        )
    """)

    # School subscriptions table for termly and session tracking
    conn.execute("""
        CREATE TABLE IF NOT EXISTS school_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            academic_session TEXT NOT NULL,
            term TEXT NOT NULL,
            status TEXT NOT NULL,
            reference TEXT,
            amount REAL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    for col, col_type in [
        ('address', 'TEXT'), ('motto', 'TEXT'), ('phone', 'TEXT'), 
        ('email', 'TEXT'), ('logo', 'TEXT'), ('vacation_date', 'TEXT'), 
        ('resumption_date', 'TEXT'), ('academic_session', 'TEXT'), ('current_term', 'TEXT'),
        ('school_type', 'TEXT'), ('head_teacher_signature', 'TEXT'), ('teacher_signature', 'TEXT')
    ]:
        try:
            conn.execute(f"ALTER TABLE users ADD COLUMN {col} {col_type}")
        except sqlite3.OperationalError:
            pass

    conn.execute("""
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            class TEXT NOT NULL,
            roll_number TEXT,
            sex TEXT,
            department TEXT,
            dob TEXT,
            photo TEXT,
            attendance_present INTEGER DEFAULT 0,
            attendance_absent INTEGER DEFAULT 0,
            total_school_days INTEGER DEFAULT 0,
            award_won TEXT,
            bill_debt REAL DEFAULT 0,
            bill_school_fees REAL DEFAULT 0,
            bill_computer REAL DEFAULT 0,
            bill_lessons REAL DEFAULT 0,
            bill_utility REAL DEFAULT 0,
            class_teacher_comment TEXT,
            head_teacher_comment TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    for col, col_type in [('photo', 'TEXT')]:
        try:
            conn.execute(f"ALTER TABLE students ADD COLUMN {col} {col_type}")
        except sqlite3.OperationalError:
            pass

    conn.execute("""
        CREATE TABLE IF NOT EXISTS teachers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            username TEXT UNIQUE,
            password TEXT,
            subjects TEXT,
            phone TEXT,
            email TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    for col, col_type in [('username', 'TEXT'), ('password', 'TEXT'), ('subjects', 'TEXT')]:
        try:
            conn.execute(f"ALTER TABLE teachers ADD COLUMN {col} {col_type}")
        except sqlite3.OperationalError:
            pass

    conn.execute("""
        CREATE TABLE IF NOT EXISTS behavior_ratings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            trait TEXT NOT NULL,
            rating INTEGER DEFAULT 1,
            academic_session TEXT,
            term TEXT,
            FOREIGN KEY (student_id) REFERENCES students(id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS marks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            subject TEXT NOT NULL,
            test_score REAL DEFAULT 0,
            exam_score REAL DEFAULT 0,
            last_cumm REAL DEFAULT 0,
            academic_session TEXT,
            term TEXT,
            FOREIGN KEY (student_id) REFERENCES students(id)
        )
    """)
    
    conn.commit()
    conn.close()

init_db()

# Subscription Middleware Gateway (Active enforcement enabled)
@app.before_request
def check_term_subscription():
    exempt_routes = ['static', 'login', 'register', 'select_term', 'payment_portal', 'pay', 'verify_payment', 'purchase', 'check_result', 'public_report_card', 'support', 'logout', 'teacher_login', 'teacher_logout']
    if request.endpoint in exempt_routes or not request.endpoint:
        return
    
    if 'user_id' in session:
        conn = get_db_connection()
        user = conn.execute('SELECT academic_session, current_term FROM users WHERE id = ?', (session['user_id'],)).fetchone()
        if user:
            sess = user['academic_session'] or '2026/2027'
            trm = user['current_term'] or 'First Term'
            sub = conn.execute('SELECT * FROM school_subscriptions WHERE user_id = ? AND academic_session = ? AND term = ? AND status = ?', 
                               (session['user_id'], sess, trm, 'active')).fetchone()
            conn.close()
            if not sub:
                return redirect(url_for('payment_portal'))
        else:
            conn.close()

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('select_term'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        login_input = request.form.get('username') or request.form.get('email') or ''
        password = request.form.get('password', '')
        
        conn = get_db_connection()
        user = conn.execute('SELECT * FROM users WHERE (username = ? OR email = ?) AND password = ?', (login_input, login_input, password)).fetchone()
        conn.close()
        
        if user:
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['email'] = user['email']
            return redirect(url_for('select_term'))
        else:
            flash('Invalid username/email or password', 'danger')
            
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        school_name = request.form.get('school_name', '')
        email = request.form.get('email', '')
        phone = request.form.get('phone', '')
        password = request.form['password']
        school_type = request.form.get('school_type', 'Junior')
        
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO users (username, password, school_name, email, phone, current_term, academic_session, school_type) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (username, password, school_name, email, phone, "First Term", "2026/2027", school_type))
            conn.commit()
            flash('Registration successful! Please log in.', 'success')
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash('Username already exists.', 'danger')
        finally:
            conn.close()
            
    return render_template('register.html')

@app.route('/select_term', methods=['GET', 'POST'])
def select_term():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    conn = get_db_connection()
    if request.method == 'POST':
        selected_session = request.form.get('academic_session', '2026/2027')
        selected_term = request.form.get('current_term', 'First Term')
        
        conn.execute('UPDATE users SET academic_session = ?, current_term = ? WHERE id = ?', 
                     (selected_session, selected_term, session['user_id']))
        conn.commit()
        conn.close()
        
        return redirect(url_for('dashboard'))
            
    user = conn.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    conn.close()
    
    academic_sessions = [f"{year}/{year+1}" for year in range(2020, 2046)]
    return render_template('select_term.html', user=user, academic_sessions=academic_sessions)

# ==========================================
# PAYSTACK PAYMENT & SUBSCRIPTION PORTAL
# ==========================================
@app.route('/payment_portal')
def payment_portal():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    conn = get_db_connection()
    user_row = conn.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    conn.close()
    
    user = dict(user_row) if user_row else {}
    
    # Default initial amount view fallback
    amount = 20000
    
    return render_template('payment_portal.html', user=user, amount=amount)

@app.route('/pay', methods=['POST'])
def pay():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    conn = get_db_connection()
    user_row = conn.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    conn.close()
    
    user = dict(user_row) if user_row else {}
    
    # Capture coverage type dynamically from the form selection
    coverage_type = request.form.get('coverage_type', 'multi_junior')
    academic_session = user.get('academic_session', '2026/2027')
    
    # Updated Multi-User Pricing Matrix
    pricing_tiers = {
        'multi_junior': 20000,
        'multi_senior': 25000,
        'multi_both': 40000,
        'multi_session': 100000
    }
    
    amount_naira = pricing_tiers.get(coverage_type, 20000)
    amount_kobo = amount_naira * 100
    email = user.get('email') or 'admin@passliteeducore.com'
    
    headers = {
        "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "email": email,
        "amount": amount_kobo,
        "callback_url": url_for('verify_payment', _external=True),
        "metadata": {
            "user_id": user.get('id'),
            "academic_session": academic_session,
            "current_term": user.get('current_term', 'First Term'),
            "coverage_type": coverage_type
        }
    }
    
    try:
        response = requests.post("https://api.paystack.co/transaction/initialize", json=payload, headers=headers)
        res_data = response.json()
        if res_data.get('status'):
            authorization_url = res_data['data']['authorization_url']
            return redirect(authorization_url)
        else:
            flash('Payment initialization failed. Please check Paystack keys.', 'danger')
            return redirect(url_for('payment_portal'))
    except Exception as e:
        flash(f'Connection error: {str(e)}', 'danger')
        return redirect(url_for('payment_portal'))

@app.route('/verify')
def verify_payment():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    reference = request.args.get('reference')
    if not reference:
        flash('No transaction reference found.', 'danger')
        return redirect(url_for('payment_portal'))
        
    headers = {"Authorization": f"Bearer {PAYSTACK_SECRET_KEY}"}
    try:
        response = requests.get(f"https://api.paystack.co/transaction/verify/{reference}", headers=headers)
        res_data = response.json()
        
        if res_data.get('status') and res_data['data']['status'] == 'success':
            data = res_data['data']
            metadata = data.get('metadata', {})
            user_id = metadata.get('user_id') or session.get('user_id')
            
            conn = get_db_connection()
            user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
            
            sess = metadata.get('academic_session') or user['academic_session']
            current_trm = metadata.get('current_term') or user['current_term']
            coverage_type = metadata.get('coverage_type')
            amount_paid = data['amount'] / 100
            
            # If Full Session selected, automatically unlock all 3 terms for this session
            if coverage_type == 'multi_session':
                all_terms = ['First Term', 'Second Term', 'Third Term']
                for t in all_terms:
                    existing_sub = conn.execute('SELECT id FROM school_subscriptions WHERE user_id = ? AND academic_session = ? AND term = ?', 
                                                (user_id, sess, t)).fetchone()
                    if not existing_sub:
                        conn.execute('''
                            INSERT INTO school_subscriptions (user_id, academic_session, term, status, reference, amount)
                            VALUES (?, ?, ?, ?, ?, ?)
                        ''', (user_id, sess, t, 'active', reference, amount_paid / 3))
            else:
                existing_sub = conn.execute('SELECT id FROM school_subscriptions WHERE user_id = ? AND academic_session = ? AND term = ?', 
                                            (user_id, sess, current_trm)).fetchone()
                if not existing_sub:
                    conn.execute('''
                        INSERT INTO school_subscriptions (user_id, academic_session, term, status, reference, amount)
                        VALUES (?, ?, ?, ?, ?, ?)
                    ''', (user_id, sess, current_trm, 'active', reference, amount_paid))
                    
            conn.commit()
            conn.close()
            
            flash('Subscription payment successful! Welcome to your dashboard.', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Payment verification failed.', 'danger')
            return redirect(url_for('payment_portal'))
    except Exception as e:
        flash(f'Verification error: {str(e)}', 'danger')
        return redirect(url_for('payment_portal'))

@app.route('/purchase')
def purchase():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    conn = get_db_connection()
    user_row = conn.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    conn.close()
    user = dict(user_row) if user_row else {}
    return render_template('purchase.html', user=user)

# ==========================================
# PUBLIC STUDENT RESULT CHECKER
# ==========================================
@app.route('/check-result', methods=['GET', 'POST'])
def check_result():
    if request.method == 'POST':
        roll_number = request.form.get('roll_number', '').strip()
        name_input = request.form.get('name_input', '').strip().lower()
        selected_session = request.form.get('academic_session', '2026/2027')
        selected_term = request.form.get('current_term', 'First Term')
        
        conn = get_db_connection()
        student = conn.execute(
            'SELECT * FROM students WHERE roll_number = ? AND LOWER(name) LIKE ?', 
            (roll_number, f'%{name_input}%')
        ).fetchone()
        conn.close()
        
        if student:
            session['public_view_session'] = selected_session
            session['public_view_term'] = selected_term
            return redirect(url_for('public_report_card', student_id=student['id']))
        else:
            flash('Invalid Roll Number or Student Name. Please check and try again.', 'danger')
            
    academic_sessions = [f"{year}/{year+1}" for year in range(2020, 2046)]
    return render_template('check_result.html', academic_sessions=academic_sessions)

@app.route('/public_report/<int:student_id>')
def public_report_card(student_id):
    context = get_report_card_context(student_id)
    if not context:
        return redirect(url_for('check_result'))
    return render_template('report_card.html', **context, auto_print=False, is_public=True)

# ==========================================
# SCHOOL ADMIN DASHBOARD
# ==========================================
@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    conn = get_db_connection()
    user_id = session.get('user_id')
    user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    school_name = user['school_name'] if user and 'school_name' in user.keys() else "School Dashboard"
    
    students_rows = conn.execute('SELECT * FROM students WHERE user_id = ?', (user_id,)).fetchall()
    total_students = len(students_rows)
    total_subjects = 20  
    
    teacher_count_row = conn.execute('SELECT COUNT(*) as count FROM teachers WHERE user_id = ?', (user_id,)).fetchone()
    total_teachers = teacher_count_row['count'] if teacher_count_row else 0
    
    conn.close()
    
    students = [dict(row) for row in students_rows]
    return render_template(
        'dashboard.html', 
        students=students, 
        school_name=school_name, 
        user=user,
        total_students=total_students,
        total_subjects=total_subjects,
        total_teachers=total_teachers
    )

# ==========================================
# ADMIN: TEACHER MANAGEMENT
# ==========================================
@app.route('/manage_teachers', methods=['GET', 'POST'])
def manage_teachers():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    conn = get_db_connection()
    user_id = session.get('user_id')
    user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    
    if request.method == 'POST':
        name = request.form.get('name')
        username = request.form.get('username')
        password = request.form.get('password')
        
        selected_subjects = request.form.getlist('subjects')
        subjects_str = ", ".join(selected_subjects) if selected_subjects else ""
        
        phone = request.form.get('phone', '')
        email = request.form.get('email', '')
        
        try:
            conn.execute('''
                INSERT INTO teachers (user_id, name, username, password, subjects, phone, email)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (user_id, name, username, password, subjects_str, phone, email))
            conn.commit()
            flash('Teacher added successfully with assigned subjects!', 'success')
        except sqlite3.IntegrityError:
            flash('Teacher username already exists. Choose another.', 'danger')
            
        return redirect(url_for('manage_teachers'))
        
    teachers = conn.execute('SELECT * FROM teachers WHERE user_id = ?', (user_id,)).fetchall()
    conn.close()
    
    return render_template('manage_teachers.html', user=user, teachers=teachers)

@app.route('/delete_teacher/<int:teacher_id>', methods=['POST'])
def delete_teacher(teacher_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    conn = get_db_connection()
    conn.execute('DELETE FROM teachers WHERE id = ? AND user_id = ?', (teacher_id, session['user_id']))
    conn.commit()
    conn.close()
    
    flash('Teacher account deleted successfully!', 'success')
    return redirect(url_for('manage_teachers'))

# ==========================================
# TEACHER AUTHENTICATION & PORTAL
# ==========================================
@app.route('/teacher/login', methods=['GET', 'POST'])
def teacher_login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        
        conn = get_db_connection()
        teacher = conn.execute('SELECT * FROM teachers WHERE username = ? AND password = ?', (username, password)).fetchone()
        conn.close()
        
        if teacher:
            session['teacher_id'] = teacher['id']
            session['teacher_name'] = teacher['name']
            session['user_id'] = teacher['user_id']
            return redirect(url_for('teacher_dashboard'))
        else:
            flash('Invalid teacher username or password', 'danger')
            
    return render_template('teacher_login.html')

@app.route('/teacher/dashboard')
def teacher_dashboard():
    if 'teacher_id' not in session:
        return redirect(url_for('teacher_login'))
        
    conn = get_db_connection()
    user_id = session.get('user_id')
    teacher_id = session.get('teacher_id')
    
    teacher = conn.execute('SELECT * FROM teachers WHERE id = ?', (teacher_id,)).fetchone()
    school = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    students = conn.execute('SELECT * FROM students WHERE user_id = ?', (user_id,)).fetchall()
    conn.close()
    
    return render_template('teacher_dashboard.html', teacher=teacher, school=school, students=students)

@app.route('/teacher/logout')
def teacher_logout():
    session.pop('teacher_id', None)
    session.pop('teacher_name', None)
    return redirect(url_for('teacher_login'))

# ==========================================
# STUDENT DIRECTORY, SCORESHEET & REPORTS
# ==========================================
@app.route('/student_list')
@app.route('/students')
@app.route('/view_students')
def student_list():
    if 'user_id' not in session and 'teacher_id' not in session:
        return redirect(url_for('login'))
        
    conn = get_db_connection()
    user_id = session.get('user_id')
    if not user_id and 'teacher_id' in session:
        t_row = conn.execute('SELECT user_id FROM teachers WHERE id = ?', (session['teacher_id'],)).fetchone()
        user_id = t_row['user_id'] if t_row else None

    user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    students_rows = conn.execute('SELECT * FROM students WHERE user_id = ?', (user_id,)).fetchall()
    conn.close()
    
    students = [dict(row) for row in students_rows]
    return render_template('student_list.html', students=students, user=user, view_mode='students')

@app.route('/score_sheet')
def score_sheet():
    if 'user_id' not in session and 'teacher_id' not in session:
        return redirect(url_for('login'))
    conn = get_db_connection()
    user_id = session.get('user_id')
    if not user_id and 'teacher_id' in session:
        t_row = conn.execute('SELECT user_id FROM teachers WHERE id = ?', (session['teacher_id'],)).fetchone()
        user_id = t_row['user_id'] if t_row else None

    user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    students_rows = conn.execute('SELECT * FROM students WHERE user_id = ?', (user_id,)).fetchall()
    conn.close()
    students = [dict(row) for row in students_rows]
    return render_template('student_list.html', students=students, user=user, view_mode='scores')

@app.route('/report_card_overview')
def report_card_overview():
    if 'user_id' not in session and 'teacher_id' not in session:
        return redirect(url_for('login'))
    conn = get_db_connection()
    user_id = session.get('user_id')
    if not user_id and 'teacher_id' in session:
        t_row = conn.execute('SELECT user_id FROM teachers WHERE id = ?', (session['teacher_id'],)).fetchone()
        user_id = t_row['user_id'] if t_row else None

    user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    students_rows = conn.execute('SELECT * FROM students WHERE user_id = ?', (user_id,)).fetchall()
    conn.close()
    students = [dict(row) for row in students_rows]
    return render_template('student_list.html', students=students, user=user, view_mode='reports')

# ==========================================
# SETTINGS & SCHOOL RECORD (WITH SIGNATURE OVERWRITE/REMOVAL)
# ==========================================
@app.route('/school_record')
def school_record():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    conn = get_db_connection()
    user_id = session.get('user_id')
    user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    
    total_students = conn.execute('SELECT COUNT(*) as count FROM students WHERE user_id = ?', (user_id,)).fetchone()['count']
    total_teachers = conn.execute('SELECT COUNT(*) as count FROM teachers WHERE user_id = ?', (user_id,)).fetchone()['count']
    
    conn.close()
    return render_template('school_record.html', user=user, total_students=total_students, total_teachers=total_teachers)

@app.route('/settings', methods=['GET', 'POST'])
@app.route('/school_settings', methods=['GET', 'POST'])
@app.route('/school-settings', methods=['GET', 'POST'])
def school_settings():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    conn = get_db_connection()
    
    if request.method == 'POST':
        school_name = request.form.get('school_name', '')
        address = request.form.get('address', '')
        motto = request.form.get('motto', '')
        phone = request.form.get('phone', '')
        email = request.form.get('email', '')
        vacation_date = request.form.get('vacation_date', '')
        resumption_date = request.form.get('resumption_date', '')
        academic_session = request.form.get('academic_session', '2026/2027')
        current_term = request.form.get('current_term', 'First Term')
        school_type = request.form.get('school_type', 'Junior')
        
        existing_user = conn.execute('SELECT logo, head_teacher_signature, teacher_signature FROM users WHERE id = ?', (session['user_id'],)).fetchone()
        
        os.makedirs('static/uploads', exist_ok=True)
        
        logo_file = request.files.get('logo')
        if logo_file and logo_file.filename != '':
            logo_filename = f"logo_user_{session['user_id']}.png"
            logo_path = os.path.join('static/uploads', logo_filename)
            logo_file.save(logo_path)
            logo_value = f"uploads/{logo_filename}"
        else:
            logo_value = existing_user['logo'] if existing_user else None

        # Teacher Signature Handling (Removal, Drawn Canvas, or Uploaded File)
        if request.form.get('remove_teacher_signature') == 'yes':
            t_sig_value = None
        else:
            drawn_t_sig = request.form.get('drawn_teacher_signature')
            if drawn_t_sig and drawn_t_sig.startswith('data:image'):
                header, encoded = drawn_t_sig.split(",", 1)
                data = base64.b64decode(encoded)
                t_filename = f"t_sig_{session['user_id']}.png"
                t_path = os.path.join('static/uploads', t_filename)
                with open(t_path, "wb") as f:
                    f.write(data)
                t_sig_value = f"uploads/{t_filename}"
            else:
                t_sig_file = request.files.get('teacher_signature')
                if t_sig_file and t_sig_file.filename != '':
                    t_filename = f"t_sig_{session['user_id']}.png"
                    t_path = os.path.join('static/uploads', t_filename)
                    t_sig_file.save(t_path)
                    t_sig_value = f"uploads/{t_filename}"
                else:
                    t_sig_value = existing_user['teacher_signature'] if existing_user else None

        # Head Teacher Signature Handling (Removal, Drawn Canvas, or Uploaded File)
        if request.form.get('remove_head_signature') == 'yes':
            ht_sig_value = None
        else:
            drawn_h_sig = request.form.get('drawn_head_signature')
            if drawn_h_sig and drawn_h_sig.startswith('data:image'):
                header, encoded = drawn_h_sig.split(",", 1)
                data = base64.b64decode(encoded)
                ht_filename = f"ht_sig_{session['user_id']}.png"
                ht_path = os.path.join('static/uploads', ht_filename)
                with open(ht_path, "wb") as f:
                    f.write(data)
                ht_sig_value = f"uploads/{ht_filename}"
            else:
                ht_sig_file = request.files.get('head_teacher_signature')
                if ht_sig_file and ht_sig_file.filename != '':
                    ht_filename = f"ht_sig_{session['user_id']}.png"
                    ht_path = os.path.join('static/uploads', ht_filename)
                    ht_sig_file.save(ht_path)
                    ht_sig_value = f"uploads/{ht_filename}"
                else:
                    ht_sig_value = existing_user['head_teacher_signature'] if existing_user else None
            
        conn.execute('''
            UPDATE users 
            SET school_name = ?, address = ?, motto = ?, phone = ?, email = ?, logo = ?, vacation_date = ?, resumption_date = ?, academic_session = ?, current_term = ?, school_type = ?, head_teacher_signature = ?, teacher_signature = ?
            WHERE id = ?
        ''', (school_name, address, motto, phone, email, logo_value, vacation_date, resumption_date, academic_session, current_term, school_type, ht_sig_value, t_sig_value, session['user_id']))
                         
        conn.commit()
        conn.close()
        flash('School settings and signatures updated successfully!', 'success')
        return redirect(url_for('dashboard'))
        
    user_row = conn.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    conn.close()
    user = dict(user_row) if user_row else {}
    
    try:
        return render_template('school_settings.html', user=user, settings=user)
    except:
        return render_template('settings.html', user=user, settings=user)

# ==========================================
# SUPPORT DESK ROUTE
# ==========================================
@app.route('/support', methods=['GET', 'POST'])
def support():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    conn = get_db_connection()
    user = conn.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    conn.close()
    
    if request.method == 'POST':
        subject = request.form.get('subject')
        message = request.form.get('message')
        flash('Your message has been sent to Passlite Support! We will reply via email shortly.', 'success')
        return redirect(url_for('support'))
        
    return render_template('support.html', user=user)

@app.route('/add_student', methods=['GET', 'POST'], endpoint='add_student')
@app.route('/student_entry', methods=['GET', 'POST'], endpoint='student_entry')
@app.route('/students/add', methods=['GET', 'POST'], endpoint='students_add')
def add_student():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    conn = get_db_connection()
    if request.method == 'POST':
        name = request.form['name']
        student_class = request.form['class']
        roll_number = request.form.get('roll_number', '')
        sex = request.form.get('sex', '')
        department = request.form.get('department', '')
        dob = request.form.get('dob', '')
        
        photo_filename = None
        photo_file = request.files.get('photo')
        if photo_file and photo_file.filename != '':
            os.makedirs('static/uploads', exist_ok=True)
            safe_roll = roll_number.replace('/', '_') if roll_number else 'student'
            photo_filename = f"uploads/student_{session['user_id']}_{safe_roll}.png"
            photo_path = os.path.join('static', photo_filename)
            photo_file.save(photo_path)
        
        conn.execute('''
            INSERT INTO students (user_id, name, class, roll_number, sex, department, dob, photo)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (session['user_id'], name, student_class, roll_number, sex, department, dob, photo_filename))
        conn.commit()
        conn.close()
        
        return redirect(url_for('dashboard'))
        
    user_row = conn.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    conn.close()
    user = dict(user_row) if user_row else {}

    try:
        return render_template('add_student.html', user=user)
    except:
        return render_template('student_entry.html', user=user)

# ==========================================
# MARKS ENTRY & BEHAVIOR RATINGS
# ==========================================
@app.route('/students/<int:student_id>/marks', methods=['GET', 'POST'])
@app.route('/marks_entry/<int:student_id>', methods=['GET', 'POST'])
def marks_entry(student_id):
    if 'user_id' not in session and 'teacher_id' not in session:
        return redirect(url_for('login'))
        
    conn = get_db_connection()
    user_id = session.get('user_id')
    teacher_subjects_list = None
    
    if 'teacher_id' in session:
        t_row = conn.execute('SELECT user_id, subjects FROM teachers WHERE id = ?', (session['teacher_id'],)).fetchone()
        if t_row:
            user_id = t_row['user_id']
            raw_subs = t_row['subjects'] or ''
            teacher_subjects_list = [s.strip() for s in raw_subs.split(',') if s.strip()]

    user_row = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    user = dict(user_row) if user_row else {}
    active_session = user.get('academic_session', '2026/2027')
    active_term = user.get('current_term', 'First Term')

    student_row = conn.execute('SELECT * FROM students WHERE id = ?', (student_id,)).fetchone()
    student = dict(student_row) if student_row else {}

    if teacher_subjects_list:
        subjects = teacher_subjects_list
    else:
        class_name = student.get('class', '').upper()
        if 'JS' in class_name or 'JSS' in class_name:
            subjects = [
                'English language', 'Mathematics', 'Physical and health education (PHE)', 'Social studies', 
                'Business studies', 'Basic Technology', 'Basic Science', 'Lit in English', 
                'Cultural and creative art (CCA)', 'Yoruba/Hausa/Igbo', 'Agric science', 'Home Economics', 
                'Computer studies/ICT', 'Data processing', 'Civic Education', 'CRS / IRK', 
                'French', 'Trade subject', 'Fine art', 'Food & Nutrition'
            ]
        else:
            subjects = [
                'English Language', 'Lit-in-English', 'Mathematics', 'Physics', 'Chemistry', 
                'Biology', 'Agric Science', 'Geography', 'Account', 'Commerce', 
                'Further Maths', 'Economics', 'Marketing', 'Government', 'ICT', 
                'Data Processing', 'Civic Education', 'Yoruba', 'C.R.S.', 'Music'
            ]
    
    if request.method == 'POST':
        present = request.form.get("attendance_present", 0)
        absent = request.form.get("attendance_absent", 0)
        total_days = request.form.get("total_school_days", 0)
        tc_comment = request.form.get("class_teacher_comment", "")
        hc_comment = request.form.get("head_teacher_comment", "")
        
        award_won = request.form.get("award_won", "")
        bill_debt = float(request.form.get("bill_debt", 0) or 0)
        bill_school_fees = float(request.form.get("bill_school_fees", 0) or 0)
        bill_computer = float(request.form.get("bill_computer", 0) or 0)
        bill_lessons = float(request.form.get("bill_lessons", 0) or 0)
        bill_utility = float(request.form.get("bill_utility", 0) or 0)

        if not teacher_subjects_list:
            conn.execute("""
                UPDATE students 
                SET attendance_present = ?, attendance_absent = ?, total_school_days = ?, 
                    award_won = ?, bill_debt = ?, bill_school_fees = ?, bill_computer = ?, 
                    bill_lessons = ?, bill_utility = ?,
                    class_teacher_comment = ?, head_teacher_comment = ?
                WHERE id = ?
            """, (present, absent, total_days, award_won, bill_debt, bill_school_fees, bill_computer, bill_lessons, bill_utility, tc_comment, hc_comment, student_id))

        for subj in subjects:
            key_slug = subj.lower().replace(" ", "_").replace("-", "_").replace("(", "").replace(")", "").replace("/", "_")
            test_val_str = request.form.get(f"test_{key_slug}", "").strip()
            exam_val_str = request.form.get(f"exam_{key_slug}", "").strip()
            last_val_str = request.form.get(f"last_{key_slug}", "").strip()
            
            conn.execute("DELETE FROM marks WHERE student_id = ? AND subject = ? AND academic_session = ? AND term = ?", (student_id, subj, active_session, active_term))

            if test_val_str != "" or exam_val_str != "" or last_val_str != "":
                test_val = float(test_val_str or 0)
                exam_val = float(exam_val_str or 0)
                last_val = float(last_val_str or 0)
                
                conn.execute(
                    "INSERT INTO marks (student_id, subject, test_score, exam_score, last_cumm, academic_session, term) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (student_id, subj, test_val, exam_val, last_val, active_session, active_term)
                )

        if not teacher_subjects_list:
            conn.execute("DELETE FROM behavior_ratings WHERE student_id = ? AND academic_session = ? AND term = ?", (student_id, active_session, active_term))
            all_traits = [
                'Creative', 'Verbal Fluency', 'Games', 'Sports', 'Handling tools', 'Drawing & Painting', 'Music Skills',
                'Punctuality', 'Neatness', 'Politeness', 'Honesty', 'Relationship with others', 'Leadership', 'Emotional Stability', 'Attitude to school', 'Attentiveness', 'Perseverance'
            ]
            
            for trait in all_traits:
                trait_key = trait.lower().replace(" ", "_").replace("&", "and")
                rating = request.form.get(trait_key, "1")
                conn.execute(
                    "INSERT INTO behavior_ratings (student_id, trait, rating, academic_session, term) VALUES (?, ?, ?, ?, ?)",
                    (student_id, trait, int(rating), active_session, active_term)
                )

        conn.commit()
        conn.close()
        
        if 'teacher_id' in session:
            return redirect(url_for('teacher_dashboard'))
        return redirect(url_for('report_card', student_id=student_id))

    marks = conn.execute('SELECT * FROM marks WHERE student_id = ? AND academic_session = ? AND term = ?', (student_id, active_session, active_term)).fetchall()
    behavior_rows = conn.execute('SELECT * FROM behavior_ratings WHERE student_id = ? AND academic_session = ? AND term = ?', (student_id, active_session, active_term)).fetchall()
    conn.close()
    
    marks_dict = {row['subject']: dict(row) for row in marks}
    behavior_dict = {row['trait']: row['rating'] for row in behavior_rows}
    
    return render_template('marks_entry.html', student=student, user=user, subjects=subjects, marks_dict=marks_dict, behavior_dict=behavior_dict)

# ==========================================
# REPORT CARDS & PDF EXPORTS
# ==========================================
@app.route('/report_card/<int:student_id>/pdf')
def report_card_pdf(student_id):
    context = get_report_card_context(student_id)
    if not context:
        return "Student not found", 404
        
    html = render_template('report_card.html', **context, auto_print=False)
    pdf_output = BytesIO()
    pisa_status = pisa.CreatePDF(BytesIO(html.encode('utf-8')), dest=pdf_output)
    
    if pisa_status.err:
        return "An error occurred while generating the PDF", 500
    
    pdf_output.seek(0)
    student_name = context['student'].get('name', 'student').replace(' ', '_')
    filename = f"report_card_{student_name}.pdf"
    
    return send_file(pdf_output, as_attachment=True, download_name=filename, mimetype='application/pdf')

@app.route('/report_card/<int:student_id>')
@app.route('/view_report/<int:student_id>')
@app.route('/report/<int:student_id>')
@app.route('/print_report/<int:student_id>')
@app.route('/generate_pdf/<int:student_id>')
def report_card(student_id):
    context = get_report_card_context(student_id)
    if not context:
        return redirect(url_for('check_result'))
    return render_template('report_card.html', **context, auto_print=False)

def get_report_card_context(student_id):
    conn = get_db_connection()
    student_row = conn.execute('SELECT * FROM students WHERE id = ?', (student_id,)).fetchone()
    if not student_row:
        conn.close()
        return None
        
    student = dict(student_row)
    user_row = conn.execute('SELECT * FROM users WHERE id = ?', (student['user_id'],)).fetchone()
    user = dict(user_row) if user_row else {}
    
    active_session = session.pop('public_view_session', None) or user.get('academic_session', '2026/2027')
    active_term = session.pop('public_view_term', None) or user.get('current_term', 'First Term')
    
    raw_marks = conn.execute('SELECT * FROM marks WHERE student_id = ? AND academic_session = ? AND term = ?', (student_id, active_session, active_term)).fetchall()
    behavior_ratings = conn.execute('SELECT * FROM behavior_ratings WHERE student_id = ? AND academic_session = ? AND term = ?', (student_id, active_session, active_term)).fetchall()
    conn.close()
    
    processed_marks = []
    total_obtained = 0
    
    for m in raw_marks:
        test = m['test_score'] or 0
        exam = m['exam_score'] or 0
        total = test + exam
        last_cumm = m['last_cumm'] or 0
        cumm = total + last_cumm if last_cumm > 0 else total
        
        if total >= 75: grade, remark = 'A1', 'Excellent'
        elif total >= 70: grade, remark = 'B2', 'Very Good'
        elif total >= 65: grade, remark = 'B3', 'Good'
        elif total >= 60: grade, remark = 'C4', 'Credit'
        elif total >= 55: grade, remark = 'C5', 'Credit'
        elif total >= 50: grade, remark = 'C6', 'Average'
        elif total >= 45: grade, remark = 'D7', 'Fair'
        elif total >= 40: grade, remark = 'E8', 'Pass'
        else: grade, remark = 'F9', 'Fail'
        
        total_obtained += total
        processed_marks.append({
            'subject': m['subject'],
            'test_score': test,
            'exam_score': exam,
            'total': total,
            'last_cumm': last_cumm,
            'cumm': cumm,
            'grade': grade,
            'remark': remark
        })
        
    max_possible = len(processed_marks) * 100 if processed_marks else 1
    percentage = (total_obtained / max_possible) * 100 if max_possible > 0 else 0
    
    if percentage >= 75: student_grade = 'A'
    elif percentage >= 70: student_grade = 'B'
    elif percentage >= 60: student_grade = 'C'
    elif percentage >= 50: student_grade = 'D'
    else: student_grade = 'F'

    return {
        'student': student,
        'user': user,
        'marks': processed_marks,
        'behavior_ratings': behavior_ratings,
        'total_obtained': total_obtained,
        'percentage': percentage,
        'student_grade': student_grade,
        'static_folder': os.path.join(current_app.root_path, 'static')
    }

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(debug=True, port=5002)