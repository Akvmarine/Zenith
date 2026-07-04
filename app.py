from flask import Flask, render_template, request, redirect, url_for, jsonify, flash, send_from_directory, session
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime, timezone, timedelta
from sqlalchemy import func
import os, re, markdown, bleach, random

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///microblog.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'super-secret-key-12345'
app.config['UPLOAD_FOLDER'] = os.path.join(app.instance_path, 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024
app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

MSK = timezone(timedelta(hours=3))


def now_msk(): return datetime.now(MSK)


def format_msk(dt):
    if dt is None: return ''
    if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(MSK).strftime('%d %b %Y, %H:%M')


def format_msk_short(dt):
    if dt is None: return ''
    if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(MSK).strftime('%d %b %Y %H:%M')


# ═══════════════════════════════════════════════════════════
# МОДЕЛИ
# ═══════════════════════════════════════════════════════════
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    is_banned = db.Column(db.Boolean, default=False)
    lang = db.Column(db.String(5), default='ru')
    theme = db.Column(db.String(20), default='cosmic')
    accent_color = db.Column(db.String(7), default='#8b2035')
    bio = db.Column(db.Text, default='')
    avatar = db.Column(db.String(200), default='default.png')
    cover = db.Column(db.String(200), default='default_cover.jpg')
    created_at = db.Column(db.DateTime, default=now_msk)
    xp = db.Column(db.Integer, default=0)
    level = db.Column(db.Integer, default=1)
    posts = db.relationship('Post', backref='author', lazy=True, cascade='all, delete-orphan')
    bookmarks = db.relationship('Bookmark', backref='user', lazy=True, cascade='all, delete-orphan')
    notifications = db.relationship('Notification', backref='user', lazy=True, cascade='all, delete-orphan')
    achievements_rel = db.relationship('UserAchievement', backref='user', lazy=True, cascade='all, delete-orphan')
    streak = db.relationship('Streak', backref='user', uselist=False, cascade='all, delete-orphan')
    activity_logs = db.relationship('ActivityLog', backref='user', lazy=True, cascade='all, delete-orphan')
    followers = db.relationship('Follow', foreign_keys='Follow.following_id', backref='follower', lazy='dynamic')
    following = db.relationship('Follow', foreign_keys='Follow.follower_id', backref='following', lazy='dynamic')
    likes = db.relationship('UserLike', backref='user', lazy=True, cascade='all, delete-orphan')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def is_following(self, user):
        return Follow.query.filter_by(follower_id=self.id, following_id=user.id).first() is not None

    def get_level_info(self):
        levels = [(1, 'Новичок', 0, '#888'), (2, 'Блогер', 50, '#4a9eff'), (3, 'Ветеран', 200, '#a855f7'),
                  (4, 'Мастер', 500, '#f59e0b'), (5, 'Легенда', 1000, '#ef4444')]
        current = levels[0];
        next_level = levels[1]
        for i, (lvl, name, xp_req, color) in enumerate(levels):
            if self.xp >= xp_req:
                current = (lvl, name, xp_req, color)
                next_level = levels[i + 1] if i + 1 < len(levels) else None
        progress = 0
        if next_level:
            progress = ((self.xp - current[2]) / (next_level[2] - current[2])) * 100
            progress = min(100, max(0, progress))
        return {'level': current[0], 'name': current[1], 'xp': self.xp, 'color': current[3],
                'next_level': next_level[1] if next_level else None, 'next_xp': next_level[2] if next_level else None,
                'progress': progress}


class Follow(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    follower_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    following_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=now_msk)


class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    topic = db.Column(db.String(100), default='Общее')
    likes = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=now_msk)
    author_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    image = db.Column(db.String(200), nullable=True)
    is_draft = db.Column(db.Boolean, default=False)
    is_pinned = db.Column(db.Boolean, default=False)
    moderation_score = db.Column(db.Integer, default=0)
    comments = db.relationship('Comment', backref='post', lazy=True, cascade='all, delete-orphan')
    tags = db.relationship('Tag', backref='post', lazy=True, cascade='all, delete-orphan')
    bookmarks = db.relationship('Bookmark', backref='post', lazy=True, cascade='all, delete-orphan')
    poll = db.relationship('Poll', backref='post', uselist=False, cascade='all, delete-orphan')
    # Связь с лайками (backref создает user_likes в User и post в UserLike)
    user_likes = db.relationship('UserLike', backref='post', lazy=True, cascade='all, delete-orphan')

    def to_dict(self):
        return {'id': self.id, 'title': self.title, 'content': self.content[:200], 'topic': self.topic,
                'likes': self.likes, 'author': self.author.username if self.author else 'Неизвестно',
                'created_at': format_msk_short(self.created_at), 'comments_count': len(self.comments),
                'image': self.image}


class Comment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    author_name = db.Column(db.String(100), nullable=False)
    text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=now_msk)
    post_id = db.Column(db.Integer, db.ForeignKey('post.id'), nullable=False)


