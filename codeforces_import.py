"""
codeforces_import.py — Импорт результатов контеста с Codeforces через API.

Позволяет загрузить результаты контеста Codeforces, реконструировать
замороженную и финальную таблицы результатов, и использовать их для
ICPC-style разморозки (presentation mode) в Synaqmaker Resolver.

Codeforces API:
  * contest.standings — итоговая таблица (задачи, участники, баллы)
  * contest.status   — все посылки (для реконструкции замороженной таблицы)
  * Аутентификация (опционально): apiKey + apiSecret + HMAC-SHA512

Формат вывода совместим с OlympiadFrozenData:
  frozen_scoreboard_json, final_scoreboard_json — массивы участников
"""

import copy
import hashlib
import hmac
import json
import random
import re
import string
import time
import uuid

try:
    import requests
except ImportError:
    requests = None


# ──────────────────────────────────────────────────────────────────────────────
# Codeforces API Client
# ──────────────────────────────────────────────────────────────────────────────
CF_API_BASE = "https://codeforces.com/api"

_CF_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}


def _check_requests():
    if requests is None:
        raise RuntimeError(
            "Библиотека 'requests' не установлена. "
            "Установите: pip install requests"
        )


def _make_signed_url(method, params, api_key=None, api_secret=None):
    """Формирует URL для Codeforces API с опциональной HMAC-SHA512 подписью."""
    url = f"{CF_API_BASE}/{method}"
    if not api_key or not api_secret:
        return url, params

    rand_str = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
    params = dict(params)
    params['apiKey'] = api_key
    params['time'] = str(int(time.time()))

    sorted_params = sorted(params.items())
    param_str = '&'.join(f"{k}={v}" for k, v in sorted_params)
    hash_str = f"{rand_str}/{method}?{param_str}#{api_secret}"
    api_sig = rand_str + hashlib.sha512(hash_str.encode('utf-8')).hexdigest()

    params['apiSig'] = api_sig
    return url, params


def _cf_request(method, params, api_key=None, api_secret=None, timeout=60):
    """Выполняет запрос к Codeforces API с обработкой ошибок и авто-ретраем."""
    _check_requests()

    max_retries = 5
    retry_delays = [3, 6, 12, 20, 30]

    session = requests.Session()
    session.headers.update(_CF_HEADERS)

    last_error = None
    for attempt in range(max_retries):
        url, signed_params = _make_signed_url(method, params, api_key, api_secret)

        try:
            resp = session.get(url, params=signed_params, timeout=timeout)
        except requests.exceptions.Timeout:
            last_error = RuntimeError("Таймаут при запросе к Codeforces API (60 сек)")
            if attempt < max_retries - 1:
                time.sleep(retry_delays[attempt])
                continue
            raise last_error
        except requests.exceptions.ConnectionError:
            last_error = RuntimeError("Нет соединения с Codeforces API. Проверьте интернет-подключение.")
            if attempt < max_retries - 1:
                time.sleep(retry_delays[attempt])
                continue
            raise last_error
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Ошибка запроса к Codeforces API: {e}")

        if resp.status_code == 429:
            last_error = RuntimeError(
                "Превышен лимит запросов Codeforces API (rate limit). "
                "Попробуйте через минуту или используйте API-ключи."
            )
            if attempt < max_retries - 1:
                time.sleep(retry_delays[attempt] * 3)
                continue
            raise last_error

        if resp.status_code == 503:
            last_error = RuntimeError(
                "Codeforces API временно недоступен (HTTP 503). "
                "Возможно, сервер перегружен или активна защита Cloudflare. "
                f"Попытка {attempt + 1}/{max_retries}..."
            )
            if attempt < max_retries - 1:
                time.sleep(retry_delays[attempt])
                continue
            raise RuntimeError(
                "Codeforces API временно недоступен (HTTP 503). "
                "Сервер перегружен или активна защита Cloudflare. "
                "Попробуйте повторить через 1-2 минуты."
            )

        if resp.status_code != 200:
            raise RuntimeError(
                f"Codeforces API вернул HTTP {resp.status_code}: {resp.text[:500]}"
            )

        data = resp.json()
        if data.get('status') != 'OK':
            comment = data.get('comment', 'Неизвестная ошибка')
            raise RuntimeError(f"Codeforces API ошибка: {comment}")

        return data['result']

    raise last_error or RuntimeError("Не удалось выполнить запрос к Codeforces API")


