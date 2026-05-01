"""
db_manager.py — Менеджер базы данных для Synaqmaker Resolver.

Хранит импортированные контесты, замороженные/финальные скорборды,
настройки церемоний, группы участников.
"""

import json
import os
from sqlalchemy import create_engine, Column, Integer, Float, Text, Boolean, Index, event, text
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import QueuePool

Base = declarative_base()


# ──────────────────────────────────────────────────────────────────────────────
# ORM Models
# ──────────────────────────────────────────────────────────────────────────────

class Contest(Base):
    """Импортированный контест."""
    __tablename__ = 'contests'
    olympiad_id = Column(Text, primary_key=True)
    name = Column(Text)
    scoring_type = Column(Text, default='icpc')
    tasks_json = Column(Text)         # [{id, title}, ...]
    first_solves_json = Column(Text)  # {task_id: participant_id}
    groups_json = Column(Text)        # {"Group A": ["uuid1", ...]}
    created_at = Column(Float)


class FrozenData(Base):
    """Замороженные и финальные скорборды."""
    __tablename__ = 'frozen_data'
    olympiad_id = Column(Text, primary_key=True)
    frozen_scoreboard_json = Column(Text)
    final_scoreboard_json = Column(Text)
    freeze_time = Column(Float)
    is_revealed = Column(Boolean, default=False)
    ceremony_settings_json = Column(Text)


class Participant(Base):
    """Участники контеста."""
    __tablename__ = 'participants'
    id = Column(Integer, primary_key=True, autoincrement=True)
    olympiad_id = Column(Text, nullable=False)
    participant_uuid = Column(Text, nullable=False)
    nickname = Column(Text, nullable=False)
    organization = Column(Text)
    handle = Column(Text)
    total_score = Column(Integer, default=0)
    total_penalty = Column(Integer, default=0)
    solved_count = Column(Integer, default=0)
    task_scores_json = Column(Text)
    disqualified = Column(Boolean, default=False)

    __table_args__ = (
        Index('ix_part_oid', 'olympiad_id'),
        Index('ix_part_uuid', 'olympiad_id', 'participant_uuid', unique=True),
    )


# ──────────────────────────────────────────────────────────────────────────────
# RowProxy — dict-like row access
# ──────────────────────────────────────────────────────────────────────────────

class RowProxy:
    """Lightweight dict-like wrapper for SQL result rows."""
    __slots__ = ('_keys', '_values')

    def __init__(self, keys, values):
        self._keys = list(keys)
        self._values = list(values)

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        try:
            idx = self._keys.index(key)
            return self._values[idx]
        except ValueError:
            raise KeyError(key)

    def get(self, key, default=None):
        try:
            return self[key]
        except (KeyError, IndexError):
            return default

    def keys(self):
        return self._keys

    def values(self):
        return self._values

    def items(self):
        return zip(self._keys, self._values)

    def __repr__(self):
        return f"RowProxy({dict(zip(self._keys, self._values))})"


def _rows_to_proxy(result):
    keys = list(result.keys()) if result.returns_rows else []
    return [RowProxy(keys, list(row)) for row in result.fetchall()] if keys else []


def _row_to_proxy(result):
    keys = list(result.keys()) if result.returns_rows else []
    row = result.fetchone()
    return RowProxy(keys, list(row)) if row else None


# ──────────────────────────────────────────────────────────────────────────────
# Database Manager
# ──────────────────────────────────────────────────────────────────────────────