class Tag(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    post_id = db.Column(db.Integer, db.ForeignKey('post.id'), nullable=False)


class Bookmark(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    post_id = db.Column(db.Integer, db.ForeignKey('post.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=now_msk)


class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    type = db.Column(db.String(50), nullable=False)
    from_user = db.Column(db.String(100), nullable=False)
    post_id = db.Column(db.Integer, nullable=True)
    message = db.Column(db.Text, nullable=False)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=now_msk)


class Achievement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=False)
    icon = db.Column(db.String(50), nullable=False, default='star')
    xp_reward = db.Column(db.Integer, default=0)
    condition_type = db.Column(db.String(50), nullable=False)
    condition_value = db.Column(db.Integer, nullable=False)
    rarity = db.Column(db.String(20), default='common')


class UserAchievement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    achievement_id = db.Column(db.Integer, db.ForeignKey('achievement.id'), nullable=False)
    unlocked_at = db.Column(db.DateTime, default=now_msk)
    achievement = db.relationship('Achievement', backref='unlocks')


class Streak(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), unique=True, nullable=False)
    current_streak = db.Column(db.Integer, default=0)
    longest_streak = db.Column(db.Integer, default=0)
    last_active_date = db.Column(db.Date, nullable=True)


class ActivityLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    action_type = db.Column(db.String(50), nullable=False)
    action_date = db.Column(db.Date, nullable=False, default=lambda: now_msk().date())
    count = db.Column(db.Integer, default=1)


class Poll(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey('post.id'), nullable=False, unique=True)
    question = db.Column(db.String(300), nullable=False)
    created_at = db.Column(db.DateTime, default=now_msk)
    options = db.relationship('PollOption', backref='poll', lazy=True, cascade='all, delete-orphan')
    votes = db.relationship('PollVote', backref='poll', lazy=True, cascade='all, delete-orphan')


class PollOption(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    poll_id = db.Column(db.Integer, db.ForeignKey('poll.id'), nullable=False)
    text = db.Column(db.String(200), nullable=False)
    votes = db.Column(db.Integer, default=0)


class PollVote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    poll_id = db.Column(db.Integer, db.ForeignKey('poll.id'), nullable=False)
    option_id = db.Column(db.Integer, db.ForeignKey('poll_option.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=now_msk)
    __table_args__ = (db.UniqueConstraint('poll_id', 'user_id', name='unique_poll_vote'),)


class Collection(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, default='')
    is_public = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=now_msk)
    user = db.relationship('User', backref='collections')
    items = db.relationship('CollectionItem', backref='collection', lazy=True, cascade='all, delete-orphan')


class CollectionItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    collection_id = db.Column(db.Integer, db.ForeignKey('collection.id'), nullable=False)
    post_id = db.Column(db.Integer, db.ForeignKey('post.id'), nullable=False)
    added_at = db.Column(db.DateTime, default=now_msk)
    post = db.relationship('Post', backref='collection_items')


class Report(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    reporter_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    post_id = db.Column(db.Integer, db.ForeignKey('post.id'), nullable=True)
    comment_id = db.Column(db.Integer, db.ForeignKey('comment.id'), nullable=True)
    reason = db.Column(db.String(50), nullable=False)
    description = db.Column(db.Text, default='')
    status = db.Column(db.String(20), default='pending')
    moderator_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=now_msk)
    resolved_at = db.Column(db.DateTime, nullable=True)
    reporter = db.relationship('User', foreign_keys=[reporter_id], backref='reports_made')
    moderator = db.relationship('User', foreign_keys=[moderator_id], backref='reports_handled')


class Ban(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    banned_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    reason = db.Column(db.Text, default='')
    created_at = db.Column(db.DateTime, default=now_msk)
    is_active = db.Column(db.Boolean, default=True)
    user = db.relationship('User', foreign_keys=[user_id], backref='bans_received')
    banned_by_user = db.relationship('User', foreign_keys=[banned_by], backref='bans_given')


class TelegramSettings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, unique=True)
    bot_token = db.Column(db.String(200), nullable=False)
    chat_id = db.Column(db.String(100), nullable=False)
    notify_posts = db.Column(db.Boolean, default=True)
    notify_comments = db.Column(db.Boolean, default=True)
    notify_likes = db.Column(db.Boolean, default=False)
    enabled = db.Column(db.Boolean, default=True)
    user = db.relationship('User', backref='telegram_settings')


# ИСПРАВЛЕННАЯ МОДЕЛЬ USERLIKE (без дублирования relationships)
class UserLike(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    post_id = db.Column(db.Integer, db.ForeignKey('post.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=now_msk)
    __table_args__ = (db.UniqueConstraint('user_id', 'post_id', name='unique_user_like'),)
    # Relationships определены через backref в User и Post


@login_manager.user_loader
def load_user(user_id): return User.query.get(int(user_id))


# ═══════════════════════════════════════════════════════════
# МУЛЬТИЯЗЫЧНОСТЬ
# ══════════════════════════════════════════════════════════
TRANSLATIONS = {
    'ru': {'feed': 'Лента', 'stats': 'Стата', 'bookmarks': 'Закладки', 'collections': 'Коллекции', 'profile': 'Профиль',
           'logout': 'Выйти', 'login': 'Войти', 'register': 'Регистрация', 'light': 'Светлая', 'dark': 'Тёмная',
           'install': 'Установить', 'search': 'Поиск...', 'all': 'Все', 'following': 'Подписки',
           'new_post': 'Новая запись', 'title_placeholder': 'Заголовок...', 'content_placeholder': 'О чём думаешь?',
           'topic_placeholder': 'Тема', 'image': 'Изображение', 'draft': 'Черновик', 'publish': 'Опубликовать',
           'no_posts': 'Пока нет записей. Будь первым!', 'like': 'Нравится', 'bookmark': 'В закладки',
           'bookmarked': 'В закладках', 'delete': 'Удалить', 'comments': 'Комментарии',
           'no_comments': 'Пока нет комментариев', 'send': 'Отправить', 'anonymous': 'Аноним',
           'name_placeholder': 'Имя', 'comment_placeholder': 'Комментарий...', 'back': 'Назад к ленте',
           'notifications': 'Уведомления', 'no_notifications': 'Уведомлений нет', 'achievements': 'Достижения',
           'received': 'Получено', 'from': 'из', 'enter_to_post': 'Войдите, чтобы создавать посты',
           'popular_tags': 'Популярные теги:', 'more_active': 'Ты активнее, чем', 'percent_users': '% пользователей',
           'edit_profile': 'Редактировать', 'posts': 'постов', 'followers': 'подписчиков', 'followings': 'подписок',
           'likes': 'лайков', 'days_streak': 'дней подряд', 'record': 'рекорд', 'activity_year': 'Активность за год',
           'less': 'Меньше', 'more': 'Больше', 'posts_by_month': 'Посты по месяцам', 'favorite_topics': 'Любимые темы',
           'user_posts': 'Посты пользователя', 'no_user_posts': 'У пользователя пока нет постов',
           'report': 'Пожаловаться', 'report_post': 'Пожаловаться на пост',
           'report_comment': 'Пожаловаться на комментарий', 'report_reason': 'Причина жалобы', 'spam': 'Спам',
           'offensive': 'Оскорбление', 'illegal': 'Незаконный контент', 'other': 'Другое',
           'send_report': 'Отправить жалобу', 'report_sent': 'Жалоба отправлена',
           'already_reported': 'Вы уже жаловались на это', 'moderation': 'Модерация',
           'pending_reports': 'Ожидают проверки', 'resolved_reports': 'Решённые', 'action': 'Действие',
           'delete_content': 'Удалить контент', 'dismiss': 'Отклонить', 'ban_user': 'Забанить пользователя',
           'banned': 'Забанен', 'unban': 'Разбанить', 'reason': 'Причина', 'date': 'Дата', 'reporter': 'Заявитель',
           'content': 'Содержимое', 'status': 'Статус', 'pending': 'Ожидает', 'resolved': 'Решено',
           'dismissed': 'Отклонено', 'all_reports': 'Все жалобы', 'banned_users': 'Забаненные пользователи',
           'unban_user': 'Разбанить', 'you_banned': 'Вы забанены и не можете совершать действия',
           'too_fast': 'Слишком быстро. Подождите немного', 'forbidden_words': 'Сообщение содержит запрещённые слова',
           'language': 'Язык', 'russian': 'Русский', 'english': 'English'},
    'en': {'feed': 'Feed', 'stats': 'Stats', 'bookmarks': 'Bookmarks', 'collections': 'Collections',
           'profile': 'Profile', 'logout': 'Logout', 'login': 'Login', 'register': 'Register', 'light': 'Light',
           'dark': 'Dark', 'install': 'Install', 'search': 'Search...', 'all': 'All', 'following': 'Following',
           'new_post': 'New post', 'title_placeholder': 'Title...', 'content_placeholder': "What's on your mind?",
           'topic_placeholder': 'Topic', 'image': 'Image', 'draft': 'Draft', 'publish': 'Publish',
           'no_posts': 'No posts yet. Be the first!', 'like': 'Like', 'bookmark': 'Bookmark',
           'bookmarked': 'Bookmarked', 'delete': 'Delete', 'comments': 'Comments', 'no_comments': 'No comments yet',
           'send': 'Send', 'anonymous': 'Anonymous', 'name_placeholder': 'Name', 'comment_placeholder': 'Comment...',
           'back': 'Back to feed', 'notifications': 'Notifications', 'no_notifications': 'No notifications',
           'achievements': 'Achievements', 'received': 'Received', 'from': 'of',
           'enter_to_post': 'Login to create posts', 'popular_tags': 'Popular tags:',
           'more_active': 'You are more active than', 'percent_users': '% of users', 'edit_profile': 'Edit profile',
           'posts': 'posts', 'followers': 'followers', 'followings': 'following', 'likes': 'likes',
           'days_streak': 'days in a row', 'record': 'record', 'activity_year': 'Activity over the year',
           'less': 'Less', 'more': 'More', 'posts_by_month': 'Posts by month', 'favorite_topics': 'Favorite topics',
           'user_posts': 'User posts', 'no_user_posts': 'User has no posts yet', 'report': 'Report',
           'report_post': 'Report post', 'report_comment': 'Report comment', 'report_reason': 'Reason', 'spam': 'Spam',
           'offensive': 'Offensive', 'illegal': 'Illegal content', 'other': 'Other', 'send_report': 'Send report',
           'report_sent': 'Report sent', 'already_reported': 'You already reported this', 'moderation': 'Moderation',
           'pending_reports': 'Pending', 'resolved_reports': 'Resolved', 'action': 'Action',
           'delete_content': 'Delete content', 'dismiss': 'Dismiss', 'ban_user': 'Ban user', 'banned': 'Banned',
           'unban': 'Unban', 'reason': 'Reason', 'date': 'Date', 'reporter': 'Reporter', 'content': 'Content',
           'status': 'Status', 'pending': 'Pending', 'resolved': 'Resolved', 'dismissed': 'Dismissed',
           'all_reports': 'All reports', 'banned_users': 'Banned users', 'unban_user': 'Unban',
           'you_banned': 'You are banned and cannot perform actions', 'too_fast': 'Too fast. Please wait a moment',
           'forbidden_words': 'Message contains forbidden words', 'language': 'Language', 'russian': 'Русский',
           'english': 'English'}
}


def get_lang():
    if current_user.is_authenticated and hasattr(current_user, 'lang'): return current_user.lang or 'ru'
    return session.get('lang', 'ru')


def t(key):
    lang = get_lang()
    return TRANSLATIONS.get(lang, TRANSLATIONS['ru']).get(key, key)


@app.context_processor
def inject_helpers():
    return {'format_msk': format_msk, 'format_msk_short': format_msk_short, 't': t, 'current_lang': get_lang(),
            'now_msk': now_msk}


@app.route('/set_lang/<lang>')
def set_lang(lang):
    if lang in ['ru', 'en']:
        session['lang'] = lang
        if current_user.is_authenticated:
            current_user.lang = lang
            db.session.commit()
    return redirect(request.referrer or url_for('index'))


# ═══════════════════════════════════════════════════════════
# АНТИСПАМ
# ══════════════════════════════════════════════════════════
FORBIDDEN_WORDS = ['мудак', 'сука', 'блять', 'хуй', 'fuck', 'shit', 'bitch']


def check_spam(user, action_type):
    if not user or not user.is_authenticated: return True, ''
    if user.is_banned: return False, t('you_banned')
    minute_ago = now_msk() - timedelta(minutes=1)
    recent = ActivityLog.query.filter(ActivityLog.user_id == user.id, ActivityLog.action_date >= minute_ago.date(),
                                      ActivityLog.action_type == action_type).count()
    limits = {'post': 5, 'comment': 15, 'like': 30}
    if recent > limits.get(action_type, 10): return False, t('too_fast')
    return True, ''


def check_forbidden_words(text):
    text_lower = text.lower()
    for word in FORBIDDEN_WORDS:
        if word in text_lower: return True, word
    return False, ''


# ═══════════════════════════════════════════════════════════
# AI-МОДЕРАЦИЯ
# ══════════════════════════════════════════════════════════
TOXIC_WORDS = ['мудак', 'сука', 'блять', 'хуй', 'пиздец', 'ебать', 'ублюдок', 'fuck', 'shit', 'bitch', 'asshole',
               'dick', 'bastard']


def ai_moderate(text, title=''):
    full_text = (title + ' ' + text).lower()
    score = 0;
    reasons = []
    toxic_found = [w for w in TOXIC_WORDS if w in full_text]
    if toxic_found:
        score += 40 * len(toxic_found)
        reasons.append(f'Токсичные слова: {", ".join(toxic_found[:3])}')
    if text and text.isupper() and len(text) > 10:
        score += 20;
        reasons.append('Весь текст КАПСОМ')
    if re.search(r'(.)\1{5,}', text):
        score += 15;
        reasons.append('Повторяющиеся символы')
    if len(re.findall(r'https?://\S+', text)) > 2:
        score += 25;
        reasons.append('Много ссылок')
    if len(text) < 3: score += 10; reasons.append('Слишком коротко')
    if len(text) > 5000: score += 15; reasons.append('Слишком длинно')
    score = min(100, score)
    return score < 50, '; '.join(reasons) if reasons else 'OK', score


# ═══════════════════════════════════════════════════════════
# TELEGRAM УВЕДОМЛЕНИЯ
# ══════════════════════════════════════════════════════════
try:
    import requests as http_requests

    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


def send_telegram_message(bot_token, chat_id, text):
    if not HAS_REQUESTS: return False
    try:
        url = f'https://api.telegram.org/bot{bot_token}/sendMessage'
        payload = {'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML', 'disable_web_page_preview': True}
        response = http_requests.post(url, json=payload, timeout=5)
        return response.json().get('ok', False)
    except Exception as e:
        print(f'Telegram error: {e}')
        return False


def notify_telegram(user, event_type, data):
    settings = TelegramSettings.query.filter_by(user_id=user.id, enabled=True).first()
    if not settings: return False
    if event_type == 'post' and not settings.notify_posts: return False
    if event_type == 'comment' and not settings.notify_comments: return False
    if event_type == 'like' and not settings.notify_likes: return False
    messages = {
        'post': f'📝 <b>Новый пост!</b>\n\n<b>{data.get("title", "")}</b>\n\n{data.get("content", "")[:200]}\n\n<a href="{data.get("url", "")}">Открыть →</a>',
        'comment': f'💬 <b>Новый комментарий</b> от {data.get("author", "Аноним")}:\n\n<i>{data.get("text", "")}</i>\n\n<a href="{data.get("url", "")}">Открыть →</a>',
        'like': f'❤️ <b>Новый лайк</b> на пост "{data.get("title", "")}"\n\n<a href="{data.get("url", "")}">Открыть →</a>',
        'follow': f'👥 <b>Новый подписчик</b>: {data.get("username", "")}',
        'report': f' <b>Новая жалоба</b>!\n\nПричина: {data.get("reason", "")}\n\n<a href="{data.get("url", "")}">Проверить →</a>',
    }
    text = messages.get(event_type, f' Уведомление: {data}')
    return send_telegram_message(settings.bot_token, settings.chat_id, text)


# ══════════════════════════════════════════════════════════
# ДОСТИЖЕНИЯ
# ═══════════════════════════════════════════════════════════
ACHIEVEMENTS_DATA = [
    {'name': 'Первые шаги', 'description': 'Опубликуй свой первый пост', 'icon': 'footprint', 'xp': 10, 'type': 'posts',
     'value': 1, 'rarity': 'common'},
    {'name': 'Активный автор', 'description': 'Напиши 10 постов', 'icon': 'edit_note', 'xp': 50, 'type': 'posts',
     'value': 10, 'rarity': 'rare'},
    {'name': 'Мастер пера', 'description': 'Напиши 50 постов', 'icon': 'edit', 'xp': 200, 'type': 'posts', 'value': 50,
     'rarity': 'epic'},
    {'name': 'Легенда блога', 'description': 'Напиши 100 постов', 'icon': 'workspace_premium', 'xp': 500,
     'type': 'posts', 'value': 100, 'rarity': 'legendary'},
    {'name': 'Любимчик', 'description': 'Набери 50 лайков', 'icon': 'favorite', 'xp': 30, 'type': 'likes', 'value': 50,
     'rarity': 'common'},
    {'name': 'Звезда', 'description': 'Набери 200 лайков', 'icon': 'star', 'xp': 100, 'type': 'likes', 'value': 200,
     'rarity': 'rare'},
    {'name': 'Неделя без пропусков', 'description': '7 дней активности подряд', 'icon': 'local_fire_department',
     'xp': 40, 'type': 'streak', 'value': 7, 'rarity': 'rare'},
    {'name': 'Месяц силы', 'description': '30 дней активности подряд', 'icon': 'fitness_center', 'xp': 150,
     'type': 'streak', 'value': 30, 'rarity': 'epic'},
    {'name': 'Социальная бабочка', 'description': 'Получи 10 подписчиков', 'icon': 'diversity_3', 'xp': 60,
     'type': 'followers', 'value': 10, 'rarity': 'rare'},
    {'name': 'Комментатор', 'description': 'Оставь 25 комментариев', 'icon': 'chat_bubble', 'xp': 40,
     'type': 'comments', 'value': 25, 'rarity': 'common'},
]


def init_achievements():
    if Achievement.query.count() == 0:
        for data in ACHIEVEMENTS_DATA:
            a = Achievement(name=data['name'], description=data['description'], icon=data['icon'], xp_reward=data['xp'],
                            condition_type=data['type'], condition_value=data['value'], rarity=data['rarity'])
            db.session.add(a)
        db.session.commit()


def check_and_award_achievements(user):
    init_achievements()
    new_achievements = []
    posts_count = Post.query.filter_by(author_id=user.id, is_draft=False).count()
    total_likes = db.session.query(func.sum(Post.likes)).filter(Post.author_id == user.id).scalar() or 0
    comments_count = Comment.query.join(Post).filter(Post.author_id == user.id).count()
    followers_count = user.followers.count()
    streak_count = user.streak.current_streak if user.streak else 0
    metrics = {'posts': posts_count, 'likes': total_likes, 'comments': comments_count, 'followers': followers_count,
               'streak': streak_count}
    for achievement in Achievement.query.all():
        if UserAchievement.query.filter_by(user_id=user.id, achievement_id=achievement.id).first(): continue
        current_value = metrics.get(achievement.condition_type, 0)
        if current_value >= achievement.condition_value:
            ua = UserAchievement(user_id=user.id, achievement_id=achievement.id)
            db.session.add(ua)
            user.xp += achievement.xp_reward
            update_user_level(user)
            create_notification(user.id, 'achievement', 'Система', f'🏆 Достижение: {achievement.name}!')
            new_achievements.append(achievement)
    if new_achievements: db.session.commit()
    return new_achievements


def update_user_level(user):
    levels = [(1, 0), (2, 50), (3, 200), (4, 500), (5, 1000)]
    new_level = 1
    for lvl, xp_req in levels:
        if user.xp >= xp_req: new_level = lvl
    if new_level != user.level:
        user.level = new_level
        create_notification(user.id, 'achievement', 'Система', f'🎉 Новый уровень: {new_level}!')


def log_activity(user, action_type):
    if not user or not user.is_authenticated: return
    today = now_msk().date()
    log = ActivityLog.query.filter_by(user_id=user.id, action_date=today, action_type=action_type).first()
    if log:
        log.count += 1
    else:
        log = ActivityLog(user_id=user.id, action_type=action_type, action_date=today, count=1)
        db.session.add(log)


def update_streak(user):
    if not user or not user.is_authenticated: return
    today = now_msk().date()
    yesterday = today - timedelta(days=1)
    streak = user.streak
    if not streak:
        streak = Streak(user_id=user.id, current_streak=1, longest_streak=1, last_active_date=today)
        db.session.add(streak)
        return
    if streak.last_active_date == today: return
    if streak.last_active_date == yesterday:
        streak.current_streak += 1
        if streak.current_streak > streak.longest_streak: streak.longest_streak = streak.current_streak
    else:
        streak.current_streak = 1
    streak.last_active_date = today


def get_heatmap_data(user):
    today = now_msk().date()
    start_date = today - timedelta(days=364)
    logs = ActivityLog.query.filter(ActivityLog.user_id == user.id, ActivityLog.action_date >= start_date).all()
    data = {}
    for log in logs:
        key = log.action_date.isoformat()
        data[key] = data.get(key, 0) + log.count
    return data


def get_percentile(user):
    total_users = User.query.count()
    if total_users <= 1: return 100
    user_posts = Post.query.filter_by(author_id=user.id, is_draft=False).count()
    users_with_fewer = User.query.join(Post).group_by(User.id).having(func.count(Post.id) < user_posts).count()
    return int((users_with_fewer / total_users) * 100)


# ═══════════════════════════════════════════════════════════
# ПОМОЩНИКИ
# ═══════════════════════════════════════════════════════════
def allowed_file(filename): return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config[
    'ALLOWED_EXTENSIONS']


def extract_tags(text): return re.findall(r'#(\w+)', text)


def render_markdown(text):
    html = markdown.markdown(text, extensions=['extra', 'codehilite'])
    allowed_tags = list(bleach.ALLOWED_TAGS) + ['p', 'br', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'pre', 'code', 'img',
                                                'ul', 'ol', 'li', 'strong', 'em', 'a', 'blockquote']
    allowed_attrs = bleach.ALLOWED_ATTRIBUTES if isinstance(bleach.ALLOWED_ATTRIBUTES, dict) else {
        'a': ['href', 'title'], 'img': ['src', 'alt', 'title']}
    return bleach.clean(html, tags=allowed_tags, attributes=allowed_attrs)


def can_delete_post(user, post): return user.is_authenticated and (user.is_admin or user.id == post.author_id)


def can_delete_comment(user, comment): return user.is_authenticated and user.is_admin


def create_notification(user_id, ntype, from_user, message, post_id=None):
    n = Notification(user_id=user_id, type=ntype, from_user=from_user, message=message, post_id=post_id)
    db.session.add(n);
    db.session.commit()


# ═══════════════════════════════════════════════════════════
# МИГРАЦИЯ БД
# ═══════════════════════════════════════════════════════════
def migrate_db():
    import sqlite3
    db_path = os.path.join(app.instance_path, 'microblog.db')
    if not os.path.exists(db_path):
        with app.app_context():
            db.create_all()
            init_achievements()
        return
    conn = sqlite3.connect(db_path);
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='user'")
    if not cursor.fetchone():
        conn.close();
        os.remove(db_path)
        with app.app_context():
            db.create_all()
            init_achievements()
        return
    cursor.execute("PRAGMA table_info(post)")
    columns = [row[1] for row in cursor.fetchall()]
    if 'author_id' not in columns:
        conn.close();
        os.remove(db_path)
        with app.app_context():
            db.create_all()
            init_achievements()
        return

    # Новые поля в user
    cursor.execute("PRAGMA table_info(user)")
    user_cols = [row[1] for row in cursor.fetchall()]
    for col, default in [('lang', "'ru'"), ('is_banned', '0'), ('theme', "'cosmic'"), ('accent_color', "'#8b2035'")]:
        if col not in user_cols:
            try:
                cursor.execute(f"ALTER TABLE user ADD COLUMN {col} DEFAULT {default}")
            except:
                pass

    # Новые поля в post
    cursor.execute("PRAGMA table_info(post)")
    post_cols = [row[1] for row in cursor.fetchall()]
    for col, default in [('image', "''"), ('is_draft', '0'), ('moderation_score', '0'), ('is_pinned', '0')]:
        if col not in post_cols:
            try:
                cursor.execute(f"ALTER TABLE post ADD COLUMN {col} DEFAULT {default}")
            except:
                pass

    conn.commit();
    conn.close()
    with app.app_context():
        db.create_all()
        init_achievements()


# ═══════════════════════════════════════════════════════════
# АВТОРИЗАЦИЯ (БЕЗ ВЕРИФИКАЦИИ)
# ═══════════════════════════════════════════════════════════
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()

        if not username or not password:
            flash('Заполните все поля', 'error')
            return redirect(url_for('register'))

        if User.query.filter_by(username=username).first():
            flash('Пользователь с таким именем уже существует', 'error')
            return redirect(url_for('register'))

        user = User(username=username)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        login_user(user)
        flash('Регистрация успешна!', 'success')
        return redirect(url_for('index'))

    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        user = User.query.filter_by(username=username).first()

        if user and user.check_password(password):
            login_user(user)
            flash('Вход выполнен!', 'success')
            return redirect(url_for('index'))
        flash('Неверное имя пользователя или пароль', 'error')
    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user();
    flash('Вы вышли из аккаунта', 'info')
    return redirect(url_for('index'))


@app.route('/make_admin/<username>')
def make_admin(username):
    if not app.debug:
        flash('Только в debug-режиме', 'error')
        return redirect(url_for('index'))
    user = User.query.filter_by(username=username).first()
    if user:
        user.is_admin = True;
        db.session.commit()
        flash(f'{username} теперь администратор!', 'success')
    return redirect(url_for('index'))


# ═══════════════════════════════════════════════════════════
# ПРОФИЛЬ
# ════════════════════════════════════════════════════════
@app.route('/user/<username>')
def user_profile(username):
    user = User.query.filter_by(username=username).first_or_404()
    posts = Post.query.filter_by(author_id=user.id, is_draft=False).order_by(Post.created_at.desc()).all()
    is_following = current_user.is_following(user) if current_user.is_authenticated else False
    followers_count = user.followers.count();
    following_count = user.following.count()
    level_info = user.get_level_info();
    heatmap_data = get_heatmap_data(user)
    percentile = get_percentile(user) if current_user.is_authenticated else 0
    total_likes = db.session.query(func.sum(Post.likes)).filter(Post.author_id == user.id).scalar() or 0
    total_comments = Comment.query.join(Post).filter(Post.author_id == user.id).count()
    monthly_stats_raw = db.session.query(func.strftime('%Y-%m', Post.created_at), func.count(Post.id)).filter(
        Post.author_id == user.id, Post.is_draft == False).group_by(func.strftime('%Y-%m', Post.created_at)).all()
    monthly_stats = [[str(row[0]), int(row[1])] for row in monthly_stats_raw]
    top_topics_raw = db.session.query(Post.topic, func.count(Post.id)).filter(Post.author_id == user.id,
                                                                              Post.is_draft == False).group_by(
        Post.topic).order_by(func.count(Post.id).desc()).limit(5).all()
    top_topics = [[str(row[0]), int(row[1])] for row in top_topics_raw]
    user_achievements = UserAchievement.query.filter_by(user_id=user.id).all()
    user_unlocked = set(ua.achievement_id for ua in user_achievements)
    all_achievements = Achievement.query.all()
    return render_template('profile.html', user=user, posts=posts, is_following=is_following,
                           followers_count=followers_count, following_count=following_count, level_info=level_info,
                           heatmap_data=heatmap_data, percentile=percentile, total_likes=total_likes,
                           total_comments=total_comments, monthly_stats=monthly_stats, top_topics=top_topics,
                           user_achievements=user_achievements, all_achievements=all_achievements,
                           user_unlocked=user_unlocked)


@app.route('/profile/edit', methods=['GET', 'POST'])
@login_required
def edit_profile():
    if request.method == 'POST':
        current_user.bio = request.form.get('bio', '')
        if 'avatar' in request.files:
            f = request.files['avatar']
            if f and allowed_file(f.filename):
                filename = f"{current_user.id}_{secure_filename(f.filename)}"
                f.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                current_user.avatar = filename
        if 'cover' in request.files:
            f = request.files['cover']
            if f and allowed_file(f.filename):
                filename = f"cover_{current_user.id}_{secure_filename(f.filename)}"
                f.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                current_user.cover = filename
        db.session.commit();
        flash('Профиль обновлён!', 'success')
        return redirect(url_for('user_profile', username=current_user.username))
    return render_template('edit_profile.html')


@app.route('/follow/<username>', methods=['POST'])
@login_required
def follow_user(username):
    user = User.query.filter_by(username=username).first_or_404()
    if user.id == current_user.id:
        flash('Нельзя подписаться на себя', 'error')
        return redirect(url_for('user_profile', username=username))
    existing = Follow.query.filter_by(follower_id=current_user.id, following_id=user.id).first()
    if existing:
        db.session.delete(existing);
        flash(f'Вы отписались от {username}', 'info')
    else:
        follow = Follow(follower_id=current_user.id, following_id=user.id)
        db.session.add(follow)
        create_notification(user.id, 'follow', current_user.username, f'{current_user.username} подписался на вас')
        notify_telegram(user, 'follow', {'username': current_user.username})
        flash(f'Вы подписались на {username}', 'success')
    db.session.commit();
    check_and_award_achievements(user)
    return redirect(url_for('user_profile', username=username))


# ═══════════════════════════════════════════════════════════
# ДОСТИЖЕНИЯ
# ══════════════════════════════════════════════════════════
@app.route('/achievements')
def achievements_page():
    all_achievements = Achievement.query.all()
    user_unlocked = set()
    if current_user.is_authenticated: user_unlocked = set(
        ua.achievement_id for ua in UserAchievement.query.filter_by(user_id=current_user.id).all())
    return render_template('achievements.html', achievements=all_achievements, user_unlocked=user_unlocked)


# ═══════════════════════════════════════════════════════════
# ОСНОВНЫЕ МАРШРУТЫ
# ══════════════════════════════════════════════════════════
@app.route('/')
def index():
    topic = request.args.get('topic', '')
    tag = request.args.get('tag', '')
    search = request.args.get('q', '')
    feed = request.args.get('feed', '')

    pinned_posts = Post.query.filter_by(is_pinned=True, is_draft=False).order_by(Post.created_at.desc()).all()

    query = Post.query.filter_by(is_draft=False, is_pinned=False)
    if topic: query = query.filter_by(topic=topic)
    if tag: query = query.join(Tag).filter(Tag.name == tag)
    if search: query = query.filter((Post.title.contains(search)) | (Post.content.contains(search)))
    if feed == 'following' and current_user.is_authenticated:
        following_ids = [f.following_id for f in current_user.following.all()]
        query = query.filter(Post.author_id.in_(following_ids + [current_user.id]))

    posts = query.order_by(func.random()).all()
    posts = pinned_posts + posts

    topics = [t[0] for t in db.session.query(Post.topic).distinct().all()]
    popular_tags = db.session.query(Tag.name, db.func.count(Tag.id)).group_by(Tag.name).order_by(
        db.func.count(Tag.id).desc()).limit(10).all()

    bookmark_ids = []
    liked_post_ids = []
    if current_user.is_authenticated:
        bookmark_ids = [b.post_id for b in current_user.bookmarks]
        liked_post_ids = [ul.post_id for ul in UserLike.query.filter_by(user_id=current_user.id).all()]

    return render_template('index.html', posts=posts, topics=topics, current_topic=topic,
                           current_tag=tag, search=search, popular_tags=popular_tags, feed=feed,
                           bookmark_ids=bookmark_ids, liked_post_ids=liked_post_ids)


@app.route('/post/<int:post_id>')
def post_page(post_id):
    post = Post.query.get_or_404(post_id)
    post.content_html = render_markdown(post.content)
    bookmark_ids = []
    liked_post_ids = []
    if current_user.is_authenticated:
        bookmark_ids = [b.post_id for b in current_user.bookmarks]
        liked_post_ids = [ul.post_id for ul in UserLike.query.filter_by(user_id=current_user.id).all()]
    voted_polls = set()
    if current_user.is_authenticated and post.poll:
        votes = PollVote.query.filter_by(poll_id=post.poll.id, user_id=current_user.id).all()
        voted_polls = {(v.poll_id, v.user_id) for v in votes}
    return render_template('post.html', post=post, bookmark_ids=bookmark_ids, liked_post_ids=liked_post_ids,
                           voted_polls=voted_polls)


@app.route('/add_post', methods=['GET', 'POST'])
@login_required
def add_post():
    if current_user.is_banned:
        flash(t('you_banned'), 'error');
        return redirect(url_for('index'))
    if request.method == 'POST':
        ok, msg = check_spam(current_user, 'post')
        if not ok: flash(msg, 'error'); return redirect(url_for('index'))
        title = request.form.get('title', '').strip()
        content = request.form.get('content', '').strip()
        topic = request.form.get('topic', 'Общее').strip()
        is_draft = 'draft' in request.form

        if not title or not content:
            flash('Заполните заголовок и содержимое', 'error')
            return redirect(url_for('index'))

        has_forbidden, word = check_forbidden_words(title + ' ' + content)
        if has_forbidden: flash(t('forbidden_words'), 'error'); return redirect(url_for('index'))

        is_safe, reason, score = ai_moderate(content, title)
        if not is_safe:
            flash(f'Пост не прошёл модерацию: {reason}', 'error')
            return redirect(url_for('index'))

        image = None
        if 'image' in request.files:
            f = request.files['image']
            if f and f.filename:
                filename = f"post_{int(now_msk().timestamp())}_{secure_filename(f.filename)}"
                f.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                image = filename

        post = Post(title=title, content=content, topic=topic if topic else 'Общее', author_id=current_user.id,
                    image=image, is_draft=is_draft, moderation_score=score)
        db.session.add(post);
        db.session.flush()
        for tag_name in extract_tags(content):
            tag = Tag(name=tag_name.lower(), post_id=post.id);
            db.session.add(tag)
        if not is_draft:
            log_activity(current_user, 'post');
            update_streak(current_user);
            check_and_award_achievements(current_user)
            for follower in current_user.followers.all():
                notify_telegram(follower.follower, 'post', {'title': title, 'content': content,
                                                            'url': url_for('post_page', post_id=post.id,
                                                                           _external=True)})
        db.session.commit()
        flash('Пост опубликован!' if not is_draft else 'Черновик сохранён', 'success')
        return redirect(url_for('post_page', post_id=post.id))
    return render_template('add_post.html')


@app.route('/like/<int:post_id>', methods=['POST'])
@login_required
def like_post(post_id):
    post = Post.query.get_or_404(post_id)

    existing_like = UserLike.query.filter_by(user_id=current_user.id, post_id=post_id).first()

    if existing_like:
        db.session.delete(existing_like)
        post.likes = max(0, post.likes - 1)
        db.session.commit()
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'likes': post.likes, 'liked': False})
        return redirect(request.referrer or url_for('index'))

    like = UserLike(user_id=current_user.id, post_id=post_id)
    db.session.add(like)
    post.likes += 1
    db.session.commit()

    if current_user.id != post.author_id:
        create_notification(post.author_id, 'like', current_user.username,
                            f'{current_user.username} лайкнул "{post.title}"', post.id)
        log_activity(current_user, 'like');
        update_streak(current_user)

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'likes': post.likes, 'liked': True})
    return redirect(request.referrer or url_for('index'))


@app.route('/add_comment/<int:post_id>', methods=['POST'])
def add_comment(post_id):
    post = Post.query.get_or_404(post_id);
    author_name = request.form.get('author', 'Аноним');
    text = request.form.get('text')
    if text:
        if current_user.is_authenticated:
            ok, msg = check_spam(current_user, 'comment')
            if not ok: flash(msg, 'error'); return redirect(url_for('post_page', post_id=post_id))
            has_forbidden, word = check_forbidden_words(text)
            if has_forbidden: flash(t('forbidden_words'), 'error'); return redirect(
                url_for('post_page', post_id=post_id))
            is_safe, reason, score = ai_moderate(text)
            if not is_safe:
                flash(f'Комментарий не прошёл модерацию: {reason}', 'error')
                return redirect(url_for('post_page', post_id=post_id))
        comment = Comment(author_name=author_name, text=text, post_id=post_id);
        db.session.add(comment)
        if current_user.is_authenticated:
            log_activity(current_user, 'comment');
            update_streak(current_user)
            if current_user.id != post.author_id:
                create_notification(post.author_id, 'comment', current_user.username or author_name,
                                    f'Новый комментарий к "{post.title}"', post.id)
                author = User.query.get(post.author_id)
                if author: notify_telegram(author, 'comment',
                                           {'author': current_user.username or author_name, 'text': text,
                                            'url': url_for('post_page', post_id=post_id, _external=True)})
        db.session.commit()
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest': return jsonify(
        {'id': comment.id, 'author': author_name, 'text': text, 'created_at': format_msk_short(now_msk())})
    return redirect(url_for('post_page', post_id=post_id))


@app.route('/delete/<int:post_id>', methods=['POST'])
@login_required
def delete_post(post_id):
    post = Post.query.get_or_404(post_id)
    if not can_delete_post(current_user, post):
        flash('Нет прав', 'error')
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest': return jsonify(
            {'status': 'error', 'message': 'Нет прав'}), 403
        return redirect(url_for('index'))
    db.session.delete(post);
    db.session.commit();
    flash('Пост удалён', 'success')
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest': return jsonify(
        {'status': 'deleted', 'post_id': post_id})
    return redirect(url_for('index'))