# ──────────────────────────────────────────────────────────────────────────────
# Публичные функции для получения данных
# ──────────────────────────────────────────────────────────────────────────────

def fetch_contest_standings(contest_id, api_key=None, api_secret=None, show_unofficial=True):
    params = {'contestId': str(contest_id), 'showUnofficial': 'true' if show_unofficial else 'false'}
    return _cf_request('contest.standings', params, api_key, api_secret)


def fetch_contest_status(contest_id, api_key=None, api_secret=None):
    params = {'contestId': str(contest_id)}
    return _cf_request('contest.status', params, api_key, api_secret, timeout=120)


def fetch_contest_info(contest_id, api_key=None, api_secret=None):
    params = {'contestId': str(contest_id), 'from': '1', 'count': '1',
              'showUnofficial': 'true'}
    return _cf_request('contest.standings', params, api_key, api_secret)


# ──────────────────────────────────────────────────────────────────────────────
# Вспомогательные функции для участников
# ──────────────────────────────────────────────────────────────────────────────

_INVISIBLE_RE = re.compile(
    r'^[\s\u200b-\u200f\u2028\u2029\u202a-\u202e\u2060\ufeff\u00ad]*$'
)


def _strip_invisible(s):
    if not s:
        return ''
    s = s.strip()
    s = re.sub(r'^[\u200b-\u200f\u2028\u2029\u202a-\u202e\u2060\ufeff\u00ad]+', '', s)
    s = re.sub(r'[\u200b-\u200f\u2028\u2029\u202a-\u202e\u2060\ufeff\u00ad]+$', '', s)
    return s


def _get_participant_name(row):
    party = row.get('party', {})
    team_name = _strip_invisible(party.get('teamName') or '')
    if team_name and not _INVISIBLE_RE.match(team_name):
        return team_name
    members = party.get('members', [])
    if not members:
        return 'Unknown'
    member = members[0]
    handle = _strip_invisible(member.get('handle') or '')
    name = _strip_invisible(member.get('name') or '')
    suffix = ''
    if len(members) > 1:
        suffix = f' (+{len(members) - 1})'
    if name and not _INVISIBLE_RE.match(name) and name.lower() != handle.lower():
        result = f"{name}{suffix}"
    elif handle and not _INVISIBLE_RE.match(handle):
        result = f"{handle}{suffix}"
    else:
        return 'Unknown'
    return result if result.strip() else 'Unknown'


def _get_participant_org(row):
    party = row.get('party', {})
    members = party.get('members', [])
    if members:
        return members[0].get('organization', '') or ''
    return ''


def _get_participant_handle(row):
    party = row.get('party', {})
    team_name = (party.get('teamName') or '').strip()
    if team_name:
        return team_name
    members = party.get('members', [])
    if members:
        return (members[0].get('handle') or '').strip()
    return ''


def _get_participant_key(row):
    party = row.get('party', {})
    team_name = party.get('teamName')
    if team_name:
        return f"team:{team_name}"
    members = party.get('members', [])
    if members:
        handles = sorted(m.get('handle', '') for m in members)
        return f"members:{','.join(handles)}"
    return f"unknown:{id(row)}"


def _get_participant_key_from_sub(author):
    team_name = author.get('teamName')
    if team_name:
        return f"team:{team_name}"
    members = author.get('members', [])
    if members:
        handles = sorted(m.get('handle', '') for m in members)
        return f"members:{','.join(handles)}"
    return f"unknown_sub:{id(author)}"


def _get_participant_type(row):
    party = row.get('party', {})
    return party.get('participantType', 'CONTESTANT')


def _is_ghost(row):
    return row.get('party', {}).get('ghost', False)


def filter_rows_by_participant_types(rows, participant_types):
    if not participant_types:
        return rows
    allowed = {t.upper() for t in participant_types}
    include_virtual = 'VIRTUAL' in allowed
    result = []
    for r in rows:
        is_ghost_val = _is_ghost(r)
        pt = _get_participant_type(r)
        if is_ghost_val:
            if include_virtual:
                result.append(r)
        else:
            if pt in allowed:
                result.append(r)
    return result


