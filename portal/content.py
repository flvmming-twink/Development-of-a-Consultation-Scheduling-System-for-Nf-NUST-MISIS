from datetime import date, datetime, time, timedelta, timezone
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for
from flask_babel import gettext as _
from sqlalchemy import select

from .auth import audit, roles_required, admin_permissions_required
from .permissions import has_admin_permission
from .models import db, NewsPost, NewsRead, SiteSettings, User, utcnow


bp = Blueprint('content', __name__)


class RichTextCleaner(HTMLParser):
    allowed = {'p', 'br', 'strong', 'em', 'u', 'h2', 'h3', 'ul', 'ol', 'li', 'blockquote', 'a'}
    renamed = {'b': 'strong', 'i': 'em', 'div': 'p'}
    blocked = {'script', 'style', 'iframe', 'object', 'embed', 'svg', 'math'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.blocked_depth = 0
        self.has_text = False

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in self.blocked:
            self.blocked_depth += 1
            return
        if self.blocked_depth:
            return
        tag = self.renamed.get(tag, tag)
        if tag not in self.allowed:
            return
        if tag == 'a':
            values = dict(attrs)
            href = values.get('href', '').strip()
            title = values.get('title', '').strip()[:200]
            parsed = urlsplit(href)
            safe_href = parsed.scheme in ('http', 'https', 'mailto') or (
                not parsed.scheme and not parsed.netloc and (href.startswith('/') or href.startswith('#'))
            )
            attributes = f' href="{escape(href, quote=True)}"' if href and safe_href else ''
            if title:
                attributes += f' title="{escape(title, quote=True)}"'
            if parsed.scheme in ('http', 'https'):
                attributes += ' rel="noopener noreferrer"'
            self.parts.append(f'<a{attributes}>')
        elif tag == 'br':
            self.parts.append('<br>')
        else:
            self.parts.append(f'<{tag}>')

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in self.blocked:
            self.blocked_depth = max(0, self.blocked_depth - 1)
            return
        if self.blocked_depth:
            return
        tag = self.renamed.get(tag, tag)
        if tag in self.allowed and tag != 'br':
            self.parts.append(f'</{tag}>')

    def handle_data(self, data):
        if self.blocked_depth:
            return
        if data.strip():
            self.has_text = True
        self.parts.append(escape(data))


def clean_rich_text(value, limit=50000):
    raw = (value or '').strip()
    if len(raw) > limit * 2:
        raise ValueError(_('Форматированный текст слишком большой.'))
    cleaner = RichTextCleaner()
    cleaner.feed(raw)
    cleaner.close()
    result = ''.join(cleaner.parts).strip()
    if len(result) > limit:
        raise ValueError(_('Форматированный текст слишком большой.'))
    return result if cleaner.has_text else ''


def site_settings():
    settings = db.session.get(SiteSettings, 1)
    if not settings:
        settings = SiteSettings(id=1, maintenance=False, about_html='', announcement_enabled=False,
            announcement_text='', announcement_color='info')
        db.session.add(settings)
        db.session.flush()
    return settings


@bp.get('/about')
@roles_required()
def about():
    return render_template('about.html', settings=site_settings())


@bp.post('/about')
@admin_permissions_required('about.manage')
def save_about():
    try:
        settings = site_settings()
        settings.about_html = clean_rich_text(request.form.get('body_html'))
        audit('about_updated', 'site', f'length={len(settings.about_html)}')
        db.session.commit()
        flash(_('Раздел «О проекте» сохранён.'), 'success')
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), 'error')
        return render_template('about.html', settings=site_settings(), draft=''), 400
    return redirect(url_for('content.about'))


