"""
Synaqmaker Resolver — Launcher
"""
import os
import sys
import configparser

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)
sys.path.insert(0, BASE_DIR)


def main():
    config = configparser.ConfigParser()
    config_path = os.path.join(BASE_DIR, 'config.ini')
    config.read(config_path, encoding='utf-8')

    host = config.get('server', 'HOST', fallback='0.0.0.0')
    port = config.getint('server', 'PORT', fallback=5050)

    print("=" * 60)
    print("  Synaqmaker Resolver v1.0")
    print("=" * 60)
    print(f"  http://127.0.0.1:{port}")
    print(f"  http://0.0.0.0:{port}")
    print("=" * 60)
    print("  Ctrl+C to stop")
    print("=" * 60)

    from app import app, socketio
    socketio.run(app, host=host, port=port, debug=False, log_output=True)


if __name__ == '__main__':
    main()
