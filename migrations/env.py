from alembic import context
from dotenv import load_dotenv
from portal import create_app
from portal.models import db
def migrate():
    if context.is_offline_mode():
        context.configure(url=db.engine.url,target_metadata=db.metadata,literal_binds=True)
        with context.begin_transaction():
            context.run_migrations()
    elif context.config.attributes.get('connection') is not None:
        context.configure(connection=context.config.attributes['connection'], target_metadata=db.metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    else:
        with db.engine.connect() as connection:
            context.configure(connection=connection,target_metadata=db.metadata,compare_type=True)
            with context.begin_transaction():
                context.run_migrations()

if context.config.attributes.get('connection') is not None:
    migrate()
else:
    load_dotenv()
    app = create_app()
    with app.app_context():
        migrate()
