"""
Synaqmaker Resolver — Standalone ICPC-style Scoreboard Resolver.

Отдельный продукт для проведения ICPC-style разморозок (presentation mode).
Поддерживает импорт результатов из Codeforces и загрузку JSON-файлов.
"""
from gevent import monkey
monkey.patch_all()

import os
import re
import json
import time
import configparser
import io

from flask import (Flask, render_template, request, redirect, url_for,
                   jsonify, flash, session, send_file)
from flask_socketio import SocketIO
from flask_wtf.csrf import CSRFProtect, CSRFError
from werkzeug.security import check_password_hash, generate_password_hash
from functools import wraps

from db_manager import DBManager
from codeforces_import import (
    get_contest_preview, get_full_contest_data,
    filter_scoreboard_by_handles, filter_rows_by_participant_types,
    _get_participant_type, _get_participant_name, _get_participant_handle,
    _get_participant_org, fetch_contest_standings
)

# ──────────────────────────────────────────────────────────────────────────────
# App Setup
# ──────────────────────────────────────────────────────────────────────────────

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 64 * 1024 * 1024  # 64 MB
csrf = CSRFProtect(app)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, 'config.ini')

config = configparser.ConfigParser()
if not os.path.exists(CONFIG_PATH):
    _default_hash = generate_password_hash('resolver2025')
    default_config = f"""[security]
SECRET_KEY = {os.urandom(32).hex()}
ADMIN_PASSWORD = {_default_hash}

[server]
HOST = 0.0.0.0
PORT = 5050
"""
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        f.write(default_config)
    print(f"WARNING: config.ini не найден. Создан файл по умолчанию.")

config.read(CONFIG_PATH, encoding='utf-8')

try:
    app.secret_key = os.environ.get('SECRET_KEY') or config.get('security', 'SECRET_KEY').strip()
    _raw_password = config.get('security', 'ADMIN_PASSWORD').strip()
    # Support both hashed and plaintext passwords
    if _raw_password.startswith('scrypt:') or _raw_password.startswith('pbkdf2:'):
        ADMIN_PASSWORD = _raw_password
    else:
        ADMIN_PASSWORD = generate_password_hash(_raw_password)
    HOST = config.get('server', 'HOST', fallback='0.0.0.0')
    PORT = config.getint('server', 'PORT', fallback=5050)
except (configparser.NoSectionError, configparser.NoOptionError) as e:
    print(f"CRITICAL: Ошибка чтения config.ini ({e}). Используем defaults.")
    app.secret_key = os.urandom(32).hex()
    ADMIN_PASSWORD = generate_password_hash('resolver2025')
    HOST = '0.0.0.0'
    PORT = 5050

app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = 86400
app.config['WTF_CSRF_TIME_LIMIT'] = 7200
app.config['WTF_CSRF_SSL_STRICT'] = False

socketio = SocketIO(app, async_mode='gevent', cors_allowed_origins=None)
db = DBManager()

# ──────────────────────────────────────────────────────────────────────────────
# i18n
# ──────────────────────────────────────────────────────────────────────────────

LANGUAGES = {'en': 'English', 'ru': 'Русский', 'kz': 'Қазақша'}
DEFAULT_LANGUAGE = 'ru'