@app.route('/delete_comment/<int:comment_id>', methods=['POST'])
@login_required
def delete_comment(comment_id):
    comment = Comment.query.get_or_404(comment_id);
    post_id = comment.post_id
    if not can_delete_comment(current_user, comment):
        flash('Нет прав', 'error')
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest': return jsonify(
            {'status': 'error', 'message': 'Нет прав'}), 403
        return redirect(url_for('post_page', post_id=post_id))
    db.session.delete(comment);
    db.session.commit();
    flash('Комментарий удалён', 'success')
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest': return jsonify(
        {'status': 'deleted', 'comment_id': comment_id})
    return redirect(url_for('post_page', post_id=post_id))


# ═══════════════════════════════════════════════════════════
# ЗАКЛАДКИ
# ═══════════════════════════════════════════════════════════
@app.route('/bookmark/<int:post_id>', methods=['POST'])
@login_required
def toggle_bookmark(post_id):
    existing = Bookmark.query.filter_by(user_id=current_user.id, post_id=post_id).first()
    if existing:
        db.session.delete(existing);
        db.session.commit()
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest': return jsonify({'bookmarked': False})
        flash('Закладка удалена', 'info')
    else:
        b = Bookmark(user_id=current_user.id, post_id=post_id);
        db.session.add(b);
        db.session.commit()
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest': return jsonify({'bookmarked': True})
        flash('Добавлено в закладки', 'success')
    return redirect(request.referrer or url_for('index'))


