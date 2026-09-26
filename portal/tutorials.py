import re
from threading import Lock
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlsplit
from urllib.request import Request, urlopen

from flask import Blueprint, jsonify, g, redirect, request, url_for
from flask_babel import gettext as _
from sqlalchemy import select

from .auth import audit, roles_required
from .models import db, TutorialVideo, User, utcnow


bp = Blueprint('tutorials', __name__, url_prefix='/tutorial')
VIDEO_ID = re.compile(r'^[0-9a-fA-F]{32}$')
ACCESS_KEY = re.compile(r'^[A-Za-z0-9_-]{1,200}$')
_availability_cache = {}
_availability_lock = Lock()


def normalize_rutube_url(value):
    value = (value or '').strip()
    if not value:
        raise ValueError(_('Укажите ссылку на видео RUTUBE.'))
    if len(value) > 500:
        raise ValueError(_('Ссылка на видео слишком длинная.'))
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or (parsed.hostname or '').lower() not in ('rutube.ru', 'www.rutube.ru'):
        raise ValueError(_('Используйте ссылку на видео с сайта rutube.ru по протоколу HTTPS.'))
    parts = [part for part in parsed.path.split('/') if part]
    video_id = None
    if len(parts) >= 3 and parts[:2] == ['play', 'embed']:
        video_id = parts[2]
    elif len(parts) >= 2 and parts[0] in ('video', 'shorts'):
        video_id = parts[2] if parts[0] == 'video' and parts[1] == 'private' and len(parts) >= 3 else parts[1]
    if not video_id or not VIDEO_ID.fullmatch(video_id):
        raise ValueError(_('Не удалось определить идентификатор видео RUTUBE из ссылки.'))
    access_key = parse_qs(parsed.query).get('p', [''])[0]
    if access_key and not ACCESS_KEY.fullmatch(access_key):
        raise ValueError(_('Ссылка RUTUBE содержит некорректный ключ доступа.'))
    embed_url = f'https://rutube.ru/play/embed/{video_id}'
    if access_key:
        embed_url += '/?' + urlencode({'p': access_key}, quote_via=quote)
    return embed_url


def probe_rutube_video(embed_url, referer):
    parsed = urlsplit(embed_url)
    parts = [part for part in parsed.path.split('/') if part]
    if len(parts) != 3 or parts[:2] != ['play', 'embed'] or not VIDEO_ID.fullmatch(parts[2]):
        return False
    query = parse_qs(parsed.query)
    api_url = f'https://rutube.ru/api/play/options/{parts[2]}'
    if query.get('p'):
        api_url += '?' + urlencode({'p': query['p'][0]}, quote_via=quote)
    req = Request(api_url, method='HEAD', headers={
        'User-Agent': 'MISIS-Consultations/1.0',
        'Referer': referer,
    })
    try:
        with urlopen(req, timeout=8) as response:
            return 200 <= response.status < 300 and response.headers.get_content_type() == 'application/json'
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        return False


def rutube_video_available(embed_url, referer):
    key = (embed_url, referer)
    now = monotonic()
    cached = _availability_cache.get(key)
    if cached and cached[0] > now:
        return cached[1]
    with _availability_lock:
        cached = _availability_cache.get(key)
        if cached and cached[0] > monotonic():
            return cached[1]
        available = probe_rutube_video(embed_url, referer)
        _availability_cache[key] = (monotonic() + (300 if available else 30), available)
        return available


def clear_rutube_availability_cache():
    with _availability_lock:
        _availability_cache.clear()


@bp.get('/status')
@roles_required()
def status():
    video = db.session.get(TutorialVideo, g.user.role)
    if not video or not rutube_video_available(video.embed_url, request.url_root):
        return jsonify(available=False)
    return jsonify(available=True, embed_url=video.embed_url)


@bp.post('/dismiss')
@roles_required()
def dismiss():
    user = db.session.execute(
        select(User).where(User.id == g.user.id).with_for_update().execution_options(populate_existing=True)
    ).scalar_one()
    if user.tutorial_seen_at is None:
        user.tutorial_seen_at = utcnow()
        audit('tutorial_dismissed', user.role)
        db.session.commit()
    target = request.form.get('next', '')
    if not target.startswith('/') or target.startswith('//'):
        target = url_for('main.home')
    return redirect(target)
