# Synaqmaker Resolver 🏆

**Standalone ICPC-style Results Resolver** — красивая разморозка результатов олимпиад по программированию.

## ✨ Возможности

- 🧊 **ICPC Resolver** — пошаговая разморозка замороженного scoreboard с анимациями
- 🏅 **Церемония награждения** — золото/серебро/бронза + пользовательские номинации
- ⭐ **First to Solve** — автоматическое определение и отображение первых решений
- 🎖️ **Honorable Mention** — красивый оверлей для благодарственных писем (стиль Star Wars credits)
- 📥 **Импорт из Codeforces** — прямой импорт контестов по ID с API ключами
- 📤 **JSON импорт** — универсальный формат для любых платформ
- 👥 **Группы** — автоматическая группировка по организациям, фильтрация по группам
- 📊 **Экспорт** — JSON и Excel выгрузка результатов
- 🌍 **Мультиязычность** — RU / EN / KZ

## 🚀 Быстрый старт

### Windows
1. Запустите `1_INSTALL.bat` для установки зависимостей
2. Запустите `2_START.bat` для запуска сервера
3. Откройте http://127.0.0.1:5050

### Вручную
```bash
pip install -r requirements.txt
python run.py
```

## 🔐 Вход

**Пароль по умолчанию:** `resolver2025`

Измените в `config.ini` → `ADMIN_PASSWORD`

## 📋 Формат JSON для импорта

```json
{
  "name": "Название контеста",
  "scoring": "icpc",
  "freeze_minutes": 60,
  "tasks": [
    {"id": "A", "name": "Задача A"},
    {"id": "B", "name": "Задача B"}
  ],
  "participants": [
    {
      "id": "1",
      "nickname": "Имя участника",
      "organization": "Университет"
    }
  ],
  "frozen_scoreboard": [
    {
      "participant_id": "1",
      "nickname": "Имя участника",
      "organization": "Университет",
      "solved_count": 2,
      "total_penalty": 150,
      "total_score": 2,
      "scores": {
        "A": {"score": 1, "attempts": 0, "passed": true, "penalty": 50, "last_attempt_time": 50},
        "B": {"score": 1, "attempts": 1, "passed": true, "penalty": 100, "last_attempt_time": 80}
      }
    }
  ],
  "final_scoreboard": [
    {
      "participant_id": "1",
      "nickname": "Имя участника",
      "organization": "Университет",
      "solved_count": 3,
      "total_penalty": 250,
      "total_score": 3,
      "scores": {
        "A": {"score": 1, "attempts": 0, "passed": true, "penalty": 50, "last_attempt_time": 50},
        "B": {"score": 1, "attempts": 1, "passed": true, "penalty": 100, "last_attempt_time": 80},
        "C": {"score": 1, "attempts": 2, "passed": true, "penalty": 200, "last_attempt_time": 180}
      }
    }
  ],
  "first_solves": {
    "A": "1",
    "B": "1"
  }
}
```

## ⌨️ Горячие клавиши (Presentation)

| Клавиша | Действие |
|---------|----------|
| **Space** | Следующий шаг (зажмите для непрерывной разморозки) |
| **←** / **↑** | Шаг назад (undo) |
| **→** / **↓** | Следующий шаг |
| **H** | Показать/скрыть панель управления |

## 📁 Структура

```
SynaqmakerResolver/
├── app.py              # Основное приложение Flask
├── run.py              # Точка входа
├── db_manager.py       # Работа с базой данных (SQLAlchemy)
├── codeforces_import.py # Импорт из Codeforces API
├── config.ini          # Конфигурация
├── requirements.txt    # Зависимости Python
├── templates/          # HTML шаблоны
│   ├── base.html
│   ├── login.html
│   ├── dashboard.html
│   ├── presentation.html  ← ⭐ Главный резольвер
│   ├── codeforces_import.html
│   ├── json_import.html
│   ├── groups.html
│   ├── archive.html
│   ├── archive_view.html
│   └── error.html
└── static/             # CSS, JS, шрифты
```

## 📄 Лицензия

MIT