@app.route('/bookmarks')
@login_required
def bookmarks():
    bookmarks_list = Bookmark.query.filter_by(user_id=current_user.id).order_by(Bookmark.created_at.desc()).all()
    posts = [b.post for b in bookmarks_list if b.post]
    return render_template('bookmarks.html', posts=posts)


# ═══════════════════════════════════════════════════════════
# УВЕДОМЛЕНИЯ
# ══════════════════════════════════════════════════════════
@app.route('/notifications')
@login_required
def notifications():
    notifs = Notification.query.filter_by(user_id=current_user.id).order_by(Notification.created_at.desc()).limit(
        30).all()
    for n in notifs: n.is_read = True
    db.session.commit()
    return render_template('notifications.html', notifs=notifs)


@app.route('/api/unread_count')
@login_required
def unread_count():
    count = Notification.query.filter_by(user_id=current_user.id, is_read=False).count()
    return jsonify({'count': count})


# ═══════════════════════════════════════════════════════════
# СТАТИСТИКА
# ═══════════════════════════════════════════════════════════
@app.route('/stats')
def stats():
    total_posts = Post.query.filter_by(is_draft=False).count();
    total_users = User.query.count();
    total_comments = Comment.query.count()
    top_posts = Post.query.filter_by(is_draft=False).order_by(Post.likes.desc()).limit(5).all()
    return render_template('stats.html', total_posts=total_posts, total_users=total_users,
                           total_comments=total_comments, top_posts=top_posts)