T = {
    'app_name': {'en': 'Synaqmaker Resolver', 'ru': 'Synaqmaker Resolver', 'kz': 'Synaqmaker Resolver'},
    'login': {'en': 'Login', 'ru': 'Войти', 'kz': 'Кіру'},
    'logout': {'en': 'Logout', 'ru': 'Выйти', 'kz': 'Шығу'},
    'back': {'en': 'Back', 'ru': 'Назад', 'kz': 'Артқа'},
    'error': {'en': 'Error', 'ru': 'Ошибка', 'kz': 'Қате'},
    'success': {'en': 'Success', 'ru': 'Успешно', 'kz': 'Сәтті'},
    'loading': {'en': 'Loading...', 'ru': 'Загрузка...', 'kz': 'Жүктелуде...'},
    'yes': {'en': 'Yes', 'ru': 'Да', 'kz': 'Иә'},
    'no': {'en': 'No', 'ru': 'Нет', 'kz': 'Жоқ'},
    'delete': {'en': 'Delete', 'ru': 'Удалить', 'kz': 'Жою'},
    'save': {'en': 'Save', 'ru': 'Сохранить', 'kz': 'Сақтау'},
    'cancel': {'en': 'Cancel', 'ru': 'Отмена', 'kz': 'Болдырмау'},
    'close': {'en': 'Close', 'ru': 'Закрыть', 'kz': 'Жабу'},
    'confirm': {'en': 'Are you sure?', 'ru': 'Вы уверены?', 'kz': 'Сенімдісіз бе?'},

    'nav_dashboard': {'en': 'Dashboard', 'ru': 'Главная', 'kz': 'Басты бет'},
    'nav_import_cf': {'en': 'Import from Codeforces', 'ru': 'Импорт с Codeforces', 'kz': 'Codeforces-тен импорт'},
    'nav_import_json': {'en': 'Import JSON', 'ru': 'Импорт JSON', 'kz': 'JSON импорт'},
    'nav_archive': {'en': 'Archive', 'ru': 'Архив', 'kz': 'Мұрағат'},
    'nav_presentation': {'en': 'Presentation', 'ru': 'Презентация', 'kz': 'Презентация'},
    'login_title': {'en': 'Login', 'ru': 'Вход для организатора', 'kz': 'Ұйымдастырушы кірісі'},
    'login_password': {'en': 'Password', 'ru': 'Пароль', 'kz': 'Құпиясөз'},
}


def get_translation(key, lang=None):
    if lang is None:
        lang = session.get('lang', DEFAULT_LANGUAGE)
    entry = T.get(key)
    if not entry:
        return key
    return entry.get(lang) or entry.get('en') or key


@app.context_processor
def inject_i18n():
    lang = session.get('lang', DEFAULT_LANGUAGE)
    def t(key):
        return get_translation(key, lang)
    return dict(t=t, current_lang=lang, LANGUAGES=LANGUAGES)


@app.route('/set_language/<lang>')
def set_language(lang):
    if lang in LANGUAGES:
        session['lang'] = lang
    return redirect(request.referrer or url_for('dashboard'))


# ──────────────────────────────────────────────────────────────────────────────
# Jinja Filters
# ──────────────────────────────────────────────────────────────────────────────

def from_json_filter(value):
    try:
        return json.loads(value) if value else {}
    except (json.JSONDecodeError, TypeError):
        return {}

app.jinja_env.filters['from_json_filter'] = from_json_filter

from datetime import datetime

def timestamp_to_date(value):
    try:
        return datetime.fromtimestamp(int(value)).strftime('%d.%m.%Y %H:%M')
    except (ValueError, TypeError, OSError):
        return '—'

app.jinja_env.filters['timestamp_to_date'] = timestamp_to_date


# ──────────────────────────────────────────────────────────────────────────────
# Auth
# ──────────────────────────────────────────────────────────────────────────────

_login_attempts = {}
_LOGIN_WINDOW = 60
_LOGIN_MAX_ATTEMPTS = 5


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('is_admin'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        password = request.form.get('password', '')
        ip = request.remote_addr
        now = time.time()
        attempts = _login_attempts.get(ip, [])
        attempts = [t for t in attempts if now - t < _LOGIN_WINDOW]
        if len(attempts) >= _LOGIN_MAX_ATTEMPTS:
            flash('Слишком много попыток. Подождите минуту.', 'danger')
            return render_template('login.html')
        if check_password_hash(ADMIN_PASSWORD, password):
            session['is_admin'] = True
            session.permanent = True
            _login_attempts.pop(ip, None)
            return redirect(url_for('dashboard'))
        else:
            attempts.append(now)
            _login_attempts[ip] = attempts
            flash('Неверный пароль', 'danger')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.errorhandler(CSRFError)
def handle_csrf_error(e):
    if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'error': 'Сессия истекла. Обновите страницу.'}), 400
    flash('Сессия истекла. Попробуйте ещё раз.', 'warning')
    return redirect(request.referrer or url_for('dashboard')), 400


