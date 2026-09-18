import os
import threading
from waitress import serve
from wsgi import app
from portal.models import db
from portal.lifecycle import run_maintenance

def maintain():
    stop = threading.Event()
    while not stop.is_set():
        with app.app_context():
            try:
                run_maintenance()
            except Exception:
                db.session.rollback()
                app.logger.exception('Scheduled maintenance failed')
        stop.wait(60)

if __name__ == '__main__':
    threading.Thread(target=maintain, name='portal-maintenance', daemon=True).start()
    serve(app, host=os.getenv('APP_BIND', '0.0.0.0'), port=int(os.getenv('PORT', os.getenv('APP_PORT', '8000'))), threads=4)