@app.route('/stats/detailed')
def detailed_stats():
    total_posts = Post.query.filter_by(is_draft=False).count()
    total_users = User.query.count()
    total_comments = Comment.query.count()
    total_likes = db.session.query(func.sum(Post.likes)).scalar() or 0
    top_authors = db.session.query(User.username, func.count(Post.id).label('post_count'),
                                   func.sum(Post.likes).label('like_count')).join(Post).filter(
        Post.is_draft == False).group_by(User.id).order_by(func.count(Post.id).desc()).limit(10).all()
    seven_days_ago = now_msk() - timedelta(days=7)
    hourly_activity = db.session.query(func.strftime('%w', Post.created_at).label('day'),
                                       func.strftime('%H', Post.created_at).label('hour'),
                                       func.count(Post.id).label('count')).filter(Post.created_at >= seven_days_ago,
                                                                                  Post.is_draft == False).group_by(
        'day', 'hour').all()
    top_topics = db.session.query(Post.topic, func.count(Post.id).label('count')).filter(
        Post.is_draft == False).group_by(Post.topic).order_by(func.count(Post.id).desc()).limit(10).all()
    return render_template('detailed_stats.html', total_posts=total_posts, total_users=total_users,
                           total_comments=total_comments, total_likes=total_likes, top_authors=top_authors,
                           hourly_activity=hourly_activity, top_topics=top_topics)