def filter_scoreboard_by_handles(scoreboard, handles):
    if not handles:
        return scoreboard
    handles_lower = {h.strip().lower() for h in handles if h.strip()}
    if not handles_lower:
        return scoreboard

    def _matches(participant):
        h = participant.get('handle', '').lower()
        n = participant.get('nickname', '').lower()
        if h in handles_lower or n in handles_lower:
            return True
        if '=' in h:
            after_eq = h.split('=', 1)[1]
            if after_eq in handles_lower:
                return True
        for fh in handles_lower:
            if '=' in fh and fh.split('=', 1)[1] == h:
                return True
        return False

    return [p for p in scoreboard if _matches(p)]


def filter_scoreboard_exclude_handles(scoreboard, handles):
    if not handles:
        return scoreboard
    handles_lower = {h.strip().lower() for h in handles if h.strip()}
    if not handles_lower:
        return scoreboard

    def _matches(participant):
        h = participant.get('handle', '').lower()
        n = participant.get('nickname', '').lower()
        if h in handles_lower or n in handles_lower:
            return True
        if '=' in h:
            after_eq = h.split('=', 1)[1]
            if after_eq in handles_lower:
                return True
        for fh in handles_lower:
            if '=' in fh and fh.split('=', 1)[1] == h:
                return True
        return False

    return [p for p in scoreboard if not _matches(p)]


# ──────────────────────────────────────────────────────────────────────────────
# Построение скорбордов
# ──────────────────────────────────────────────────────────────────────────────

def _make_score_entry(score=0, attempts=0, passed=False, penalty=0,
                      last_attempt_time=0):
    return {
        'score': score, 'attempts': attempts, 'passed': passed,
        'penalty': penalty, 'first_solved': False, 'is_pending': False,
        'last_attempt_time': last_attempt_time,
    }


def _make_participant(p_uuid, nickname, organization, scores,
                      total_score, total_penalty, solved_count, handle=''):
    return {
        'participant_id': p_uuid, 'nickname': nickname, 'handle': handle,
        'organization': organization, 'total_score': total_score,
        'total_penalty': total_penalty, 'solved_count': solved_count,
        'scores': scores, 'disqualified': False,
    }


