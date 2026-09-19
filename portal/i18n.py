from urllib.parse import urlsplit
from flask import Blueprint, abort, g, has_request_context, redirect, request, session, url_for
from flask_babel import Babel
from .models import db

bp = Blueprint('language', __name__)
babel = Babel()


def current_language():
    if not has_request_context():
        return 'ru'
    user = getattr(g, 'admin_user', None) or getattr(g, 'user', None)
    return (user.language if user else session.get('language', 'ru')) or 'ru'


def safe_return(value):
    try:
        parsed = urlsplit(value or '')
    except ValueError:
        return url_for('main.home')
    if parsed.scheme or parsed.netloc or not parsed.path.startswith('/') or value.startswith('//') or '\\' in value or any(ord(c) < 32 for c in value):
        return url_for('main.home')
    return value


@bp.post('/language')
def change():
    language = request.form.get('language')
    if language not in ('ru', 'en'):
        abort(400)
    session['language'] = language
    user = getattr(g, 'admin_user', None) or getattr(g, 'user', None)
    if user:
        user.language = language
        db.session.commit()
    return redirect(safe_return(request.form.get('next', '/')))