# ═══════════════════════════════════════════════════════════
# ОПРОСЫ
# ═══════════════════════════════════════════════════════════
@app.route('/create_poll/<int:post_id>', methods=['POST'])
@login_required
def create_poll(post_id):
    post = Post.query.get_or_404(post_id)
    if post.author_id != current_user.id and not current_user.is_admin:
        flash('Нет прав', 'error');
        return redirect(url_for('post_page', post_id=post_id))
    question = request.form.get('question', '').strip();
    options = [opt.strip() for opt in request.form.getlist('options[]') if opt.strip()]
    if not question or len(options) < 2:
        flash('Нужен вопрос и минимум 2 варианта', 'error');
        return redirect(url_for('post_page', post_id=post_id))
    poll = Poll(post_id=post_id, question=question);
    db.session.add(poll);
    db.session.flush()
    for opt_text in options:
        option = PollOption(poll_id=poll.id, text=opt_text);
        db.session.add(option)
    db.session.commit();
    flash('Опрос создан!', 'success')
    return redirect(url_for('post_page', post_id=post_id))


@app.route('/vote_poll/<int:option_id>', methods=['POST'])
@login_required
def vote_poll(option_id):
    option = PollOption.query.get_or_404(option_id);
    poll = option.poll
    existing = PollVote.query.filter_by(poll_id=poll.id, user_id=current_user.id).first()
    if existing:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest': return jsonify(
            {'status': 'error', 'message': 'already_voted'}), 409
        flash('Вы уже голосовали', 'error');
        return redirect(url_for('post_page', post_id=poll.post_id))
    option.votes += 1;
    vote = PollVote(poll_id=poll.id, option_id=option.id, user_id=current_user.id);
    db.session.add(vote);
    db.session.commit()
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest': return jsonify(
        {'status': 'ok', 'option_id': option.id, 'votes': option.votes, 'total': sum(o.votes for o in poll.options)})
    return redirect(url_for('post_page', post_id=poll.post_id))


@app.route('/delete_poll/<int:poll_id>', methods=['POST'])
@login_required
def delete_poll(poll_id):
    poll = Poll.query.get_or_404(poll_id);
    post = Post.query.get_or_404(poll.post_id)
    if post.author_id != current_user.id and not current_user.is_admin:
        flash('Нет прав', 'error');
        return redirect(url_for('post_page', post_id=poll.post_id))
    db.session.delete(poll);
    db.session.commit();
    flash('Опрос удалён', 'success')
    return redirect(url_for('post_page', post_id=poll.post_id))


# ══════════════════════════════════════════════════════════
# КОЛЛЕКЦИИ
# ═══════════════════════════════════════════════════════════
@app.route('/collections')
@login_required
def collections():
    my_collections = Collection.query.filter_by(user_id=current_user.id).order_by(Collection.created_at.desc()).all()
    public_collections = Collection.query.filter_by(is_public=True).order_by(Collection.created_at.desc()).limit(
        20).all()
    return render_template('collections.html', my_collections=my_collections, public_collections=public_collections)


@app.route('/collection/new', methods=['GET', 'POST'])
@login_required
def new_collection():
    if request.method == 'POST':
        name = request.form.get('name', '').strip();
        description = request.form.get('description', '').strip();
        is_public = 'public' in request.form
        if name:
            collection = Collection(user_id=current_user.id, name=name, description=description, is_public=is_public)
            db.session.add(collection);
            db.session.commit();
            flash('Коллекция создана!', 'success')
            return redirect(url_for('collection_view', collection_id=collection.id))
        flash('Нужно название', 'error')
    return render_template('new_collection.html')


@app.route('/collection/<int:collection_id>')
def collection_view(collection_id):
    collection = Collection.query.get_or_404(collection_id)
    if not collection.is_public and (not current_user.is_authenticated or current_user.id != collection.user_id):
        flash('Коллекция приватная', 'error');
        return redirect(url_for('index'))
    posts = [item.post for item in collection.items if item.post]
    return render_template('collection_view.html', collection=collection, posts=posts)


