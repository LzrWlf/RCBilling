from flask import Flask
from flask_login import LoginManager
from config import Config
import os

login_manager = LoginManager()
login_manager.login_view = 'auth.login'
login_manager.login_message = 'Please log in to access this page.'


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Ensure upload folder exists
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

    # Initialize database
    from app.models import db
    db.init_app(app)

    # Initialize login manager
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        from app.models import User
        return User.query.get(int(user_id))

    # Create tables
    with app.app_context():
        db.create_all()

        # Create default admin if not exists
        from app.models import User
        admin = User.query.filter_by(email='admin@myclinicsoftware.com').first()
        if not admin:
            admin = User(
                email='admin@myclinicsoftware.com',
                clinic_name='Admin',
                role='admin',
                is_active=True
            )
            admin.set_password('admin123')  # Change this!
            db.session.add(admin)
            db.session.commit()

    # Register blueprints
    from app.routes import main_bp
    from app.auth import auth_bp
    from app.admin import admin_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(admin_bp, url_prefix='/admin')

    return app