class DBManager:
    def __init__(self, db_path=None):
        if db_path is None:
            db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resolver.db')
        db_url = f'sqlite:///{db_path}'
        self.engine = create_engine(
            db_url,
            pool_size=5,
            max_overflow=10,
            poolclass=QueuePool,
            connect_args={'check_same_thread': False},
        )

        @event.listens_for(self.engine, 'connect')
        def _set_sqlite_pragma(dbapi_conn, connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute('PRAGMA journal_mode=WAL')
            cursor.execute('PRAGMA busy_timeout=30000')
            cursor.execute('PRAGMA synchronous=NORMAL')
            cursor.execute('PRAGMA foreign_keys=ON')
            cursor.close()

        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def _execute(self, sql, params=None, fetch='all', commit=False):
        with self.engine.connect() as conn:
            result = conn.execute(text(sql), params or {})
            if commit:
                conn.commit()
            if fetch == 'all':
                return _rows_to_proxy(result)
            elif fetch == 'one':
                return _row_to_proxy(result)
            return None

    # ── Contest CRUD ──

    def save_contest(self, olympiad_id, name, scoring_type, tasks, first_solves=None, created_at=None):
        import time as _t
        tasks_json = json.dumps(tasks, ensure_ascii=False)
        fs_json = json.dumps(first_solves) if first_solves else None
        ts = created_at or _t.time()
        self._execute(
            """INSERT INTO contests (olympiad_id, name, scoring_type, tasks_json, first_solves_json, created_at)
               VALUES (:oid, :name, :sc, :tj, :fs, :ts)
               ON CONFLICT(olympiad_id) DO UPDATE SET
               name=excluded.name, scoring_type=excluded.scoring_type,
               tasks_json=excluded.tasks_json, first_solves_json=excluded.first_solves_json""",
            {'oid': olympiad_id, 'name': name, 'sc': scoring_type,
             'tj': tasks_json, 'fs': fs_json, 'ts': ts},
            fetch=None, commit=True
        )

    def get_contest(self, olympiad_id):
        row = self._execute(
            "SELECT * FROM contests WHERE olympiad_id = :oid",
            {'oid': olympiad_id}, fetch='one')
        if not row:
            return None
        return {
            'olympiad_id': row['olympiad_id'],
            'name': row['name'],
            'scoring_type': row['scoring_type'],
            'tasks': json.loads(row['tasks_json']) if row['tasks_json'] else [],
            'first_solves': json.loads(row['first_solves_json']) if row.get('first_solves_json') else {},
            'groups': json.loads(row['groups_json']) if row.get('groups_json') else {},
            'created_at': row['created_at'],
        }

    def get_all_contests(self):
        rows = self._execute("""
            SELECT c.olympiad_id, c.name, c.scoring_type, c.created_at,
                   (SELECT COUNT(*) FROM participants p WHERE p.olympiad_id = c.olympiad_id) as participants_count
            FROM contests c
            ORDER BY c.created_at DESC
        """)
        return rows

    def delete_contest(self, olympiad_id):
        try:
            with self.engine.begin() as conn:
                conn.execute(text("DELETE FROM participants WHERE olympiad_id = :oid"), {'oid': olympiad_id})
                conn.execute(text("DELETE FROM frozen_data WHERE olympiad_id = :oid"), {'oid': olympiad_id})
                conn.execute(text("DELETE FROM contests WHERE olympiad_id = :oid"), {'oid': olympiad_id})
            return True
        except Exception as e:
            print(f"DB Error: Failed to delete contest {olympiad_id}: {e}")
            return False

    # ── Frozen Data ──

    def save_frozen_scoreboard(self, olympiad_id, frozen_scoreboard, final_scoreboard, freeze_time):
        frozen_json = json.dumps(frozen_scoreboard)
        final_json = json.dumps(final_scoreboard)
        self._execute(
            """INSERT INTO frozen_data (olympiad_id, frozen_scoreboard_json, final_scoreboard_json,
                   freeze_time, is_revealed)
               VALUES (:oid, :fj, :ffj, :ft, 0)
               ON CONFLICT(olympiad_id) DO UPDATE SET
               frozen_scoreboard_json=excluded.frozen_scoreboard_json,
               final_scoreboard_json=excluded.final_scoreboard_json,
               freeze_time=excluded.freeze_time""",
            {'oid': olympiad_id, 'fj': frozen_json, 'ffj': final_json, 'ft': freeze_time},
            fetch=None, commit=True
        )

    def get_frozen_data(self, olympiad_id):
        row = self._execute(
            "SELECT * FROM frozen_data WHERE olympiad_id = :oid",
            {'oid': olympiad_id}, fetch='one')
        if row:
            return {
                'frozen_scoreboard': json.loads(row['frozen_scoreboard_json']) if row['frozen_scoreboard_json'] else [],
                'final_scoreboard': json.loads(row['final_scoreboard_json']) if row['final_scoreboard_json'] else [],
                'freeze_time': row['freeze_time'],
                'is_revealed': row['is_revealed'],
            }
        return None

    def mark_revealed(self, olympiad_id):
        self._execute(
            "UPDATE frozen_data SET is_revealed = 1 WHERE olympiad_id = :oid",
            {'oid': olympiad_id}, fetch=None, commit=True)

    def export_frozen_json(self, olympiad_id):
        """Export frozen data as dict for JSON download."""
        frozen = self.get_frozen_data(olympiad_id)
        if not frozen:
            return None
        contest = self.get_contest(olympiad_id)
        tasks = contest['tasks'] if contest else []
        return {
            'olympiad_id': olympiad_id,
            'tasks': tasks,
            'frozen_scoreboard': frozen['frozen_scoreboard'],
            'final_scoreboard': frozen['final_scoreboard'],
            'freeze_time': frozen['freeze_time'],
        }

    # ── Ceremony Settings ──

    def save_ceremony_settings(self, olympiad_id, settings):
        settings_json = json.dumps(settings, ensure_ascii=False)
        self._execute(
            "UPDATE frozen_data SET ceremony_settings_json = :sj WHERE olympiad_id = :oid",
            {'sj': settings_json, 'oid': olympiad_id}, fetch=None, commit=True)

    def get_ceremony_settings(self, olympiad_id):
        row = self._execute(
            "SELECT ceremony_settings_json FROM frozen_data WHERE olympiad_id = :oid",
            {'oid': olympiad_id}, fetch='one')
        if row and row['ceremony_settings_json']:
            return json.loads(row['ceremony_settings_json'])
        return None

    # ── Groups ──

    def save_groups(self, olympiad_id, groups_dict):
        groups_json = json.dumps(groups_dict, ensure_ascii=False) if groups_dict else None
        self._execute(
            "UPDATE contests SET groups_json = :gj WHERE olympiad_id = :oid",
            {'gj': groups_json, 'oid': olympiad_id}, fetch=None, commit=True)

    def get_groups(self, olympiad_id):
        row = self._execute(
            "SELECT groups_json FROM contests WHERE olympiad_id = :oid",
            {'oid': olympiad_id}, fetch='one')
        if row and row.get('groups_json'):
            try:
                return json.loads(row['groups_json'])
            except (json.JSONDecodeError, TypeError):
                pass
        return None

    # ── First Solves ──

    def save_first_solves(self, olympiad_id, first_solves_dict):
        fs_json = json.dumps(first_solves_dict) if first_solves_dict else None
        self._execute(
            "UPDATE contests SET first_solves_json = :fs WHERE olympiad_id = :oid",
            {'fs': fs_json, 'oid': olympiad_id}, fetch=None, commit=True)

    def get_first_solves(self, olympiad_id):
        row = self._execute(
            "SELECT first_solves_json FROM contests WHERE olympiad_id = :oid",
            {'oid': olympiad_id}, fetch='one')
        if row and row.get('first_solves_json'):
            try:
                return json.loads(row['first_solves_json'])
            except (json.JSONDecodeError, TypeError):
                pass
        return {}

    # ── Participants ──

    def save_participants(self, olympiad_id, final_scoreboard):
        """Save all participants from the final scoreboard."""
        with self.engine.begin() as conn:
            for p in final_scoreboard:
                conn.execute(text("""
                    INSERT INTO participants (olympiad_id, participant_uuid, nickname, organization,
                        handle, total_score, total_penalty, solved_count, task_scores_json, disqualified)
                    VALUES (:oid, :uuid, :nick, :org, :handle, :ts, :tp, :sc, :tsj, :dq)
                    ON CONFLICT(olympiad_id, participant_uuid) DO UPDATE SET
                    nickname=excluded.nickname, organization=excluded.organization,
                    total_score=excluded.total_score, total_penalty=excluded.total_penalty,
                    solved_count=excluded.solved_count, task_scores_json=excluded.task_scores_json
                """), {
                    'oid': olympiad_id,
                    'uuid': p['participant_id'],
                    'nick': p['nickname'],
                    'org': p.get('organization', ''),
                    'handle': p.get('handle', ''),
                    'ts': p.get('total_score', 0),
                    'tp': p.get('total_penalty', 0),
                    'sc': p.get('solved_count', 0),
                    'tsj': json.dumps(p.get('scores', {})),
                    'dq': p.get('disqualified', False),
                })

    def get_participants_for_grouping(self, olympiad_id):
        rows = self._execute(
            "SELECT participant_uuid, nickname, organization FROM participants WHERE olympiad_id = :oid ORDER BY nickname",
            {'oid': olympiad_id})
        return [{'uuid': r['participant_uuid'], 'nickname': r['nickname'],
                 'organization': r['organization'] or ''} for r in rows]

    def auto_group_by_org(self, olympiad_id):
        participants = self.get_participants_for_grouping(olympiad_id)
        groups = {}
        ungrouped = []
        for p in participants:
            org = (p.get('organization') or '').strip()
            if org:
                groups.setdefault(org, []).append(p['uuid'])
            else:
                ungrouped.append(p['uuid'])
        if ungrouped:
            groups['Без группы'] = ungrouped
        return groups

    def get_results(self, olympiad_id):
        """Get full results for archive view."""
        contest = self.get_contest(olympiad_id)
        if not contest:
            return None
        frozen = self.get_frozen_data(olympiad_id)
        if not frozen:
            return None

        participants = frozen['final_scoreboard']
        scoring_type = contest['scoring_type']

        if scoring_type == 'icpc':
            participants.sort(key=lambda p: (-p.get('solved_count', 0), p.get('total_penalty', 0)))
        else:
            participants.sort(key=lambda p: (-p.get('total_score', 0), p.get('total_penalty', 0)))

        return {
            'contest': contest,
            'participants': participants,
            'tasks': contest['tasks'],
            'first_solves': contest.get('first_solves', {}),
        }