@app.route('/collection/<int:collection_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_collection(collection_id):
    collection = Collection.query.get_or_404(collection_id)
    if collection.user_id != current_user.id:
        flash('Нет прав', 'error');
        return redirect(url_for('collection_view', collection_id=collection_id))
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'update':
            collection.name = request.form.get('name', '').strip();
            collection.description = request.form.get('description', '').strip();
            collection.is_public = 'public' in request.form
            db.session.commit();
            flash('Коллекция обновлена', 'success')
        elif action == 'add_post':
            post_id = request.form.get('post_id')
            if post_id and not CollectionItem.query.filter_by(collection_id=collection.id, post_id=post_id).first():
                item = CollectionItem(collection_id=collection.id, post_id=post_id);
                db.session.add(item);
                db.session.commit();
                flash('Пост добавлен', 'success')
        elif action == 'remove_post':
            post_id = request.form.get('post_id')
            if post_id:
                item = CollectionItem.query.filter_by(collection_id=collection.id, post_id=post_id).first()
                if item: db.session.delete(item); db.session.commit(); flash('Пост удалён из коллекции', 'success')
        elif action == 'delete':
            db.session.delete(collection);
            db.session.commit();
            flash('Коллекция удалена', 'success');
            return redirect(url_for('collections'))
        return redirect(url_for('edit_collection', collection_id=collection_id))
    collection_posts = [item.post for item in collection.items if item.post]
    return render_template('edit_collection.html', collection=collection, posts=collection_posts)


# ═══════════════════════════════════════════════════════════
# RSS
# ═══════════════════════════════════════════════════════════
@app.route('/rss.xml')
def rss_feed():
    posts = Post.query.filter_by(is_draft=False).order_by(Post.created_at.desc()).limit(30).all()
    xml = ['<?xml version="1.0" encoding="UTF-8"?>', '<rss version="2.0">', '<channel>', '<title>Zenith Blog</title>',
           f'<link>{request.url_root.rstrip("/")}</link>', '<description>Личный дневник в сети</description>',
           '<language>ru</language>']
    for post in posts:
        xml.append('<item>')
        xml.append(f'<title>{post.title.replace("&", "&amp;").replace("<", "&lt;")}</title>')
        xml.append(f'<link>{url_for("post_page", post_id=post.id, _external=True)}</link>')
        xml.append(f'<description>{post.content[:500].replace("&", "&amp;").replace("<", "&lt;")}</description>')
        xml.append(f'<pubDate>{post.created_at.strftime("%a, %d %b %Y %H:%M:%S +0300")}</pubDate>')
        xml.append('</item>')
    xml.extend(['</channel>', '</rss>'])
    return '\n'.join(xml), 200, {'Content-Type': 'application/rss+xml; charset=utf-8'}


# ═══════════════════════════════════════════════════════════
# ЭКСПОРТ
# ═══════════════════════════════════════════════════════════
@app.route('/export/json')
@login_required
def export_json():
    posts = Post.query.filter_by(author_id=current_user.id).order_by(Post.created_at.desc()).all()
    data = [{'id': post.id, 'title': post.title, 'content': post.content, 'topic': post.topic, 'likes': post.likes,
             'created_at': format_msk_short(post.created_at), 'is_draft': post.is_draft,
             'tags': [t.name for t in post.tags],
             'comments': [{'author': c.author_name, 'text': c.text, 'date': format_msk_short(c.created_at)} for c in
                          post.comments]} for post in posts]
    return jsonify({'user': current_user.username, 'exported_at': format_msk_short(now_msk()), 'posts': data})


@app.route('/export/markdown')
@login_required
def export_markdown():
    posts = Post.query.filter_by(author_id=current_user.id).order_by(Post.created_at.desc()).all()
    md = [f'# Экспорт постов пользователя {current_user.username}', '', f'Дата: {format_msk(now_msk())}',
          f'Всего: {len(posts)}', '', '---', '']
    for post in posts:
        md.extend([f'## {post.title}', '',
                   f'**Тема:** {post.topic} | **Дата:** {format_msk(post.created_at)} | **Лайки:** {post.likes}', ''])
        if post.tags: md.extend([f'**Теги:** {", ".join("#" + t.name for t in post.tags)}', ''])
        md.extend([post.content, ''])
        if post.comments:
            md.extend(['### Комментарии', ''])
            for c in post.comments: md.append(f'- **{c.author_name}** ({format_msk(c.created_at)}): {c.text}')
            md.append('')
        md.extend(['---', ''])
    content = '\n'.join(md)
    return content, 200, {'Content-Type': 'text/plain; charset=utf-8',
                          'Content-Disposition': f'attachment; filename=zenith_{current_user.username}.md'}


# ═══════════════════════════════════════════════════════════
# ЖАЛОБЫ И МОДЕРАЦИЯ
# ══════════════════════════════════════════════════════════
@app.route('/report_post/<int:post_id>', methods=['GET', 'POST'])
@login_required
def report_post(post_id):
    post = Post.query.get_or_404(post_id)
    if post.author_id == current_user.id:
        flash('Нельзя пожаловаться на свой пост', 'error');
        return redirect(url_for('post_page', post_id=post_id))
    existing = Report.query.filter_by(reporter_id=current_user.id, post_id=post_id, status='pending').first()
    if existing: flash(t('already_reported'), 'error'); return redirect(url_for('post_page', post_id=post_id))
    if request.method == 'POST':
        reason = request.form.get('reason', 'other');
        description = request.form.get('description', '')
        report = Report(reporter_id=current_user.id, post_id=post_id, reason=reason, description=description)
        db.session.add(report);
        db.session.commit();
        flash(t('report_sent'), 'success')
        for admin in User.query.filter_by(is_admin=True).all():
            notify_telegram(admin, 'report',
                            {'reason': reason, 'url': url_for('post_page', post_id=post_id, _external=True)})
        return redirect(url_for('post_page', post_id=post_id))
    return render_template('report.html', post=post, comment=None)


@app.route('/report_comment/<int:comment_id>', methods=['GET', 'POST'])
@login_required
def report_comment(comment_id):
    comment = Comment.query.get_or_404(comment_id);
    post = Post.query.get_or_404(comment.post_id)
    existing = Report.query.filter_by(reporter_id=current_user.id, comment_id=comment_id, status='pending').first()
    if existing: flash(t('already_reported'), 'error'); return redirect(url_for('post_page', post_id=post.id))
    if request.method == 'POST':
        reason = request.form.get('reason', 'other');
        description = request.form.get('description', '')
        report = Report(reporter_id=current_user.id, comment_id=comment_id, post_id=post.id, reason=reason,
                        description=description)
        db.session.add(report);
        db.session.commit();
        flash(t('report_sent'), 'success')
        for admin in User.query.filter_by(is_admin=True).all():
            notify_telegram(admin, 'report',
                            {'reason': reason, 'url': url_for('post_page', post_id=post.id, _external=True)})
        return redirect(url_for('post_page', post_id=post.id))
    return render_template('report.html', post=post, comment=comment)


@app.route('/moderation')
@login_required
def moderation():
    if not current_user.is_admin: flash('Доступ запрещён', 'error'); return redirect(url_for('index'))
    pending = Report.query.filter_by(status='pending').order_by(Report.created_at.desc()).all()
    resolved = Report.query.filter(Report.status.in_(['resolved', 'dismissed'])).order_by(
        Report.resolved_at.desc()).limit(50).all()
    return render_template('moderation.html', pending=pending, resolved=resolved)


@app.route('/moderation/action/<int:report_id>', methods=['POST'])
@login_required
def moderation_action(report_id):
    if not current_user.is_admin: flash('Доступ запрещён', 'error'); return redirect(url_for('index'))
    report = Report.query.get_or_404(report_id);
    action = request.form.get('action')
    if action == 'delete_post' and report.post_id:
        post = Post.query.get(report.post_id)
        if post: db.session.delete(post)
    elif action == 'delete_comment' and report.comment_id:
        comment = Comment.query.get(report.comment_id)
        if comment: db.session.delete(comment)
    elif action == 'ban' and report.reporter_id:
        user = User.query.get(report.reporter_id)
        if user and not user.is_admin:
            user.is_banned = True;
            ban = Ban(user_id=user.id, banned_by=current_user.id, reason=f'По жалобе #{report.id}');
            db.session.add(ban)
    report.status = 'resolved' if action != 'dismiss' else 'dismissed'
    report.moderator_id = current_user.id;
    report.resolved_at = now_msk()
    db.session.commit();
    flash('Действие выполнено', 'success')
    return redirect(url_for('moderation'))


@app.route('/moderation/bans')
@login_required
def moderation_bans():
    if not current_user.is_admin: flash('Доступ запрещён', 'error'); return redirect(url_for('index'))
    bans = Ban.query.filter_by(is_active=True).order_by(Ban.created_at.desc()).all()
    return render_template('bans.html', bans=bans)


@app.route('/moderation/unban/<int:user_id>', methods=['POST'])
@login_required
def unban_user(user_id):
    if not current_user.is_admin: flash('Доступ запрещён', 'error'); return redirect(url_for('moderation_bans'))
    user = User.query.get_or_404(user_id);
    user.is_banned = False
    Ban.query.filter_by(user_id=user.id, is_active=True).update({'is_active': False})
    db.session.commit();
    flash(f'{user.username} разбанен', 'success')
    return redirect(url_for('moderation_bans'))


# ═══════════════════════════════════════════════════════════
# ТЕМА ОФОРМЛЕНИЯ
# ═══════════════════════════════════════════════════════════
AVAILABLE_THEMES = ['cosmic', 'neon', 'sunset', 'forest', 'ocean', 'minimal']
THEME_COLORS = {'cosmic': '#8b2035', 'neon': '#00ff88', 'sunset': '#ff6b35', 'forest': '#2d5016', 'ocean': '#0077b6',
                'minimal': '#555555'}


@app.route('/set_theme/<theme>')
@login_required
def set_theme(theme):
    if theme in AVAILABLE_THEMES:
        current_user.theme = theme
        db.session.commit()
        flash(f'Тема "{theme}" применена', 'success')
    return redirect(request.referrer or url_for('index'))


@app.route('/set_accent/<color>')
@login_required
def set_accent(color):
    if re.match(r'^#[0-9a-fA-F]{6}$', color):
        current_user.accent_color = color
        db.session.commit()
    return redirect(request.referrer or url_for('index'))


# ═══════════════════════════════════════════════════════════
# TELEGRAM НАСТРОЙКИ
# ═════════════════════════════════════════════════════════
@app.route('/telegram/settings', methods=['GET', 'POST'])
@login_required
def telegram_settings():
    settings = TelegramSettings.query.filter_by(user_id=current_user.id).first()
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'save':
            bot_token = request.form.get('bot_token', '').strip()
            chat_id = request.form.get('chat_id', '').strip()
            if not bot_token or not chat_id:
                flash('Заполните все поля', 'error')
                return redirect(url_for('telegram_settings'))
            if settings:
                settings.bot_token = bot_token;
                settings.chat_id = chat_id
                settings.notify_posts = 'notify_posts' in request.form
                settings.notify_comments = 'notify_comments' in request.form
                settings.notify_likes = 'notify_likes' in request.form
                settings.enabled = True
            else:
                settings = TelegramSettings(user_id=current_user.id, bot_token=bot_token, chat_id=chat_id,
                                            notify_posts='notify_posts' in request.form,
                                            notify_comments='notify_comments' in request.form,
                                            notify_likes='notify_likes' in request.form, enabled=True)
                db.session.add(settings)
            db.session.commit();
            flash('Настройки Telegram сохранены', 'success')
            if send_telegram_message(bot_token, chat_id, '✅ Zenith: Telegram-уведомления подключены!'):
                flash('Тестовое сообщение отправлено!', 'success')
            else:
                flash('Ошибка отправки. Проверьте токен и chat_id', 'error')
            return redirect(url_for('telegram_settings'))
        elif action == 'disable':
            if settings: settings.enabled = False; db.session.commit(); flash('Уведомления отключены', 'info')
            return redirect(url_for('telegram_settings'))
        elif action == 'delete':
            if settings: db.session.delete(settings); db.session.commit(); flash('Настройки удалены', 'info')
            return redirect(url_for('telegram_settings'))
    return render_template('telegram_settings.html', settings=settings)


@app.route('/telegram/test')
@login_required
def telegram_test():
    settings = TelegramSettings.query.filter_by(user_id=current_user.id).first()
    if not settings:
        flash('Сначала настройте Telegram', 'error')
        return redirect(url_for('telegram_settings'))
    if send_telegram_message(settings.bot_token, settings.chat_id,
                             f'🔔 Тест от Zenith\n\nВремя: {format_msk(now_msk())}\nПользователь: {current_user.username}'):
        flash('Тестовое сообщение отправлено!', 'success')
    else:
        flash('Ошибка отправки', 'error')
    return redirect(url_for('telegram_settings'))


# ═══════════════════════════════════════════════════════════
# КАЛЕНДАРЬ
# ══════════════════════════════════════════════════════════
@app.route('/calendar')
def calendar_view():
    year = request.args.get('year', now_msk().year, type=int)
    month = request.args.get('month', now_msk().month, type=int)
    start_date = datetime(year, month, 1, tzinfo=MSK)
    if month == 12:
        end_date = datetime(year + 1, 1, 1, tzinfo=MSK)
    else:
        end_date = datetime(year, month + 1, 1, tzinfo=MSK)
    posts = Post.query.filter(Post.created_at >= start_date, Post.created_at < end_date,
                              Post.is_draft == False).order_by(Post.created_at.asc()).all()
    posts_by_day = {}
    for post in posts:
        day = post.created_at.day
        if day not in posts_by_day: posts_by_day[day] = []
        posts_by_day[day].append(post)
    import calendar
    cal = calendar.monthcalendar(year, month)
    month_name = calendar.month_name[month]
    today = now_msk()
    return render_template('calendar.html', year=year, month=month, month_name=month_name, calendar=cal,
                           posts_by_day=posts_by_day, today=today)


# ═══════════════════════════════════════════════════════════
# РАСШИРЕННЫЙ ПОИСК
# ══════════════════════════════════════════════════════════
@app.route('/search')
def advanced_search():
    query = request.args.get('q', '')
    author = request.args.get('author', '')
    topic = request.args.get('topic', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    min_likes = request.args.get('min_likes', 0, type=int)
    posts = Post.query.filter_by(is_draft=False)
    if query: posts = posts.filter((Post.title.contains(query)) | (Post.content.contains(query)))
    if author: posts = posts.join(User).filter(User.username.contains(author))
    if topic: posts = posts.filter(Post.topic == topic)
    if date_from: posts = posts.filter(Post.created_at >= datetime.strptime(date_from, '%Y-%m-%d'))
    if date_to: posts = posts.filter(Post.created_at <= datetime.strptime(date_to, '%Y-%m-%d'))
    if min_likes > 0: posts = posts.filter(Post.likes >= min_likes)
    posts = posts.order_by(Post.created_at.desc()).all()
    topics = [t[0] for t in db.session.query(Post.topic).distinct().all()]
    return render_template('search.html', posts=posts, topics=topics, query=query, author=author, topic=topic,
                           date_from=date_from, date_to=date_to, min_likes=min_likes)


# ═══════════════════════════════════════════════════════════
# РЕЙТИНГ
# ════════════════════════════════════════════════════════
@app.route('/leaderboard')
def leaderboard():
    users = db.session.query(User.username, func.count(Post.id).label('post_count'),
                             func.sum(Post.likes).label('like_count'), User.xp.label('xp')).outerjoin(Post).filter(
        Post.is_draft == False).group_by(User.id).order_by(User.xp.desc()).limit(50).all()
    return render_template('leaderboard.html', users=users)


# ═══════════════════════════════════════════════════════════
# ЧЕЛЛЕНДЖИ
# ═════════════════════════════════════════════════════════
@app.route('/challenges')
@login_required
def challenges():
    active_challenges = Challenge.query.filter_by(is_active=True).all()
    user_progress = {}
    for challenge in active_challenges:
        progress = ChallengeProgress.query.filter_by(challenge_id=challenge.id, user_id=current_user.id).first()
        user_progress[challenge.id] = progress
    return render_template('challenges.html', challenges=active_challenges, user_progress=user_progress)


@app.route('/challenge/<int:challenge_id>/progress', methods=['POST'])
@login_required
def update_challenge_progress(challenge_id):
    challenge = Challenge.query.get_or_404(challenge_id)
    if not challenge.is_active:
        return jsonify({'status': 'error', 'message': 'Челлендж не активен'}), 400
    progress = ChallengeProgress.query.filter_by(challenge_id=challenge_id, user_id=current_user.id).first()
    if not progress:
        progress = ChallengeProgress(challenge_id=challenge_id, user_id=current_user.id, current_count=0)
        db.session.add(progress)
    progress.current_count += 1
    if progress.current_count >= challenge.target and not progress.completed:
        progress.completed = True
        progress.completed_at = now_msk()
        current_user.xp += challenge.reward_xp
        db.session.commit()
        create_notification(current_user.id, 'achievement', 'Система',
                            f'🎯 Челлендж выполнен: {challenge.title}! +{challenge.reward_xp} XP')
        return jsonify({'status': 'completed', 'xp': challenge.reward_xp})
    db.session.commit()
    return jsonify({'status': 'ok', 'count': progress.current_count, 'target': challenge.target})


# ═══════════════════════════════════════════════════════════
# API
# ══════════════════════════════════════════════════════════
@app.route('/api/posts')
def api_posts():
    topic = request.args.get('topic', '')
    posts = Post.query.filter_by(is_draft=False)
    if topic: posts = posts.filter_by(topic=topic)
    posts = posts.order_by(Post.created_at.desc()).all()
    return jsonify([p.to_dict() for p in posts])


@app.route('/uploads/<filename>')
def uploaded_file(filename): return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


@app.route('/manifest.webmanifest')
def manifest(): return send_from_directory('static', 'manifest.webmanifest', mimetype='application/manifest+json')


@app.route('/sw.js')
def service_worker(): return send_from_directory('static', 'sw.js', mimetype='application/javascript')


# ═══════════════════════════════════════════════════════════
if __name__ == '__main__':
    migrate_db()
    app.run(host='0.0.0.0', port=5000, debug=True)