def _build_final_from_standings(rows, problems, problem_index_to_id,
                                participant_uuids, contest_type):
    scoreboard = []
    for row in rows:
        p_key = _get_participant_key(row)
        p_uuid = participant_uuids.get(p_key, str(uuid.uuid4()))
        nickname = _get_participant_name(row)
        handle = _get_participant_handle(row)
        organization = _get_participant_org(row)
        problem_results = row.get('problemResults', [])
        scores = {}
        total_penalty = 0
        solved_count = 0
        for prob_idx, prob in enumerate(problems):
            task_id = problem_index_to_id[prob_idx]
            pr = problem_results[prob_idx] if prob_idx < len(problem_results) else {}
            points = pr.get('points', 0)
            rejected = pr.get('rejectedAttemptCount', 0)
            best_time = pr.get('bestSubmissionTimeSeconds', 0)
            if contest_type == 'ICPC':
                passed = points > 0
                score_val = 1 if passed else 0
                attempts = rejected
                penalty = ((best_time // 60) + (rejected * 20)) if passed else 0
            else:
                passed = points > 0
                score_val = int(points)
                attempts = rejected
                penalty = (best_time // 60) if passed and best_time else 0
            if passed:
                solved_count += 1
                total_penalty += penalty
            # For solved tasks: last_attempt_time = solve time (bestSubmissionTimeSeconds)
            # For unsolved tasks: last_attempt_time = 0 (unknown from standings API)
            if passed and best_time:
                last_time = best_time // 60
            else:
                last_time = 0
            scores[task_id] = _make_score_entry(score_val, attempts, passed, penalty, last_time)
        total_score = solved_count if contest_type == 'ICPC' else sum(s['score'] for s in scores.values())
        scoreboard.append(_make_participant(p_uuid, nickname, organization, scores,
                                           total_score, total_penalty, solved_count, handle=handle))
    return scoreboard


def _build_final_from_submissions(rows, problems, problem_index_to_id,
                                  participant_uuids, participant_submissions,
                                  contest_type):
    """Строит финальный скорборд — гибридный подход.

    Количество попыток и баллы берутся из standings (rejectedAttemptCount —
    точное значение от Codeforces). Время последней посылки (last_attempt_time)
    вычисляется из submissions, что позволяет получить точное время для ВСЕХ
    задач, включая нерешённые.
    """
    scoreboard = []
    for row in rows:
        p_key = _get_participant_key(row)
        p_uuid = participant_uuids.get(p_key, str(uuid.uuid4()))
        nickname = _get_participant_name(row)
        handle = _get_participant_handle(row)
        organization = _get_participant_org(row)
        subs = participant_submissions.get(p_key, [])
        problem_results = row.get('problemResults', [])
        scores = {}
        total_penalty = 0
        solved_count = 0
        for prob_idx, prob in enumerate(problems):
            task_id = problem_index_to_id[prob_idx]
            prob_index = prob.get('index', '')
            pr = problem_results[prob_idx] if prob_idx < len(problem_results) else {}
            points = pr.get('points', 0)
            rejected = pr.get('rejectedAttemptCount', 0)
            best_time = pr.get('bestSubmissionTimeSeconds', 0)

            # Основные данные — из standings (точные)
            if contest_type == 'ICPC':
                passed = points > 0
                score_val = 1 if passed else 0
                attempts = rejected
                penalty = ((best_time // 60) + (rejected * 20)) if passed else 0
            else:
                passed = points > 0
                score_val = int(points)
                attempts = rejected
                penalty = (best_time // 60) if passed and best_time else 0

            if passed:
                solved_count += 1
                total_penalty += penalty

            # Время — из submissions (точное для всех задач)
            last_time = 0
            task_subs = [s for s in subs if s.get('problem', {}).get('index') == prob_index]
            if task_subs:
                last_time = _get_last_attempt_time(task_subs, contest_type)
            if last_time == 0 and passed and best_time:
                last_time = best_time // 60  # фолбэк на standings

            scores[task_id] = _make_score_entry(score_val, attempts, passed, penalty, last_time)
        total_score = solved_count if contest_type == 'ICPC' else sum(s['score'] for s in scores.values())
        scoreboard.append(_make_participant(p_uuid, nickname, organization, scores,
                                           total_score, total_penalty, solved_count, handle=handle))
    return scoreboard


def _get_last_attempt_time(submissions, contest_type='ICPC'):
    """Вычисляет время последней значимой посылки из списка submissions."""
    last_time = 0
    if contest_type == 'ICPC':
        for sub in submissions:
            verdict = sub.get('verdict', '')
            rel_time = sub.get('relativeTimeSeconds', 0)
            if verdict in ('COMPILATION_ERROR', 'SKIPPED', 'TESTING'):
                continue
            last_time = rel_time // 60 if rel_time else 0
            if verdict == 'OK':
                break
    else:
        for sub in submissions:
            verdict = sub.get('verdict', '')
            rel_time = sub.get('relativeTimeSeconds', 0)
            if verdict in ('COMPILATION_ERROR', 'SKIPPED', 'TESTING'):
                continue
            last_time = rel_time // 60 if rel_time else 0
    return last_time


def _build_frozen_from_submissions(rows, problems, problem_index_to_id,
                                   participant_uuids, participant_submissions,
                                   freeze_time_sec, contest_type):
    scoreboard = []
    for row in rows:
        p_key = _get_participant_key(row)
        p_uuid = participant_uuids.get(p_key, str(uuid.uuid4()))
        nickname = _get_participant_name(row)
        handle = _get_participant_handle(row)
        organization = _get_participant_org(row)
        subs = participant_submissions.get(p_key, [])
        problem_results = row.get('problemResults', [])
        scores = {}
        total_penalty = 0
        solved_count = 0
        for prob_idx, prob in enumerate(problems):
            task_id = problem_index_to_id[prob_idx]
            prob_index = prob.get('index', '')
            task_subs = [s for s in subs if s.get('problem', {}).get('index') == prob_index]
            if task_subs:
                subs_before = [s for s in task_subs if s.get('relativeTimeSeconds', 0) <= freeze_time_sec]
                score_info = _compute_task_from_subs(subs_before, contest_type)
            else:
                pr = problem_results[prob_idx] if prob_idx < len(problem_results) else {}
                score_info = _compute_task_from_standings(pr, freeze_time_sec, contest_type)
            if score_info['passed']:
                solved_count += 1
                total_penalty += score_info['penalty']
            scores[task_id] = score_info
        total_score = solved_count if contest_type == 'ICPC' else sum(s['score'] for s in scores.values())
        scoreboard.append(_make_participant(p_uuid, nickname, organization, scores,
                                           total_score, total_penalty, solved_count, handle=handle))
    return scoreboard


def _build_frozen_from_standings(rows, problems, problem_index_to_id,
                                 participant_uuids, freeze_time_sec, contest_type):
    scoreboard = []
    for row in rows:
        p_key = _get_participant_key(row)
        p_uuid = participant_uuids.get(p_key, str(uuid.uuid4()))
        nickname = _get_participant_name(row)
        handle = _get_participant_handle(row)
        organization = _get_participant_org(row)
        problem_results = row.get('problemResults', [])
        scores = {}
        total_penalty = 0
        solved_count = 0
        for prob_idx, prob in enumerate(problems):
            task_id = problem_index_to_id[prob_idx]
            pr = problem_results[prob_idx] if prob_idx < len(problem_results) else {}
            points = pr.get('points', 0)
            rejected = pr.get('rejectedAttemptCount', 0)
            best_time = pr.get('bestSubmissionTimeSeconds', 0)
            passed_overall = points > 0
            solved_before_freeze = passed_overall and best_time <= freeze_time_sec
            if solved_before_freeze:
                if contest_type == 'ICPC':
                    score_val = 1
                    attempts = rejected
                    penalty = (best_time // 60) + (rejected * 20)
                else:
                    score_val = int(points)
                    attempts = rejected
                    penalty = (best_time // 60) if best_time else 0
                solved_count += 1
                total_penalty += penalty
                last_time = (best_time // 60) if best_time else 0
                scores[task_id] = _make_score_entry(score_val, attempts, True, penalty, last_time)
            else:
                # For unsolved tasks in frozen view from standings API:
                # - attempts = total rejected (from standings, may include post-freeze)
                # - last_attempt_time = 0 (unknown — bestSubmissionTimeSeconds is AC time,
                #   not WA time; we can't know from standings alone)
                # The frontend will show '?' when last_attempt_time is 0
                frozen_attempts = rejected
                scores[task_id] = _make_score_entry(0, frozen_attempts, False, 0, 0)
        total_score = solved_count if contest_type == 'ICPC' else sum(s['score'] for s in scores.values())
        scoreboard.append(_make_participant(p_uuid, nickname, organization, scores,
                                           total_score, total_penalty, solved_count, handle=handle))
    return scoreboard


def _compute_task_from_subs(submissions, contest_type='ICPC'):
    result = _make_score_entry()
    wrong_attempts = 0
    if contest_type == 'ICPC':
        for sub in submissions:
            verdict = sub.get('verdict', '')
            rel_time = sub.get('relativeTimeSeconds', 0)
            result['last_attempt_time'] = rel_time // 60 if rel_time else 0
            if verdict in ('COMPILATION_ERROR', 'SKIPPED', 'TESTING'):
                continue
            if verdict == 'OK':
                result['score'] = 1
                result['passed'] = True
                result['penalty'] = (rel_time // 60) + (wrong_attempts * 20)
                result['last_attempt_time'] = rel_time // 60
                break
            else:
                result['attempts'] += 1
                wrong_attempts += 1
    else:
        best_score = 0
        best_time = 0
        for sub in submissions:
            verdict = sub.get('verdict', '')
            rel_time = sub.get('relativeTimeSeconds', 0)
            points = sub.get('points', 0)
            result['last_attempt_time'] = rel_time // 60 if rel_time else 0
            if verdict in ('COMPILATION_ERROR', 'SKIPPED', 'TESTING'):
                continue
            if points > best_score:
                best_score = points
                best_time = rel_time
            if verdict != 'OK':
                result['attempts'] += 1
                wrong_attempts += 1
        if best_score > 0:
            result['score'] = int(best_score)
            result['passed'] = True
            result['penalty'] = (best_time // 60) if best_time else 0
            result['last_attempt_time'] = best_time // 60 if best_time else 0
    return result


def _compute_task_from_standings(pr, cutoff_time_sec, contest_type):
    result = _make_score_entry()
    points = pr.get('points', 0)
    rejected = pr.get('rejectedAttemptCount', 0)
    best_time = pr.get('bestSubmissionTimeSeconds', 0)
    passed_overall = points > 0
    passed_before_cutoff = passed_overall and best_time <= cutoff_time_sec
    if passed_before_cutoff:
        if contest_type == 'ICPC':
            result['score'] = 1
            result['passed'] = True
            result['attempts'] = rejected
            result['penalty'] = (best_time // 60) + (rejected * 20)
        else:
            result['score'] = int(points)
            result['passed'] = True
            result['attempts'] = rejected
            result['penalty'] = (best_time // 60) if best_time else 0
        result['last_attempt_time'] = best_time // 60 if best_time else 0
    else:
        result['attempts'] = rejected
        result['last_attempt_time'] = best_time // 60 if best_time else 0
    return result


def _compute_first_solves(rows, problems, problem_index_to_id, participant_uuids):
    first_solves = {}
    for prob_idx, prob in enumerate(problems):
        task_id = problem_index_to_id[prob_idx]
        best_time = float('inf')
        best_uuid = None
        for row in rows:
            pr_list = row.get('problemResults', [])
            if prob_idx >= len(pr_list):
                continue
            pr = pr_list[prob_idx]
            if pr.get('points', 0) > 0:
                solve_time = pr.get('bestSubmissionTimeSeconds', 0)
                if solve_time >= 0 and solve_time < best_time:
                    best_time = solve_time
                    p_key = _get_participant_key(row)
                    best_uuid = participant_uuids.get(p_key)
        if best_uuid:
            first_solves[task_id] = best_uuid
    return first_solves


# ──────────────────────────────────────────────────────────────────────────────
# Трансформер: Codeforces -> Synaqmaker
# ──────────────────────────────────────────────────────────────────────────────

def transform_cf_to_synaqmaker(standings, submissions, freeze_minutes, contest_id,
                               participant_types=None, top_n=None):
    contest = standings['contest']
    problems = standings['problems']
    rows = standings['rows']

    if not participant_types:
        participant_types = ['CONTESTANT']
    rows = filter_rows_by_participant_types(rows, participant_types)

    if top_n and top_n > 0 and len(rows) > top_n:
        rows = rows[:top_n]

    rows = [r for r in rows if _get_participant_name(r) != 'Unknown']

    def _has_any_activity(row):
        for pr in row.get('problemResults', []):
            if pr.get('points', 0) > 0 or pr.get('rejectedAttemptCount', 0) > 0:
                return True
        return False
    rows = [r for r in rows if _has_any_activity(row=r)]

    contest_name = contest.get('name', f'CF Contest #{contest_id}')
    duration_sec = contest.get('durationSeconds', 0)
    contest_type = contest.get('type', 'ICPC')
    scoring_type = 'icpc' if contest_type == 'ICPC' else 'all_or_nothing'

    olympiad_id = 'CF' + str(contest_id)[-6:].zfill(6)
    if len(olympiad_id) > 8:
        olympiad_id = olympiad_id[:8]
    elif len(olympiad_id) < 8:
        olympiad_id = olympiad_id.ljust(8, '0')

    tasks = []
    problem_index_to_id = {}
    for i, prob in enumerate(problems):
        task_id = f"cf_{contest_id}_{prob.get('index', chr(65 + i))}"
        problem_index_to_id[i] = task_id
        tasks.append({'id': task_id, 'title': f"{prob.get('index', '?')}. {prob.get('name', 'Problem')}"})

    freeze_time_sec = duration_sec - (freeze_minutes * 60) if freeze_minutes > 0 else duration_sec
    if freeze_time_sec < 0:
        freeze_time_sec = 0

    participant_uuids = {}
    for row in rows:
        key = _get_participant_key(row)
        participant_uuids[key] = str(uuid.uuid5(uuid.NAMESPACE_URL, f"cf:{contest_id}:{key}"))

    allowed_sub_types = set(participant_types) if participant_types else {'CONTESTANT'}
    if 'VIRTUAL' in allowed_sub_types:
        allowed_sub_types.update({'CONTESTANT', 'OUT_OF_COMPETITION', 'PRACTICE', 'MANAGER'})

    # Build final scoreboard: prefer submissions data for accurate last_attempt_time
    if submissions:
        participant_subs = _index_submissions(submissions, allowed_sub_types)
        final_scoreboard = _build_final_from_submissions(
            rows, problems, problem_index_to_id, participant_uuids,
            participant_subs, contest_type)
    else:
        participant_subs = None
        final_scoreboard = _build_final_from_standings(
            rows, problems, problem_index_to_id, participant_uuids, contest_type)

    if freeze_minutes > 0:
        if participant_subs:
            frozen_scoreboard = _build_frozen_from_submissions(
                rows, problems, problem_index_to_id, participant_uuids,
                participant_subs, freeze_time_sec, contest_type)
        else:
            frozen_scoreboard = _build_frozen_from_standings(
                rows, problems, problem_index_to_id, participant_uuids,
                freeze_time_sec, contest_type)
    else:
        frozen_scoreboard = copy.deepcopy(final_scoreboard)

    _sort_scoreboard(final_scoreboard, scoring_type)
    _sort_scoreboard(frozen_scoreboard, scoring_type)

    first_solves = _compute_first_solves(rows, problems, problem_index_to_id, participant_uuids)

    return {
        'olympiad_id': olympiad_id,
        'frozen_scoreboard': frozen_scoreboard,
        'final_scoreboard': final_scoreboard,
        'freeze_time': time.time() - (duration_sec - freeze_time_sec),
        'tasks': tasks,
        'contest_name': contest_name,
        'scoring_type': scoring_type,
        'participants_count': len(rows),
        'problems_count': len(problems),
        'duration_seconds': duration_sec,
        'first_solves': first_solves,
    }


def _index_submissions(submissions, allowed_types=None):
    if not allowed_types:
        allowed_types = {'CONTESTANT'}
    idx = {}
    for sub in submissions:
        author = sub.get('author', {})
        if author.get('participantType') not in allowed_types:
            continue
        key = _get_participant_key_from_sub(author)
        if key not in idx:
            idx[key] = []
        idx[key].append(sub)
    for key in idx:
        idx[key].sort(key=lambda s: s.get('relativeTimeSeconds', 0))
    return idx


def _sort_scoreboard(scoreboard, scoring_type):
    if scoring_type == 'icpc':
        scoreboard.sort(key=lambda p: (-p['solved_count'], p['total_penalty']))
    else:
        scoreboard.sort(key=lambda p: (-p['total_score'], p['total_penalty']))


# ──────────────────────────────────────────────────────────────────────────────
# Превью и полная загрузка
# ──────────────────────────────────────────────────────────────────────────────

def get_contest_preview(contest_id, api_key=None, api_secret=None):
    info = fetch_contest_info(contest_id, api_key, api_secret)
    contest = info['contest']
    problems = info['problems']
    phase = contest.get('phase', 'UNKNOWN')
    if phase not in ('FINISHED',):
        raise RuntimeError(
            f"Контест ещё не завершён (фаза: {phase}). "
            f"Импорт возможен только для завершённых контестов."
        )
    duration_sec = contest.get('durationSeconds', 0)
    duration_min = duration_sec // 60
    return {
        'contest_id': contest_id,
        'name': contest.get('name', f'Contest #{contest_id}'),
        'type': contest.get('type', 'ICPC'),
        'phase': phase,
        'duration_minutes': duration_min,
        'duration_display': f"{duration_min // 60}ч {duration_min % 60}мин",
        'problems': [{'index': p.get('index', '?'), 'name': p.get('name', 'Problem')} for p in problems],
        'problems_count': len(problems),
        'scoring_type': 'icpc' if contest.get('type') == 'ICPC' else 'points',
    }


def get_full_contest_data(contest_id, freeze_minutes, api_key=None, api_secret=None,
                          participant_types=None, top_n=None):
    show_unofficial = True
    standings = fetch_contest_standings(contest_id, api_key, api_secret, show_unofficial=show_unofficial)
    # Always fetch submissions for accurate last_attempt_time on all tasks
    # (contest.standings only has bestSubmissionTimeSeconds for AC,
    #  contest.status provides relativeTimeSeconds for every submission)
    try:
        submissions = fetch_contest_status(contest_id, api_key, api_secret)
    except RuntimeError:
        submissions = []
    return transform_cf_to_synaqmaker(
        standings, submissions, freeze_minutes, contest_id,
        participant_types=participant_types, top_n=top_n)