def news_form(post=None):
    if g.user.role not in ('teacher', 'admin'):
        abort(403)
    if g.user.role == 'admin' and not has_admin_permission(g.user, 'news.manage'):
        abort(403)
    if post and g.user.role != 'admin' and post.author_id != g.user.id:
        abort(403)
    editing = post is not None
    if request.method == 'POST':
        cleaned_body = ''
        try:
            cleaned_body = clean_rich_text(request.form.get('body_html'), 30000)
            title = ' '.join(request.form.get('title', '').split())
            if not 3 <= len(title) <= 200:
                raise ValueError(_('Заголовок новости должен содержать от 3 до 200 символов.'))
            if not cleaned_body:
                raise ValueError(_('Добавьте текст новости.'))
            if post:
                post.title = title
                post.body_html = cleaned_body
                post.updated_at = utcnow()
                audit('news_updated', post.id, title)
            else:
                post = NewsPost(author_id=g.user.id, author_name=g.user.full_name or g.user.login,
                    author_role=g.user.role, title=title, body_html=cleaned_body,
                    published_at=utcnow(), updated_at=utcnow())
                db.session.add(post)
                db.session.flush()
                audit('news_created', post.id, title)
            db.session.commit()
            flash(_('Новость обновлена.') if editing else _('Новость опубликована.'), 'success')
            return redirect(url_for('content.news_detail', post_id=post.id))
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), 'error')
            return render_template('news_form.html', post=post,
                draft={'title': request.form.get('title', ''), 'body_html': cleaned_body}), 400
    return render_template('news_form.html', post=post)


@bp.route('/news/new', methods=['GET', 'POST'])
@roles_required('teacher', 'admin')
def news_new():
    return news_form()


@bp.route('/news/<int:post_id>/edit', methods=['GET', 'POST'])
@roles_required('teacher', 'admin')
def news_edit(post_id):
    return news_form(db.get_or_404(NewsPost, post_id))


@bp.get('/news')
@roles_required()
def news():
    query = select(NewsPost)
    teacher_id = request.args.get('teacher', type=int)
    if teacher_id:
        query = query.where(NewsPost.author_id == teacher_id, NewsPost.author_role == 'teacher')
    selected_date = None
    if request.args.get('date'):
        try:
            selected_date = date.fromisoformat(request.args['date'])
            zone = ZoneInfo(current_app.config['APP_TIMEZONE'])
            begin = datetime.combine(selected_date, time.min, tzinfo=zone).astimezone(timezone.utc)
            query = query.where(NewsPost.published_at >= begin, NewsPost.published_at < begin + timedelta(days=1))
        except ValueError:
            flash(_('Некорректная дата фильтра.'), 'error')
    ordering = request.args.get('sort', 'newest')
    if ordering == 'oldest':
        query = query.order_by(NewsPost.published_at, NewsPost.id)
    elif ordering == 'teacher':
        query = query.order_by(NewsPost.author_name, NewsPost.published_at.desc())
    else:
        ordering = 'newest'
        query = query.order_by(NewsPost.published_at.desc(), NewsPost.id.desc())
    page = db.paginate(query, per_page=15, error_out=False)
    post_ids = [post.id for post in page.items]
    read_ids = set(db.session.scalars(select(NewsRead.post_id).where(
        NewsRead.user_id == g.user.id, NewsRead.post_id.in_(post_ids))).all()) if post_ids else set()
    teachers = db.session.scalars(select(User).where(User.role == 'teacher', User.deleted_at.is_(None))
        .order_by(User.full_name, User.login)).all()
    return render_template('news.html', page=page, read_ids=read_ids, teachers=teachers,
        teacher_id=teacher_id, selected_date=selected_date, ordering=ordering)


@bp.get('/news/<int:post_id>')
@roles_required()
def news_detail(post_id):
    post = db.get_or_404(NewsPost, post_id)
    if not db.session.get(NewsRead, (g.user.id, post.id)):
        db.session.add(NewsRead(user_id=g.user.id, post_id=post.id, read_at=utcnow()))
        db.session.commit()
    return render_template('news_detail.html', post=post)


@bp.post('/news/<int:post_id>/delete')
@roles_required('teacher', 'admin')
def news_delete(post_id):
    post = db.get_or_404(NewsPost, post_id)
    if g.user.role == 'admin' and not has_admin_permission(g.user, 'news.manage'):
        abort(403)
    if g.user.role != 'admin' and post.author_id != g.user.id:
        abort(403)
    title = post.title
    db.session.delete(post)
    audit('news_deleted', post_id, title)
    db.session.commit()
    flash(_('Новость удалена.'), 'success')
    return redirect(url_for('content.news'))
