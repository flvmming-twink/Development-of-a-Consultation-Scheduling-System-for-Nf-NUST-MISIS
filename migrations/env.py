from alembic import context
from dotenv import load_dotenv
from portal import create_app
from portal.models import db
load_dotenv()
app = create_app()
with app.app_context():
    if context.is_offline_mode():
        context.configure(url=app.config['SQLALCHEMY_DATABASE_URI'],target_metadata=db.metadata,literal_binds=True)
        with context.begin_transaction():
            context.run_migrations()
    else:
        with db.engine.connect() as connection:
            context.configure(connection=connection,target_metadata=db.metadata,compare_type=True)
            with context.begin_transaction():
                context.run_migrations()
