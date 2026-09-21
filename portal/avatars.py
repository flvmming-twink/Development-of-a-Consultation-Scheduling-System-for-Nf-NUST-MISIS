from io import BytesIO
import warnings

from flask import Blueprint, abort, current_app, flash, g, redirect, request, send_file, session, url_for
from flask_babel import gettext as _
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import select, delete

from .auth import audit, roles_required
from .models import db, User, UserAvatar, utcnow

bp = Blueprint('avatars', __name__)


def normalize_avatar(upload):
    if not upload or not upload.filename:
        raise ValueError(_('Выберите фотографию.'))
    raw = upload.read(current_app.config['AVATAR_MAX_BYTES'] + 1)
    if len(raw) > current_app.config['AVATAR_MAX_BYTES']:
        raise ValueError(_('Фотография должна быть не больше 5 МБ.'))
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            # Restrict decoders before opening; filenames and MIME headers are untrusted.
            with Image.open(BytesIO(raw), formats=('JPEG', 'PNG')) as source:
                if source.width * source.height > 16_000_000 or max(source.size) > 8192:
                    raise ValueError(_('Слишком большое разрешение фотографии: максимум 16 мегапикселей и 8192 пикселя по стороне.'))
                if getattr(source, 'n_frames', 1) != 1:
                    raise ValueError(_('Анимированные изображения не поддерживаются.'))
                source.verify()
            with Image.open(BytesIO(raw), formats=('JPEG', 'PNG')) as source:
                picture = ImageOps.fit(ImageOps.exif_transpose(source).convert('RGBA'), (256, 256))
                clean = Image.new('RGB', (256, 256), 'white')
                clean.paste(picture, mask=picture.getchannel('A'))
                output = BytesIO()
                clean.save(output, 'JPEG', quality=85, exif=b'', icc_profile=None)
                return output.getvalue()
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValueError(_('Не удалось прочитать фотографию. Используйте исправный файл JPEG или PNG.')) from None


@bp.get('/avatars/<int:user_id>')
@roles_required()
def image(user_id):
    if user_id != g.user.id and g.user.role != 'admin':
        abort(404)
    avatar = db.get_or_404(UserAvatar, user_id)
    return send_file(BytesIO(avatar.image), mimetype='image/jpeg', download_name='avatar.jpg', max_age=0)


@bp.post('/settings/avatar')
@roles_required()
def settings():
    try:
        action = request.form.get('action')
        if action not in ('upload', 'remove'):
            abort(400)
        data = normalize_avatar(request.files.get('avatar')) if action == 'upload' else None
        user = db.session.scalar(select(User).where(User.id == g.user.id).with_for_update().execution_options(populate_existing=True))
        if not user or not user.active or user.deleted_at or user.session_version != session.get('version'):
            abort(403)
        if action == 'remove':
            db.session.execute(delete(UserAvatar).where(UserAvatar.user_id == user.id))
        else:
            avatar = db.session.get(UserAvatar, user.id)
            if avatar is None:
                avatar = UserAvatar(user_id=user.id)
                db.session.add(avatar)
            avatar.image, avatar.updated_at = data, utcnow()
        audit('avatar_updated', user.id)
        db.session.commit()
        flash(_('Фотография сохранена.') if action == 'upload' else _('Фотография удалена.'), 'success')
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), 'error')
    return redirect(url_for('auth.settings'))