@app.after_request
def add_header(response):
    if request.path.startswith('/static'):
        response.headers['Cache-Control'] = 'public, max-age=3600'
    else:
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    return response


# ──────────────────────────────────────────────────────────────────────────────
# Dashboard
# ──────────────────────────────────────────────────────────────────────────────

@app.route('/')
@admin_required
def dashboard():
    contests = db.get_all_contests()
    return render_template('dashboard.html', contests=contests)


# ──────────────────────────────────────────────────────────────────────────────
# Codeforces Import
# ──────────────────────────────────────────────────────────────────────────────

@app.route('/import/codeforces')
@admin_required
def codeforces_import_page():
    return render_template('codeforces_import.html')


@app.route('/import/codeforces/fetch', methods=['POST'])
@admin_required
def codeforces_fetch():
    data = request.get_json(silent=True) or {}
    contest_id = data.get('contest_id')
    freeze_minutes = data.get('freeze_minutes', 60)
    api_key = data.get('api_key') or None
    api_secret = data.get('api_secret') or None

    raw_standings_input = data.get('raw_standings')

    if not contest_id:
        return jsonify({'error': 'Не указан Contest ID'}), 400
    try:
        contest_id = int(contest_id)
    except (ValueError, TypeError):
        return jsonify({'error': 'Contest ID должен быть числом'}), 400
    try:
        freeze_minutes = int(freeze_minutes)
        if freeze_minutes < 0:
            freeze_minutes = 0
    except (ValueError, TypeError):
        freeze_minutes = 60

    try:
        if raw_standings_input:
            raw_standings = raw_standings_input
            preview = {
                'name': raw_standings.get('contest', {}).get('name', f'CF Contest #{contest_id}'),
                'problems_count': len(raw_standings.get('problems', [])),
                'problems': [{'index': p.get('index', '?'), 'name': p.get('name', 'Problem')} for p in raw_standings.get('problems', [])],
                'duration_display': f"{(raw_standings.get('contest', {}).get('durationSeconds', 0) or 0) // 60 // 60}ч"
            }
        else:
            preview = get_contest_preview(contest_id, api_key, api_secret)
            raw_standings = fetch_contest_standings(contest_id, api_key, api_secret, show_unofficial=True)
            
        raw_rows = raw_standings.get('rows', [])
        contest_type = raw_standings.get('contest', {}).get('type', 'ICPC')

        oid = 'CF' + str(contest_id)[-6:].zfill(6)
        if len(oid) > 8:
            oid = oid[:8]
        elif len(oid) < 8:
            oid = oid.ljust(8, '0')

        type_counts = {}
        for row in raw_rows:
            is_ghost = row.get('party', {}).get('ghost', False)
            pt = 'VIRTUAL' if is_ghost else _get_participant_type(row)
            type_counts[pt] = type_counts.get(pt, 0) + 1

        all_participants = []
        for row in raw_rows:
            p_name = _get_participant_name(row)
            if p_name == 'Unknown':
                continue
            pr = row.get('problemResults', [])
            total_score = 0
            total_penalty = 0
            for prob_res in pr:
                pts = prob_res.get('points', 0)
                if pts > 0:
                    if contest_type == 'ICPC':
                        total_score += 1
                        bt = prob_res.get('bestSubmissionTimeSeconds', 0)
                        rej = prob_res.get('rejectedAttemptCount', 0)
                        total_penalty += (bt // 60) + (rej * 20)
                    else:
                        total_score += int(pts)
            is_ghost = row.get('party', {}).get('ghost', False)
            all_participants.append({
                'nickname': p_name,
                'handle': _get_participant_handle(row),
                'organization': _get_participant_org(row),
                'total_score': total_score,
                'total_penalty': total_penalty,
                'participant_type': 'VIRTUAL' if is_ghost else _get_participant_type(row),
            })

        scoring_type = 'icpc' if contest_type == 'ICPC' else 'points'

        return jsonify({
            'contest_id': contest_id,
            'contest_name': preview.get('name', f'CF Contest #{contest_id}'),
            'olympiad_id': oid,
            'scoring_type': scoring_type,
            'participants_count': len(all_participants),
            'problems_count': preview.get('problems_count', 0),
            'problems': preview.get('problems', []),
            'duration_display': preview.get('duration_display', '—'),
            'freeze_minutes': freeze_minutes,
            'preview_participants': all_participants[:10],
            'all_participants': all_participants,
            'type_counts': type_counts,
            'api_key': api_key,
            'api_secret': api_secret,
        })
    except RuntimeError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': f'Ошибка при загрузке данных: {str(e)}'}), 500


@app.route('/import/codeforces/do', methods=['POST'])
@admin_required
def codeforces_do_import():
    data = request.get_json(silent=True) or {}
    contest_id = data.get('contest_id')
    freeze_minutes = data.get('freeze_minutes', 60)
    api_key = data.get('api_key') or None
    api_secret = data.get('api_secret') or None
    custom_name = data.get('name', '').strip()
    handles_filter = data.get('handles_filter') or None
    participant_types = data.get('participant_types') or None
    top_n = data.get('top_n') or None

    raw_standings = data.get('raw_standings')
    raw_submissions = data.get('raw_submissions')

    if not contest_id:
        return jsonify({'error': 'Не указан Contest ID'}), 400
    try:
        contest_id = int(contest_id)
    except (ValueError, TypeError):
        return jsonify({'error': 'Contest ID должен быть числом'}), 400
    try:
        freeze_minutes = int(freeze_minutes)
        if freeze_minutes < 0:
            freeze_minutes = 0
    except (ValueError, TypeError):
        freeze_minutes = 60
    if top_n:
        try:
            top_n = int(top_n)
        except (ValueError, TypeError):
            top_n = None

    try:
        if raw_standings:
            from codeforces_import import transform_cf_to_synaqmaker
            result = transform_cf_to_synaqmaker(
                raw_standings, raw_submissions or [], freeze_minutes, contest_id,
                participant_types=participant_types, top_n=top_n
            )
        else:
            result = get_full_contest_data(
                contest_id, freeze_minutes, api_key, api_secret,
                participant_types=participant_types, top_n=top_n)

        olympiad_id = result['olympiad_id']
        name = custom_name or result['contest_name']
        scoring_type = result['scoring_type']
        tasks = result['tasks']
        frozen_scoreboard = result['frozen_scoreboard']
        final_scoreboard = result['final_scoreboard']
        first_solves = result.get('first_solves', {})

        # Filter by handles if provided
        if handles_filter and isinstance(handles_filter, list):
            frozen_scoreboard = filter_scoreboard_by_handles(frozen_scoreboard, handles_filter)
            final_scoreboard = filter_scoreboard_by_handles(final_scoreboard, handles_filter)
            kept_ids = {p['participant_id'] for p in final_scoreboard}
            first_solves = {k: v for k, v in first_solves.items() if v in kept_ids}

        # Normalize ICPC scores
        if scoring_type == 'icpc':
            for board in [frozen_scoreboard, final_scoreboard]:
                for p in board:
                    solved = sum(1 for s in p.get('scores', {}).values() if s.get('passed'))
                    penalty = sum(s.get('penalty', 0) for s in p.get('scores', {}).values() if s.get('passed'))
                    p['total_score'] = solved
                    p['solved_count'] = solved
                    p['total_penalty'] = penalty

        # Save to DB
        db.save_contest(olympiad_id, name, scoring_type, tasks, first_solves)
        db.save_frozen_scoreboard(olympiad_id, frozen_scoreboard, final_scoreboard,
                                  result.get('freeze_time', time.time()))
        db.save_participants(olympiad_id, final_scoreboard)
        db.save_first_solves(olympiad_id, first_solves)

        return jsonify({
            'ok': True,
            'olympiad_id': olympiad_id,
            'name': name,
            'participants_count': len(final_scoreboard),
            'problems_count': len(tasks),
        })
    except RuntimeError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Ошибка при импорте: {str(e)}'}), 500


# ──────────────────────────────────────────────────────────────────────────────
# JSON Import
# ──────────────────────────────────────────────────────────────────────────────

@app.route('/import/json')
@admin_required
def json_import_page():
    return render_template('json_import.html')


@app.route('/import/json/upload', methods=['POST'])
@admin_required
def json_import_upload():
    if 'json_file' not in request.files:
        return jsonify({'error': 'Файл не предоставлен'}), 400
    file = request.files['json_file']
    if file.filename == '':
        return jsonify({'error': 'Файл не выбран'}), 400
    if not file.filename.endswith('.json'):
        return jsonify({'error': 'Файл должен быть .json'}), 400

    try:
        json_data = json.loads(file.read().decode('utf-8'))
        required = ['olympiad_id', 'frozen_scoreboard', 'final_scoreboard', 'freeze_time']
        for field in required:
            if field not in json_data:
                return jsonify({'error': f'Отсутствует обязательное поле: {field}'}), 400

        olympiad_id = json_data['olympiad_id']
        frozen = json_data['frozen_scoreboard']
        final = json_data['final_scoreboard']
        freeze_time = json_data['freeze_time']
        tasks = json_data.get('tasks', [])
        name = json_data.get('name', json_data.get('contest_name', f'Contest {olympiad_id}'))
        scoring_type = json_data.get('scoring_type', 'icpc')
        first_solves = json_data.get('first_solves', {})

        if not isinstance(olympiad_id, str) or len(olympiad_id) < 1:
            return jsonify({'error': 'Неверный olympiad_id'}), 400
        if not isinstance(frozen, list) or not isinstance(final, list):
            return jsonify({'error': 'Скорборды должны быть массивами'}), 400

        # Auto-generate tasks from score keys if empty
        if not tasks and final:
            keys = sorted(final[0].get('scores', {}).keys())
            letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            tasks = [{'id': k, 'title': f"{letters[i] if i < 26 else str(i+1)}. Task"} for i, k in enumerate(keys)]

        # Нормализация данных: обеспечиваем наличие last_attempt_time для презентации
        for board in [frozen, final]:
            for p in board:
                scores = p.get('scores', {})
                for tid, s in scores.items():
                    if isinstance(s, dict):
                        if 'attempts' not in s:
                            s['attempts'] = s.get('wrong_attempts', 0)
                        
                        if 'last_attempt_time' not in s:
                            if 'time' in s:
                                s['last_attempt_time'] = s['time']
                            elif 'solve_time' in s:
                                s['last_attempt_time'] = s['solve_time']
                            elif 'best_time' in s:
                                s['last_attempt_time'] = s['best_time']
                            else:
                                # Если времени нет, пробуем вычислить из штрафа (ICPC)
                                if scoring_type == 'icpc' and s.get('passed'):
                                    penalty = s.get('penalty', 0)
                                    attempts = s.get('attempts', 0)
                                    calc_time = penalty - (attempts * 20)
                                    s['last_attempt_time'] = calc_time if calc_time >= 0 else 0
                                else:
                                    s['last_attempt_time'] = 0

        db.save_contest(olympiad_id, name, scoring_type, tasks, first_solves)
        db.save_frozen_scoreboard(olympiad_id, frozen, final, freeze_time)
        db.save_participants(olympiad_id, final)
        if first_solves:
            db.save_first_solves(olympiad_id, first_solves)

        return jsonify({
            'ok': True,
            'olympiad_id': olympiad_id,
            'name': name,
            'participants_count': len(final),
            'problems_count': len(tasks),
        })
    except json.JSONDecodeError as e:
        return jsonify({'error': f'Ошибка JSON: {str(e)}'}), 400
    except Exception as e:
        return jsonify({'error': f'Ошибка импорта: {str(e)}'}), 500


# ──────────────────────────────────────────────────────────────────────────────
# Archive
# ──────────────────────────────────────────────────────────────────────────────

@app.route('/archive')
@admin_required
def archive():
    contests = db.get_all_contests()
    return render_template('archive.html', contests=contests)


@app.route('/archive/view/<olympiad_id>')
@admin_required
def archive_view(olympiad_id):
    results = db.get_results(olympiad_id)
    if not results:
        flash('Контест не найден.', 'warning')
        return redirect(url_for('archive'))

    all_groups = db.get_groups(olympiad_id) or {}
    group_filter = request.args.get('group', '').strip()

    participants = results['participants']
    if group_filter and group_filter in all_groups:
        allowed = set(all_groups[group_filter])
        participants = [p for p in participants if p.get('participant_id') in allowed]

    return render_template('archive_view.html',
                           contest=results['contest'],
                           participants=participants,
                           tasks=results['tasks'],
                           first_solves=results.get('first_solves', {}),
                           olympiad_id=olympiad_id,
                           all_groups=list(all_groups.keys()),
                           group_filter=group_filter)


@app.route('/archive/delete/<olympiad_id>', methods=['POST'])
@admin_required
def archive_delete(olympiad_id):
    if db.delete_contest(olympiad_id):
        flash(f'Контест {olympiad_id} удалён.', 'success')
    else:
        flash('Ошибка при удалении.', 'danger')
    return redirect(url_for('archive'))


@app.route('/archive/export/<olympiad_id>')
@admin_required
def archive_export_json(olympiad_id):
    export_data = db.export_frozen_json(olympiad_id)
    if not export_data:
        return jsonify({'error': 'Нет данных'}), 404
    contest = db.get_contest(olympiad_id)
    if contest:
        export_data['name'] = contest['name']
        export_data['scoring_type'] = contest['scoring_type']
        export_data['first_solves'] = contest.get('first_solves', {})
    response = app.response_class(
        response=json.dumps(export_data, ensure_ascii=False, indent=2),
        status=200, mimetype='application/json')
    response.headers['Content-Disposition'] = f'attachment; filename=resolver_{olympiad_id}.json'
    return response


@app.route('/archive/export_excel/<olympiad_id>')
@admin_required
def archive_export_excel(olympiad_id):
    """Export results as Excel file."""
    import pandas as pd
    results = db.get_results(olympiad_id)
    if not results:
        flash('Контест не найден.', 'warning')
        return redirect(url_for('archive'))

    group_filter = request.args.get('group', '').strip()
    all_groups = db.get_groups(olympiad_id) or {}
    participants = results['participants']
    if group_filter and group_filter in all_groups:
        allowed = set(all_groups[group_filter])
        participants = [p for p in participants if p.get('participant_id') in allowed]

    tasks = results['tasks']
    scoring_type = results['contest']['scoring_type']

    rows_data = []
    for i, p in enumerate(participants):
        row = {
            '№': i + 1,
            'Участник': p.get('nickname', ''),
            'Организация': p.get('organization', ''),
        }
        for task in tasks:
            tid = task['id'] if isinstance(task, dict) else task
            title = task.get('title', tid) if isinstance(task, dict) else str(tid)
            scores = p.get('scores', {})
            s = scores.get(str(tid), scores.get(tid, {}))
            if isinstance(s, dict):
                if scoring_type == 'icpc':
                    if s.get('passed'):
                        row[title] = f"+{s.get('attempts', 0)}" if s.get('attempts', 0) > 0 else '+'
                    elif s.get('attempts', 0) > 0:
                        row[title] = f"-{s['attempts']}"
                    else:
                        row[title] = ''
                else:
                    row[title] = s.get('score', 0) if s.get('score', 0) > 0 else ''
            else:
                row[title] = s if s else ''

        if scoring_type == 'icpc':
            row['Решено'] = p.get('solved_count', 0)
            row['Штраф'] = p.get('total_penalty', 0)
        else:
            row['Баллы'] = p.get('total_score', 0)
            row['Штраф'] = p.get('total_penalty', 0)

        rows_data.append(row)

    df = pd.DataFrame(rows_data)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Results')
    buf.seek(0)
    name = results['contest'].get('name', olympiad_id)
    safe_name = re.sub(r'[^\w\s-]', '', name)[:50]
    filename = f"results_{safe_name}_{olympiad_id}.xlsx"
    return send_file(buf, download_name=filename, as_attachment=True,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


# ──────────────────────────────────────────────────────────────────────────────
# Presentation (Resolver)
# ──────────────────────────────────────────────────────────────────────────────

@app.route('/presentation/<olympiad_id>')
@admin_required
def presentation(olympiad_id):
    frozen_data = db.get_frozen_data(olympiad_id)
    if not frozen_data:
        flash('Нет данных для этого контеста.', 'warning')
        return redirect(url_for('archive'))

    contest = db.get_contest(olympiad_id)
    tasks = contest['tasks'] if contest else []
    scoring_type = contest['scoring_type'] if contest else 'icpc'
    oly_name = contest['name'] if contest else olympiad_id

    # Build tasks as [(id, title)] for template
    tasks_tuples = []
    if tasks:
        for t in tasks:
            if isinstance(t, dict):
                tasks_tuples.append((t['id'], t.get('title', t['id'])))
            elif isinstance(t, (list, tuple)) and len(t) >= 2:
                tasks_tuples.append((t[0], t[1]))
    else:
        # Auto-generate from frozen data
        all_boards = frozen_data.get('final_scoreboard') or frozen_data.get('frozen_scoreboard') or []
        if all_boards:
            keys = sorted(list(all_boards[0].get('scores', {}).keys()))
            for k in keys:
                letter = k.rsplit('_', 1)[-1] if k.startswith('cf_') else k
                tasks_tuples.append((k, letter))

    _INVIS_RE = re.compile(r'^[\s\u200b-\u200f\u2028\u2029\u202a-\u202e\u2060\ufeff\u00ad]*$')
    frozen_clean = [p for p in frozen_data['frozen_scoreboard']
                    if p.get('nickname') and not _INVIS_RE.match(p['nickname'])
                    and not p.get('disqualified')]
    final_clean = [p for p in frozen_data['final_scoreboard']
                   if p.get('nickname') and not _INVIS_RE.match(p['nickname'])
                   and not p.get('disqualified')]

    # Group filter
    group_filter = request.args.get('group', '').strip()
    all_groups = db.get_groups(olympiad_id) or {}
    if group_filter and group_filter in all_groups:
        allowed = set(all_groups[group_filter])
        frozen_clean = [p for p in frozen_clean if p.get('participant_id') in allowed]
        final_clean = [p for p in final_clean if p.get('participant_id') in allowed]

    first_solves = db.get_first_solves(olympiad_id)
    ceremony_settings = db.get_ceremony_settings(olympiad_id)

    config_obj = {'config': {'olympiad_id': olympiad_id, 'scoring': scoring_type}}

    return render_template('presentation.html',
                           olympiad_id=olympiad_id,
                           olympiad_name=oly_name,
                           frozen_scoreboard=frozen_clean,
                           final_scoreboard=final_clean,
                           tasks=tasks_tuples,
                           config=config_obj,
                           is_revealed=frozen_data['is_revealed'],
                           first_solves=first_solves,
                           ceremony_settings=ceremony_settings,
                           group_filter=group_filter,
                           all_groups=list(all_groups.keys()))


# ──────────────────────────────────────────────────────────────────────────────
# API Endpoints
# ──────────────────────────────────────────────────────────────────────────────

@app.route('/api/contest/<olympiad_id>/frozen_data')
@admin_required
def api_frozen_data(olympiad_id):
    frozen = db.get_frozen_data(olympiad_id)
    if not frozen:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(frozen)


@app.route('/api/contest/<olympiad_id>/mark_revealed', methods=['POST'])
@admin_required
def api_mark_revealed(olympiad_id):
    db.mark_revealed(olympiad_id)
    return jsonify({'status': 'ok'})


@app.route('/api/contest/<olympiad_id>/ceremony_settings', methods=['GET'])
@admin_required
def api_get_ceremony_settings(olympiad_id):
    settings = db.get_ceremony_settings(olympiad_id)
    if settings:
        return jsonify({'ok': True, 'settings': settings})
    return jsonify({'ok': False, 'settings': None})


@app.route('/api/contest/<olympiad_id>/ceremony_settings', methods=['POST'])
@admin_required
def api_save_ceremony_settings(olympiad_id):
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data'}), 400
    gold = max(0, int(data.get('gold', 0)))
    silver = max(0, int(data.get('silver', 0)))
    bronze = max(0, int(data.get('bronze', 0)))
    nominations = data.get('nominations', [])
    settings = {'gold': gold, 'silver': silver, 'bronze': bronze, 'nominations': nominations}
    db.save_ceremony_settings(olympiad_id, settings)
    return jsonify({'ok': True, 'message': 'Настройки сохранены'})


@app.route('/api/contest/<olympiad_id>/groups', methods=['GET'])
@admin_required
def api_get_groups(olympiad_id):
    groups = db.get_groups(olympiad_id)
    return jsonify({'ok': True, 'groups': groups or {}})


@app.route('/api/contest/<olympiad_id>/groups', methods=['POST'])
@admin_required
def api_save_groups(olympiad_id):
    data = request.get_json(silent=True) or {}
    groups = data.get('groups', {})
    db.save_groups(olympiad_id, groups)
    return jsonify({'ok': True})


@app.route('/api/contest/<olympiad_id>/participants_for_grouping')
@admin_required
def api_participants_for_grouping(olympiad_id):
    participants = db.get_participants_for_grouping(olympiad_id)
    groups = db.get_groups(olympiad_id) or {}
    return jsonify({'ok': True, 'participants': participants, 'groups': groups})


@app.route('/api/contest/<olympiad_id>/auto_groups', methods=['POST'])
@admin_required
def api_auto_groups(olympiad_id):
    groups = db.auto_group_by_org(olympiad_id)
    return jsonify({'ok': True, 'groups': groups})


@app.route('/api/contest/<olympiad_id>/winners')
@admin_required
def api_winners(olympiad_id):
    frozen = db.get_frozen_data(olympiad_id)
    if not frozen or not frozen['final_scoreboard']:
        return jsonify({'error': 'Not found'}), 404
    scoreboard = frozen['final_scoreboard']
    winners = {1: [], 2: [], 3: []}
    for i, p in enumerate(scoreboard[:6]):
        if i < 1:
            place = 1
        elif i < 3:
            place = 2
        else:
            place = 3
        winners[place].append({
            'nickname': p.get('nickname'),
            'organization': p.get('organization'),
            'total_score': p.get('total_score', 0),
            'total_penalty': p.get('total_penalty', 0),
        })
    return jsonify({'winners': winners})


# ──────────────────────────────────────────────────────────────────────────────
# Export / Download frozen data
# ──────────────────────────────────────────────────────────────────────────────

@app.route('/api/contest/<olympiad_id>/export_json')
@admin_required
def api_export_json(olympiad_id):
    return redirect(url_for('archive_export_json', olympiad_id=olympiad_id))


# ──────────────────────────────────────────────────────────────────────────────
# Groups Management Page
# ──────────────────────────────────────────────────────────────────────────────

@app.route('/groups/<olympiad_id>')
@admin_required
def groups_page(olympiad_id):
    contest = db.get_contest(olympiad_id)
    if not contest:
        flash('Контест не найден.', 'warning')
        return redirect(url_for('archive'))
    return render_template('groups.html', olympiad_id=olympiad_id, contest=contest)


# ──────────────────────────────────────────────────────────────────────────────
# Error handlers
# ──────────────────────────────────────────────────────────────────────────────

@app.errorhandler(404)
def not_found(e):
    return render_template('error.html', message='Страница не найдена'), 404


@app.errorhandler(500)
def server_error(e):
    return render_template('error.html', message='Внутренняя ошибка сервера'), 500
